# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The life support's stretch against a hand over the vessels on its line.

One of the race files (see `test_races.py` for the family's method). Here the
contended rows are the oxygen standing on a hull's air line and the rows of
the crew that breathes it, and the order the two are taken in. Everybody who
acts takes their body first and the things after -- `_alive` is the prologue
of every command (D-211) -- so a sweep that will write a crew row takes the
crew before it touches anything the hull holds (`ship.belonging.lock_crew`,
`ship.fate._lose`). The hull's stretch took them the other way round: the
stacks on the line under `FOR UPDATE`, and the crew only once the line had
come up short.

* a crew member pours oxygen out of a vessel on the line in the same second
  the stretch comes up short: the pour holds the body and reaches for the
  stack, the stretch holds the stack and reaches for the body;
* the same pour while a member counting down breathes again: the crew row the
  stretch writes then is the grace given back, and writing it without taking
  the row is the same knot one door quieter;
* the system the line hangs on is unbolted while the stretch waits for a crew
  row: waiting is waiting for whoever held it, and the hold read before the
  wait no longer says what stands aboard;
* the line is emptied between the stretch's reading of it and its lock: what
  the stretch read covered the draw, so it did not take the crew -- and now it
  may neither wait for a body nor let the crew off the stretch;
* the same, with a second member holding their own row meanwhile: the busy one
  is passed over and left to the next stretch, the free one is settled now.

The handshake is `conftest._until_blocked_by`: the pour keeps its
transaction open and commits only once the stretch provably waits on it. On
the code the first race catches, the database finds the knot instead and kills
one of the two.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _until_blocked_by
from oxygen_kit import AIR, TANK, _hull, _in_tank, _plumb, _port, _sphere, _system
from src.api.commands.things import _ground_pick, _liquid_pour, _station_take
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import gear, liquid, oxygen, storage, travel, world
from src.engine.oxygen import breath
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Node, Planet

#: The stretch, in hours. A crew of one owes `oxygen.crew_draw` an hour of it,
#: and the two hulls below are stocked around that number: one under it, one
#: just over.
HOURS = 100.0
#: Units of oxygen the hand pours off the line into a tank beside it.
POURED = 2.0
#: What the mate picks off the floor while the stretch runs -- anything that
#: holds their row and is nothing to do with the line.
ORE = "iron_ore"


def _owed(constants: Constants) -> float:
    """What a crew of one owes for the stretch -- the number both hulls are stocked by."""
    return constants[R.OXYGEN_CREW_DRAW] * HOURS


async def _short_hull(
    session: AsyncSession, constants: Constants, *, air: float
) -> tuple[Body, Item, Item, Item]:
    """A sealed hull with `air` units on its line and an empty tank beside it
    to pour into. Returns its whole crew, the vessel on the line, the one off
    it and the life support the line hangs on."""
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, owner, connector = await _hull(session, constants, port)
    system = await _system(session, connector)
    tank = await _in_tank(session, connector, AIR, air)
    #: Off the line and empty: what the hand pours over goes somewhere the
    #: life support does not drink from, so none of it comes back to the crew.
    yard = await world.node_container(session, connector)
    spare = await world.grant_item(session, yard, TANK, quality=60, origin="test")
    await _plumb(session, system, tank)
    #: Cast off: a hull at a Terran pier breathes the planet's air (D-233).
    vessel.docked_node_id = None
    vessel.air_at = datetime.now(UTC) - timedelta(hours=HOURS)
    await session.flush()
    return owner, tank, spare, system


def _pouring(body: Body, source: Item, target: Item):
    """The player's own pour, as the socket runs it: `_alive` first, the
    vessels and the stacks in them after."""
    identity_id, source_id, target_id = body.identity_id, source.id, target.id

    async def pour(db: AsyncSession) -> float:
        answer = await _liquid_pour(
            {"identity_id": identity_id},
            db,
            {"from": str(source_id), "to": str(target_id), "goods": AIR, "amount": POURED},
        )
        return float(answer["poured"])

    return pour


async def _units(db: AsyncSession, vessel_id: uuid.UUID) -> float:
    """How much liquid stands in this vessel."""
    vessel = await db.get(Item, vessel_id)
    assert vessel is not None
    return sum(float(one.amount) for one in await storage.content(db, vessel)) / 1000


async def test_a_pour_off_the_line_while_the_hull_breathes_short_does_not_knot(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crew member pours the line's oxygen over while the stretch runs short.

    The pour holds its own row (`_alive`) and has not reached the vessels yet;
    the stretch walks in between. Taken the way the stretch took them, it held
    the stack on the line and waited for the body while the pour held the body
    and waited for the stack -- and the database untied that by killing one of
    the two, which the player is shown as a database error instead of an
    answer. Taken crew first, the stretch waits on a pour holding nothing it
    wants, and both land: what was poured over is gone from the line, and the
    crew chokes for what is left.
    """
    air = _owed(constants) / 2
    owner, tank, spare, _ = await _short_hull(session, constants, air=air)
    pouring = _pouring(owner, tank, spare)
    owner_id, tank_id, spare_id = owner.id, tank.id, spare.id
    await session.commit()

    stretches: list[asyncio.Future[tuple[float, int]]] = []
    waited: list[bool] = []
    reach = liquid.within_reach

    async def breathing() -> tuple[float, int]:
        async with factory() as db, db.begin():
            return await oxygen.tick_ships(db, constants, catalog)

    async def reaching(db: AsyncSession, *args, **kwargs) -> None:
        await reach(db, *args, **kwargs)
        if not stretches:
            stretches.append(asyncio.ensure_future(breathing()))
            waited.append(await _until_blocked_by(factory, db, unless=stretches[0]))

    monkeypatch.setattr(liquid, "within_reach", reaching)

    async def acting() -> float:
        async with factory() as db, db.begin():
            return await pouring(db)

    (poured,) = await asyncio.gather(acting(), return_exceptions=True)
    (stretch,) = await asyncio.gather(*stretches, return_exceptions=True)

    assert not isinstance(poured, BaseException), poured
    assert not isinstance(stretch, BaseException), stretch
    assert waited == [True], "the stretch did not wait for the pour"
    assert poured == pytest.approx(POURED)
    drawn, dead = stretch
    assert dead == 0, "the first short stretch only starts the countdown"
    assert drawn == pytest.approx(air - POURED), (
        "the stretch breathed what the pour left, not what it read before it"
    )
    async with factory() as db:
        body = await db.get(Body, owner_id)
        assert body is not None and body.choking_since is not None
        assert await _units(db, tank_id) == pytest.approx(0)
        assert await _units(db, spare_id) == pytest.approx(POURED)


async def test_the_grace_given_back_while_a_hand_pours_off_the_line_does_not_knot(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A member counting down breathes again while pouring off the line.

    The other half of the stretch's business with its crew: the tanks cover the
    draw, nobody dies, and the countdown of the stretch before is given back --
    a crew row written all the same. Written without the row being taken, it
    was an `UPDATE` waiting for whoever held the body, with the line's stacks
    already in hand: the same knot, one door quieter. So a member already
    counting down is reason enough to take the crew before the first vessel.
    """
    air = _owed(constants) * 2
    owner, tank, spare, _ = await _short_hull(session, constants, air=air)
    #: The stretch before ran the line dry: the countdown is on, and this one
    #: gives it back.
    owner.choking_since = datetime.now(UTC) - timedelta(hours=HOURS)
    pouring = _pouring(owner, tank, spare)
    owner_id = owner.id
    await session.commit()

    stretches: list[asyncio.Future[tuple[float, int]]] = []
    waited: list[bool] = []
    reach = liquid.within_reach

    async def breathing() -> tuple[float, int]:
        async with factory() as db, db.begin():
            return await oxygen.tick_ships(db, constants, catalog)

    async def reaching(db: AsyncSession, *args, **kwargs) -> None:
        await reach(db, *args, **kwargs)
        if not stretches:
            stretches.append(asyncio.ensure_future(breathing()))
            waited.append(await _until_blocked_by(factory, db, unless=stretches[0]))

    monkeypatch.setattr(liquid, "within_reach", reaching)

    async def acting() -> float:
        async with factory() as db, db.begin():
            return await pouring(db)

    (poured,) = await asyncio.gather(acting(), return_exceptions=True)
    (stretch,) = await asyncio.gather(*stretches, return_exceptions=True)

    assert not isinstance(poured, BaseException), poured
    assert not isinstance(stretch, BaseException), stretch
    assert waited == [True], "the stretch wrote a crew row without taking it"
    drawn, dead = stretch
    assert dead == 0 and drawn == pytest.approx(_owed(constants))
    async with factory() as db:
        body = await db.get(Body, owner_id)
        assert body is not None and body.choking_since is None, "the grace was not given back"


async def test_the_system_unbolted_while_the_crew_is_waited_for_leaves_nothing_to_breathe(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The life support is taken down while the stretch waits for a crew row.

    Waiting for a body is waiting for whoever holds it, and what they were
    doing was unbolting the one thing aboard that breathes for people (D-308).
    The hold was read before that wait and says the system stands; read again
    after it, it says the system lies on the floor -- and a hull without one
    breathes nothing, however full its tanks (D-288). By the reading taken
    before the wait the stretch drank a line that hung on nothing.
    """
    air = _owed(constants) / 2
    owner, tank, _, system = await _short_hull(session, constants, air=air)
    owner_id, tank_id, system_id = owner.id, tank.id, system.id
    identity_id = owner.identity_id
    await session.commit()

    stretches: list[asyncio.Future[tuple[float, int]]] = []
    waited: list[bool] = []
    here = travel.require_here

    async def breathing() -> tuple[float, int]:
        async with factory() as db, db.begin():
            return await oxygen.tick_ships(db, constants, catalog)

    async def standing(db: AsyncSession, *args, **kwargs) -> None:
        await here(db, *args, **kwargs)
        if not stretches:
            stretches.append(asyncio.ensure_future(breathing()))
            waited.append(await _until_blocked_by(factory, db, unless=stretches[0]))

    monkeypatch.setattr(travel, "require_here", standing)

    async def unbolting() -> str:
        async with factory() as db, db.begin():
            answer = await _station_take({"identity_id": identity_id}, db, {"item": str(system_id)})
            return str(answer["taken"])

    (taken,) = await asyncio.gather(unbolting(), return_exceptions=True)
    (stretch,) = await asyncio.gather(*stretches, return_exceptions=True)

    assert not isinstance(taken, BaseException), taken
    assert not isinstance(stretch, BaseException), stretch
    assert waited == [True], "the stretch did not wait for the hand"
    drawn, dead = stretch
    assert dead == 0
    assert drawn == pytest.approx(0), "the stretch drank a line hanging on nothing"
    async with factory() as db:
        assert await _units(db, tank_id) == pytest.approx(air)
        body = await db.get(Body, owner_id)
        assert body is not None and body.choking_since is not None


async def test_the_line_emptied_between_the_reading_and_the_lock_still_settles_the_crew(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The line covered the draw as the stretch read it and did not as it
    locked it: the crew is settled all the same, and not waited for.

    The stretch decides off a reading whether it will write a crew row, and a
    pour landing between that reading and the lock makes the reading wrong. It
    may not **wait** for a body now, with the line's stacks in its hands -- but
    it may not let the crew off either: a stretch bought by winning that race
    could be bought again every minute, and asphyxia would be a matter of
    timing. Whoever emptied the line has committed to have emptied it, so their
    row is free, and the countdown starts as it always did.
    """
    #: Just over the draw, so the reading covers it -- and under it once the
    #: hand has poured two units over.
    air = _owed(constants) + POURED / 2
    owner, tank, spare, _ = await _short_hull(session, constants, air=air)
    pouring = _pouring(owner, tank, spare)
    owner_id, tank_id = owner.id, tank.id
    await session.commit()

    poured: list[float] = []
    read = breath.breathable_stacks

    async def reading(*args, **kwargs) -> list[Item]:
        stacks = await read(*args, **kwargs)
        if not poured:
            async with factory() as db, db.begin():
                poured.append(await pouring(db))
        return stacks

    monkeypatch.setattr(breath, "breathable_stacks", reading)

    async with factory() as db, db.begin():
        drawn, dead = await oxygen.tick_ships(db, constants, catalog)

    assert poured == [pytest.approx(POURED)]
    assert dead == 0, "the first short stretch only starts the countdown"
    assert drawn == pytest.approx(air - POURED), "the stretch breathed what the pour left"
    async with factory() as db:
        body = await db.get(Body, owner_id)
        assert body is not None
        assert body.choking_since is not None, "a stretch on an empty line was given away"
        assert await _units(db, tank_id) == pytest.approx(0)


async def test_a_member_whose_row_is_busy_when_the_reading_turns_out_wrong_is_left_to_the_next(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two aboard: one empties the line between the reading and the lock, the
    other is holding their own row meanwhile.

    This is the knot in full -- the stretch has the line's stacks and a body it
    cannot have -- and it is the one the late taking must not wait for. The
    mate, busy picking a sack off the floor, is passed over and settled by the
    next stretch; the owner, whose pour has committed, is settled now. Waiting
    for either would be the deadlock; letting both off would be the stretch
    given away.
    """
    air = _owed(constants) * 2 + POURED / 2
    owner, tank, spare, _ = await _short_hull(session, constants, air=air)
    connector = await session.get(Node, owner.node_id)
    assert connector is not None
    mate = await world.print_body(
        session, await world.create_identity(session, f"Mate-{uuid.uuid4().hex[:6]}"), connector
    )
    #: A sack on the floor for the mate to pick up: the floor is open to a
    #: guest (D-204), so nothing asks whose hull this is.
    sack = await world.grant_item(
        session, await world.node_container(session, connector), ORE, amount=4, origin="test"
    )
    pouring = _pouring(owner, tank, spare)
    owner_id, mate_id, sack_id = owner.id, mate.id, sack.id
    mate_identity = mate.identity_id
    #: A crew of two owes twice the draw, and the line holds a little over it.
    await session.commit()

    stretches: list[asyncio.Future[tuple[float, int]]] = []
    waited: list[bool] = []
    poured: list[float] = []
    weigh = gear.check_carry_thing
    read = breath.breathable_stacks

    async def reading(*args, **kwargs) -> list[Item]:
        stacks = await read(*args, **kwargs)
        if not poured:
            #: The owner's pour lands and commits between the stretch's
            #: reading of the line and its lock on it: that is what makes the
            #: reading wrong.
            async with factory() as db, db.begin():
                poured.append(await pouring(db))
        return stacks

    async def breathing() -> tuple[float, int]:
        async with factory() as db, db.begin():
            return await oxygen.tick_ships(db, constants, catalog)

    async def weighing(db: AsyncSession, *args, **kwargs) -> None:
        await weigh(db, *args, **kwargs)
        if not stretches:
            stretches.append(asyncio.ensure_future(breathing()))
            #: The mate's row is held from here. The stretch must walk past it
            #: and finish -- `False` -- rather than come and wait on it.
            waited.append(await _until_blocked_by(factory, db, unless=stretches[0]))

    monkeypatch.setattr(gear, "check_carry_thing", weighing)
    monkeypatch.setattr(breath, "breathable_stacks", reading)

    async def picking() -> float:
        async with factory() as db, db.begin():
            answer = await _ground_pick({"identity_id": mate_identity}, db, {"item": str(sack_id)})
            return float(answer["picked"])

    (picked,) = await asyncio.gather(picking(), return_exceptions=True)
    (stretch,) = await asyncio.gather(*stretches, return_exceptions=True)

    assert not isinstance(picked, BaseException), picked
    assert not isinstance(stretch, BaseException), stretch
    assert waited == [False], "the stretch waited on a row it may not wait on"
    assert poured == [pytest.approx(POURED)]
    drawn, dead = stretch
    assert dead == 0
    assert drawn == pytest.approx(air - POURED), "the stretch breathed what the pour left"
    async with factory() as db:
        settled = await db.get(Body, owner_id)
        passed_over = await db.get(Body, mate_id)
        assert settled is not None and passed_over is not None
        assert settled.choking_since is not None, "the free row was not settled"
        assert passed_over.choking_since is None, "the busy row was waited for, not passed over"
