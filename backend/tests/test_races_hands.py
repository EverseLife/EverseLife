# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once putting matter into one pair of hands.

One of the race files (see `test_races.py` for the family's method): here the
contended thing is a body's carry limit (D-146), and the doors that weigh the
hands before they put into them. Every such door but one is the receiver's
own command, so the receiver's body row -- locked by `_alive` for the command
-- already queues them. The one that is not is a parcel from somebody else's
hands (`item.hand`): the giver acts, and the taker's row is nobody's to lock
unless the handover takes it.

* two parcels into one pair of hands, each fitting alone and not together;
* a parcel and the taker's own pick-up off the floor, whichever goes first;
* two parcels crossing -- each giver the other's taker -- which must both
  land: the handover takes two bodies' rows, and taken giver-first they wait
  on each other for ever;
* the daily wear of gear, one transaction over every body in the world,
  against a handover of a thing it has just worn -- the two hold rows of two
  bodies, and must take them in one order;
* a parcel the giver puts down while the handover waits, which must stay on
  the floor, or folds into a sack the giver picks up, which is no longer
  there to hand: the giver is found without a lock, so the thing is looked
  at before the wait and must be looked at again after it;
* and a parcel for a body standing somewhere else, whose row the handover
  must not take at all: the id comes off the wire.

The handshake is `conftest._until_blocked_by`: the side that went first
keeps its transaction open and commits only once the other side has provably
walked into one of its rows -- or, on the code the race exists to catch, once
the other side has walked straight through and finished.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow, _until_blocked_by
from src.api.commands.things import _ground_drop, _ground_pick, _item_hand
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import gear, storage, wear, world
from src.models.identity import Body
from src.models.inventory import Item
from src.units import amount_float

ORE = "iron_ore"
COAL = "coal"
#: Gear, so the daily step wears it (`wear._is_gear`), and nobody wears it
#: here, so it is handed over as it is (D-305).
BASKET = "basket"

#: Kilograms: of each parcel and of the sack on the floor, and of the room the
#: taker's hands keep beyond the load they carry -- more than one parcel, less
#: than two.
PARCEL_KG = 10.0
ROOM_KG = 15.0


@dataclass
class Room:
    """Three bodies on nobody's floor: a taker whose hands have room for one
    parcel, two givers holding one each, and a sack of ore lying between them."""

    taker: Body
    first: Body
    second: Body
    first_parcel: Item
    second_parcel: Item
    sack: Item


async def _room(session: AsyncSession, constants: Constants, catalog: Catalog) -> Room:
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.hands.{stamp}", "Clearing", area_m2=200)

    async def body(name: str) -> Body:
        return await world.print_body(
            session, await world.create_identity(session, f"{name}-{stamp}"), node
        )

    taker, first, second = await body("Taker"), await body("First"), await body("Second")
    per_ore = gear.mass_of(catalog, ORE, 1)

    async def parcel(container) -> Item:
        return await world.grant_item(
            session, container, ORE, amount=PARCEL_KG / per_ore, quality=60, origin="test"
        )

    #: The load is coal, not ore: a parcel of ore folding into it (D-214) would
    #: lock the taker's stack and queue the two sides on it -- a wait the race
    #: must not borrow from a coincidence of kinds.
    limit = await gear.capacity(session, constants, catalog, taker)
    await world.grant_item(
        session,
        await world.body_container(session, taker),
        COAL,
        amount=(limit - ROOM_KG) / gear.mass_of(catalog, COAL, 1),
        quality=60,
        origin="test",
    )
    room = Room(
        taker=taker,
        first=first,
        second=second,
        first_parcel=await parcel(await world.body_container(session, first)),
        second_parcel=await parcel(await world.body_container(session, second)),
        sack=await parcel(await world.node_container(session, node)),
    )
    await session.flush()
    return room


def _handing(giver: Body, taker: Body, parcel: Item) -> Callable[[AsyncSession], Awaitable[float]]:
    """The socket command `item.hand` of the whole parcel, as the giver sends it."""
    state = {"identity_id": giver.identity_id}
    message = {"item": str(parcel.id), "to": str(taker.id)}

    async def hand(db: AsyncSession) -> float:
        return (await _item_hand(state, db, message))["given"]

    return hand


def _picking(taker: Body, sack: Item) -> Callable[[AsyncSession], Awaitable[float]]:
    """The socket command `ground.pick` of the whole sack, as the taker sends it."""
    state = {"identity_id": taker.identity_id}
    message = {"item": str(sack.id)}

    async def pick(db: AsyncSession) -> float:
        return (await _ground_pick(state, db, message))["picked"]

    return pick


async def _first_holds(
    factory: async_sessionmaker[AsyncSession],
    first: Callable[[AsyncSession], Awaitable[float]],
    second: Callable[[AsyncSession], Awaitable[float]],
) -> tuple[float | BaseException, float | BaseException | None, bool]:
    """Run `first` to the end of its work, start `second`, and commit `first`
    only once `second` waits on it or has finished. Returns both answers and
    whether the second waited."""
    others: list[asyncio.Future[float]] = []
    waited: list[bool] = []

    async def after() -> float:
        async with factory() as db, db.begin():
            return await second(db)

    async def before() -> float:
        async with factory() as db, db.begin():
            done = await first(db)
            others.append(asyncio.ensure_future(after()))
            waited.append(await _until_blocked_by(factory, db, unless=others[0]))
            return done

    (went,) = await asyncio.gather(before(), return_exceptions=True)
    came = (await asyncio.gather(*others, return_exceptions=True) or [None])[0]
    return went, came, waited == [True]


async def _within_limit(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog, body_id
) -> None:
    async with factory() as db:
        body = await db.get(Body, body_id)
        assert body is not None
        carries = await gear.load_of(db, constants, catalog, body)
        limit = await gear.capacity(db, constants, catalog, body)
    assert carries <= limit + 1e-6, f"hands carry {carries} kg on a limit of {limit}"


async def test_two_parcels_into_one_pair_of_hands_leave_one_refused(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The second parcel is weighed against the hands the first one left.

    Each giver locks only their own body, and the taker's load is read, not
    taken: both read the same fifteen kilograms of room and both put ten into
    it. The handover takes the taker's row, so the second giver waits for the
    first parcel to land and then finds five.
    """
    room = await _room(session, constants, catalog)
    taker_id = room.taker.id
    per_ore = gear.mass_of(catalog, ORE, 1)
    await session.commit()

    went, came, waited = await _first_holds(
        factory,
        _handing(room.first, room.taker, room.first_parcel),
        _handing(room.second, room.taker, room.second_parcel),
    )

    await _within_limit(factory, constants, catalog, taker_id)
    assert went == pytest.approx(PARCEL_KG / per_ore), went
    assert isinstance(came, gear.Overloaded), came
    assert waited, "the second parcel did not wait for the first"


@pytest.mark.parametrize("first", ["hand", "pick"])
async def test_a_parcel_and_a_pick_up_into_one_pair_of_hands_leave_one_refused(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    first: str,
) -> None:
    """A parcel and the taker's own reach for the floor weigh the same hands.

    The pick-up is the taker's command and holds the taker's row; the parcel
    held only the giver's, so the two never met and each put its ten kilograms
    into the same fifteen. Whichever goes first, the other waits and is
    refused.
    """
    room = await _room(session, constants, catalog)
    taker_id, sack_id = room.taker.id, room.sack.id
    per_ore = gear.mass_of(catalog, ORE, 1)
    await session.commit()

    hand = _handing(room.first, room.taker, room.first_parcel)
    pick = _picking(room.taker, room.sack)
    went, came, waited = await _first_holds(
        factory, *((hand, pick) if first == "hand" else (pick, hand))
    )

    await _within_limit(factory, constants, catalog, taker_id)
    assert went == pytest.approx(PARCEL_KG / per_ore), went
    assert isinstance(came, gear.Overloaded), came
    assert waited, "the second door into the hands did not wait for the first"
    if first == "hand":
        async with factory() as db:
            sack = await db.get(Item, sack_id)
            assert sack is not None
            assert sack.container_id != (await world.body_container(db, room.taker)).id


async def test_a_parcel_put_down_while_the_handover_waits_stays_down(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A handover hands over what is in the giver's hands after the wait.

    The handover finds the giver without a lock and sees the parcel in their
    hands; the giver's own `ground.drop` holds their row and puts the parcel
    down. Judged by what it saw before the wait, the handover went on --
    and `world.move_stack` moves a row from wherever it lies now, so the
    taker got the sack off the floor.
    """
    room = await _room(session, constants, catalog)
    parcel_id, taker_id = room.first_parcel.id, room.taker.id
    await session.commit()

    state = {"identity_id": room.first.identity_id}

    async def drop(db: AsyncSession) -> float:
        return (await _ground_drop(state, db, {"item": str(parcel_id)}))["dropped"]

    went, came, waited = await _first_holds(
        factory, drop, _handing(room.first, room.taker, room.first_parcel)
    )

    assert went == pytest.approx(PARCEL_KG / gear.mass_of(catalog, ORE, 1)), went
    assert isinstance(came, storage.StorageError), came
    assert came.key == "storage-not-in-hands-to-hand", came.key
    assert waited, "the handover did not wait for the giver's own act"
    async with factory() as db:
        parcel = await db.get(Item, parcel_id)
        taker = await db.get(Body, taker_id)
        assert parcel is not None and taker is not None
        assert parcel.container_id != (await world.body_container(db, taker)).id


async def test_a_parcel_folded_away_while_the_handover_waits_is_gone(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A parcel that stopped existing while the handover waited is said so.

    The giver picks up the sack lying beside them, and the parcel in their
    hands is the same ore: it folds into the arriving sack and its row goes
    (D-214). The handover read the parcel before the wait; after it, the row
    is not there to reread, and that is the world's ordinary answer (D-011),
    not a failure of the server.
    """
    room = await _room(session, constants, catalog)
    sack_id = room.sack.id
    await session.commit()

    went, came, waited = await _first_holds(
        factory,
        _picking(room.first, room.sack),
        _handing(room.first, room.taker, room.first_parcel),
    )

    assert went == pytest.approx(PARCEL_KG / gear.mass_of(catalog, ORE, 1)), went
    assert isinstance(came, storage.StorageError), came
    assert came.key == "thing-gone", came.key
    assert waited, "the handover did not wait for the giver's own act"
    async with factory() as db:
        sack = await db.get(Item, sack_id)
        assert sack is not None
        assert amount_float(sack.amount) == pytest.approx(
            2 * PARCEL_KG / gear.mass_of(catalog, ORE, 1)
        )


async def test_two_parcels_crossing_both_land(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two people handing each other ore at the same moment both hand it over.

    Each handover holds two bodies' rows, and each giver is the other's taker.
    Taken giver first, the two take the same pair of rows in opposite orders
    and the database kills one of them; taken in id order, the second waits.
    The same knot tied itself before the taker's row was taken at all, one
    layer down: each parcel folds into the other's hands (D-214) and locks
    the stack the other is about to give. The pause after the weighing holds
    both givers on their own stack long enough for that to be certain.
    """
    room = await _room(session, constants, catalog)
    per_ore = gear.mass_of(catalog, ORE, 1)
    #: Two kinds of ore, told apart by quality: they lock each other's stacks
    #: as they fold, and do not merge, so each parcel is still there to give.
    room.second_parcel.quality = 40
    first_id, second_id = room.first.id, room.second.id
    await session.commit()

    _slow(monkeypatch, gear, "check_carry_thing")

    async def run(hand: Callable[[AsyncSession], Awaitable[float]]) -> float:
        async with factory() as db, db.begin():
            return await hand(db)

    given = await asyncio.gather(
        run(_handing(room.first, room.second, room.first_parcel)),
        run(_handing(room.second, room.first, room.second_parcel)),
        return_exceptions=True,
    )

    assert given == [pytest.approx(PARCEL_KG / per_ore)] * 2, given
    async with factory() as db:
        for owner, quality in ((first_id, 40), (second_id, 60)):
            body = await db.get(Body, owner)
            assert body is not None
            ore = [
                thing
                for thing in await world.contents(db, await world.body_container(db, body))
                if thing.type_key == ORE
            ]
            assert [(float(thing.quality), amount_float(thing.amount)) for thing in ore] == [
                (quality, pytest.approx(PARCEL_KG / per_ore))
            ]


async def test_daily_wear_and_a_handover_of_what_it_wore_both_land(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The day's wear takes the bodies it will need before it writes a thing.

    The walk goes body by body in id order. The giver comes first, and nothing
    of theirs ends today, so their basket's wear is written without their row;
    the taker comes second, and their basket falls apart, which takes the
    taker's row. A handover of the giver's basket to the taker between the
    two held both bodies' rows and waited for the basket -- and the walk,
    holding the basket, waited for the taker: the database killed one of
    them. Taken before the first write, the taker's row is the walk's
    already, and the handover waits for the day to end.
    """
    room = await _room(session, constants, catalog)
    giver, taker = sorted((room.first, room.second), key=lambda body: body.id)
    per_day = constants[R.WEAR_GEAR_PER_DAY]
    scale = constants[R.QUALITY_SCALE]
    basket = await world.grant_item(
        session, await world.body_container(session, giver), BASKET, quality=60, origin="test"
    )
    frayed = await world.grant_item(
        session, await world.body_container(session, taker), BASKET, quality=60, origin="test"
    )
    frayed.condition = Decimal(str(scale.min + 0.01))
    await session.flush()
    #: What the race needs of the vault, asserted rather than assumed: the
    #: giver's basket lives through the day and the taker's does not.
    assert not wear.wears_out(constants, basket, per_day)
    assert wear.wears_out(constants, frayed, per_day)
    basket_id, frayed_id, fresh = basket.id, frayed.id, float(basket.condition)
    await session.commit()

    handovers: list[asyncio.Future[float]] = []
    waited: list[bool] = []
    spent = wear.spend

    async def spending(db, constants_, item, *args, **kwargs):
        worn_out = await spent(db, constants_, item, *args, **kwargs)
        if item is not None and item.id == basket_id and not handovers:

            async def hand() -> float:
                async with factory() as other, other.begin():
                    return await _handing(giver, taker, basket)(other)

            handovers.append(asyncio.ensure_future(hand()))
            waited.append(await _until_blocked_by(factory, db, unless=handovers[0]))
        return worn_out

    monkeypatch.setattr(wear, "spend", spending)

    async def walk() -> int:
        async with factory() as db, db.begin():
            return await wear.daily_gear_wear(db, constants, catalog)

    (gone,) = await asyncio.gather(walk(), return_exceptions=True)
    (given,) = await asyncio.gather(*handovers, return_exceptions=True)

    assert gone == 1, gone
    assert given == pytest.approx(1), given
    assert waited == [True], "the handover did not wait for the day's wear"
    async with factory() as db:
        assert await db.get(Item, frayed_id) is None
        handed = await db.get(Item, basket_id)
        assert handed is not None
        assert handed.container_id == (await world.body_container(db, taker)).id
        #: Worn by the day, and handed over worn: the two both happened.
        assert float(handed.condition) < fresh


async def test_a_parcel_for_a_body_elsewhere_does_not_take_its_row(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A handover to somebody in another city is refused without touching them.

    The taker is named by an id off the wire. Locking it before asking where
    the body stands would let any client queue a stranger's every command --
    and a tick's -- behind handovers that were never going to happen. The
    taker's row is held by another transaction here; the refusal must come
    without waiting for it.
    """
    room = await _room(session, constants, catalog)
    stamp = uuid.uuid4().hex[:8]
    far = await world.create_node(session, f"terra.far.{stamp}", "Far", area_m2=200)
    room.taker.node_id = far.id
    taker_id = room.taker.id
    await session.commit()

    async def hand() -> float:
        async with factory() as db, db.begin():
            return await _handing(room.first, room.taker, room.first_parcel)(db)

    async with factory() as holder, holder.begin():
        await holder.execute(select(Body.id).where(Body.id == taker_id).with_for_update())
        handing = asyncio.ensure_future(hand())
        waited = await _until_blocked_by(factory, holder, unless=handing)
    (refused,) = await asyncio.gather(handing, return_exceptions=True)

    assert not waited, "the handover waited on the row of a body in another city"
    assert isinstance(refused, storage.StorageError), refused
    assert refused.key == "storage-person-not-here", refused.key
