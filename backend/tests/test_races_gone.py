# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two transactions at once over a thing that went, or changed, while a hand reached.

One of the race files (see `test_races.py` for the family's method): here the
contended thing is a thing lying on a floor anybody inside may pick up from
(D-204) -- a sack, a chest, a canister on nobody's land (D-198), a machine on
the floor of a house -- and the questions each door asks of it: where it lies,
whether it stands and what it weighs. They are asked of the row **after** its
lock, and the races come in pairs of who goes first:

* a fill goes first -- the lift weighs the canister as the fill left it
  (D-230, D-313), or the hands walk off past their limit (D-146);
* a lift goes first -- a pour, an output settling, a second hand reaching
  must find the thing gone from the floor, not follow it into the lifter's
  hands;
* a fire goes first (`plates._burn`) -- a pour or an output into a canister
  burnt with its yard must find it gone, not open a new inside for a thing
  that no longer exists and pour the liquid out of the world unsaid;
* a put-up goes first (`station.place`) -- a guest's lift must find the
  machine standing, not unbolt it into the guest's hands past the one door
  that asks whose the place is (D-278, D-308).

The handshake is `automat_kit._until_blocked_by`: the side that went first
keeps its transaction open, holding the contended row, and commits only once
the other side has provably walked into it.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from automat_kit import _until_blocked_by
from gone_kit import _burning, _lifting
from src.constants import Catalog, Constants
from src.engine import gear, liquid, station, storage, world
from src.models.estate import Building
from src.models.identity import Body
from src.models.inventory import Container, Item
from src.models.world import Layer, Node
from src.units import amount_float

ORE = "iron_ore"
WATER = "water"
CANISTER = "canister"
CHEST = "chest"
#: A machine a pair of hands can lift, and one put up from the floor (D-278).
BENCH = "workbench"

#: Kilograms: of water in the canister on the floor, of water the fill brings,
#: and of room the lifter's hands keep for the canister beyond what it holds
#: before the fill -- less than the fill, so the fill decides the lift.
INSIDE_KG = 2.0
POURED_KG = 15.0
SLACK_KG = 5.0
#: Kilograms of ore two hands reach for at once: light enough for either.
SACK_KG = 3.0


async def _clearing(session: AsyncSession, constants: Constants, catalog: Catalog):
    """A canister on nobody's floor, a lifter beside it and a filler with water.

    The lifter's hands are loaded so that the canister as it lies fits and the
    canister with the fill in it does not. The filler holds a second canister
    with the water to pour. Returns the node, the lifter, the filler, the
    canister on the floor and the filler's canister.
    """
    per_ore = gear.mass_of(catalog, ORE, 1)
    per_water = gear.mass_of(catalog, WATER, 1)
    limit = storage.capacity(catalog, CANISTER)
    assert limit is not None
    #: What the race needs of the vault, asserted rather than assumed: the
    #: fill goes into the canister whole, and water is a liquid (D-230).
    assert limit >= INSIDE_KG + POURED_KG
    assert catalog.recipes.is_liquid(WATER)

    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.gone.{stamp}", "Clearing", area_m2=200)
    lifter = await world.print_body(
        session, await world.create_identity(session, f"Lifter-{stamp}"), node
    )
    filler = await world.print_body(
        session, await world.create_identity(session, f"Filler-{stamp}"), node
    )

    yard = await world.node_container(session, node)
    canister = await world.grant_item(session, yard, CANISTER, quality=60, origin="test")
    await world.grant_item(
        session,
        await storage.inside(session, canister),
        WATER,
        amount=INSIDE_KG / per_water,
        quality=60,
        origin="test",
    )
    source = await world.grant_item(
        session, await world.body_container(session, filler), CANISTER, quality=60, origin="test"
    )
    await world.grant_item(
        session,
        await storage.inside(session, source),
        WATER,
        amount=POURED_KG / per_water,
        quality=60,
        origin="test",
    )

    carried = gear.mass_of(catalog, CANISTER, 1) + INSIDE_KG + SLACK_KG
    room = await gear.capacity(session, constants, catalog, lifter)
    await world.grant_item(
        session,
        await world.body_container(session, lifter),
        ORE,
        amount=(room - carried) / per_ore,
        origin="test",
    )
    await session.flush()
    return node, lifter, filler, canister, source


async def _two_on_the_floor(session: AsyncSession, constants: Constants, catalog: Catalog):
    """The clearing with an empty canister standing beside the one to be lifted.

    The lifted one comes first in the order an output pours in (`vessels_in`,
    by id) -- the other way round the spare takes the whole output and the
    lifted one is never reached -- so the water goes into whichever of the two
    that is. Returns the node, the lifter, the canister to lift and the one
    that stays.
    """
    node, lifter, _, canister, _ = await _clearing(session, constants, catalog)
    yard = await world.node_container(session, node)
    spare = await world.grant_item(session, yard, CANISTER, quality=60, origin="test")
    first, second = sorted((canister, spare), key=lambda can: can.id)
    if first.id != canister.id:
        water = (await world.contents(session, await storage.inside(session, canister)))[0]
        await world.move_stack(
            session, water, await storage.inside(session, first), amount_float(water.amount)
        )
    await session.flush()
    return node, lifter, first, second


async def _two_hands(session: AsyncSession, catalog: Catalog):
    """Two empty-handed bodies on nobody's floor and a sack of ore between them.

    Returns the node, the two bodies and the sack.
    """
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.gone.{stamp}", "Clearing", area_m2=200)
    first = await world.print_body(
        session, await world.create_identity(session, f"First-{stamp}"), node
    )
    second = await world.print_body(
        session, await world.create_identity(session, f"Second-{stamp}"), node
    )
    sack = await world.grant_item(
        session,
        await world.node_container(session, node),
        ORE,
        amount=SACK_KG / gear.mass_of(catalog, ORE, 1),
        quality=60,
        origin="test",
    )
    return node, first, second, sack


async def _within_limit(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog, body_id
) -> None:
    async with factory() as db:
        body = await db.get(Body, body_id)
        assert body is not None
        carries = await gear.load_of(db, constants, catalog, body)
        limit = await gear.capacity(db, constants, catalog, body)
    assert carries <= limit + 1e-6, f"hands carry {carries} kg on a limit of {limit}"


async def _water_in(factory: async_sessionmaker[AsyncSession], vessel_id) -> float:
    async with factory() as db:
        vessel = await db.get(Item, vessel_id)
        assert vessel is not None
        return sum(
            amount_float(thing.amount)
            for thing in await storage.content(db, vessel)
            if thing.type_key == WATER
        )


async def _insides_of(factory: async_sessionmaker[AsyncSession], owner_id) -> list[Container]:
    """Every inside that names this thing as its owner, the thing gone or not."""
    async with factory() as db:
        return list(
            (await db.execute(select(Container).where(Container.owner_id == owner_id)))
            .scalars()
            .all()
        )


async def test_a_canister_filled_before_the_lift_is_weighed_with_the_fill(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The lift weighs what the fill left in the canister, not what lay in it before.

    The filler empties a canister into the one on the floor and keeps the
    transaction open, holding the canister's row. The lift reads the canister
    as it lay -- two kilograms -- and walks into the row. The fill commits.
    Weighed off what it read before the wait, the lift passed and the hands
    walked off fifteen kilograms past their limit; weighed after it, the
    canister is too heavy and stays on the floor.
    """
    node, lifter, filler, canister, source = await _clearing(session, constants, catalog)
    lifter_id, filler_id, canister_id, source_id = lifter.id, filler.id, canister.id, source.id
    node_id = node.id
    per_water = gear.mass_of(catalog, WATER, 1)
    await session.commit()

    held = asyncio.Event()

    async def fill() -> float:
        async with factory() as db, db.begin():
            me = await db.get(Body, filler_id)
            assert me is not None
            _, poured = await liquid.pour(
                db,
                constants,
                catalog,
                me,
                await db.get(Item, source_id),
                await db.get(Item, canister_id),
            )
            held.set()
            await _until_blocked_by(factory, db)
            return poured

    async def lift() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            me = await db.get(Body, lifter_id)
            thing = await db.get(Item, canister_id)
            assert me is not None and thing is not None
            return await storage.pick(db, constants, catalog, me, thing)

    poured, lifted = await asyncio.gather(fill(), lift(), return_exceptions=True)

    assert poured == pytest.approx(POURED_KG / per_water), poured
    assert isinstance(lifted, gear.Overloaded), lifted
    await _within_limit(factory, constants, catalog, lifter_id)
    async with factory() as db:
        thing = await db.get(Item, canister_id)
        spot = await db.get(Node, node_id)
        assert thing is not None and spot is not None
        assert thing.container_id == (await world.node_container(db, spot)).id


async def test_a_canister_lifted_before_the_pour_is_not_poured_into_in_the_hands(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A hand pours into a vessel where it is after the lock, not where it lay before.

    The lifter picks the canister up and keeps the transaction open, holding
    its row. The filler sees the canister still on the floor -- within reach,
    and nobody's land is anybody's to pour on (D-198) -- and walks into the
    row. The lift commits. Asked where the canister is before the wait, the
    pour went on into a canister now in the lifter's hands: fifteen kilograms
    past their limit, poured by a stranger past the check a pour into the
    hands makes. Asked after it, the canister is no longer here to pour into.
    """
    _, lifter, filler, canister, source = await _clearing(session, constants, catalog)
    lifter_id, filler_id, canister_id, source_id = lifter.id, filler.id, canister.id, source.id
    per_water = gear.mass_of(catalog, WATER, 1)
    await session.commit()

    held = asyncio.Event()

    async def fill() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            me = await db.get(Body, filler_id)
            assert me is not None
            _, poured = await liquid.pour(
                db,
                constants,
                catalog,
                me,
                await db.get(Item, source_id),
                await db.get(Item, canister_id),
            )
            return poured

    lifted, poured = await asyncio.gather(
        _lifting(factory, constants, catalog, lifter_id, canister_id, held=held),
        fill(),
        return_exceptions=True,
    )

    assert lifted == pytest.approx(1), lifted
    assert isinstance(poured, liquid.LiquidError), poured
    assert poured.key == "liquid-vessel-not-here", poured.key
    await _within_limit(factory, constants, catalog, lifter_id)
    assert await _water_in(factory, canister_id) == pytest.approx(INSIDE_KG / per_water)
    assert await _water_in(factory, source_id) == pytest.approx(POURED_KG / per_water)


async def test_an_output_does_not_settle_into_a_canister_lifted_off_the_floor(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A liquid output settles into the vessels still standing here after the lock.

    A batch's water lands in the yard and settles into the vessels standing
    there (`liquid.settle`, the door of every output -- a craft, an automat,
    a rig). The lifter has just picked up the first of two canisters in the
    pouring order and holds its row; the output reads the yard before the
    lift commits and walks into that row. Poured where it was read, the
    output went into the lifter's hands -- past their limit, and somebody
    else's product in them. After the lock the lifted canister is passed
    over and the output goes into the one still standing, none of it spilled.
    """
    node, lifter, lifted, standing = await _two_on_the_floor(session, constants, catalog)
    lifter_id, lifted_id, standing_id, node_id = lifter.id, lifted.id, standing.id, node.id
    per_water = gear.mass_of(catalog, WATER, 1)
    await session.commit()

    held = asyncio.Event()

    async def output() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            spot = await db.get(Node, node_id)
            assert spot is not None
            floor = await world.node_container(db, spot)
            made = await world.grant_item(
                db, floor, WATER, amount=POURED_KG / per_water, quality=60, origin="test"
            )
            return await liquid.settle(db, catalog, made, (floor,))

    taken, spilled = await asyncio.gather(
        _lifting(factory, constants, catalog, lifter_id, lifted_id, held=held),
        output(),
        return_exceptions=True,
    )

    assert taken == pytest.approx(1), taken
    assert spilled == pytest.approx(0), spilled
    await _within_limit(factory, constants, catalog, lifter_id)
    assert await _water_in(factory, lifted_id) == pytest.approx(INSIDE_KG / per_water)
    assert await _water_in(factory, standing_id) == pytest.approx(POURED_KG / per_water)


async def test_the_room_for_a_find_does_not_count_a_canister_lifted_off_the_floor(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The room promised beforehand is the room the pour then finds.

    A find that is liquid is asked about before it is conjured
    (`liquid.room_for`, under the lock the pour takes): refused, it must stay
    a find rather than be made and spilled. Asked while the lifter holds the
    first canister, the question walks into that row. Answered where the
    canister was read, the room counted a canister now in the lifter's
    hands -- room `liquid.fill` passes over, so the answer and the pour it
    promises disagree. After the lock only the canister still standing counts.
    """
    node, lifter, lifted, _ = await _two_on_the_floor(session, constants, catalog)
    lifter_id, lifted_id, node_id = lifter.id, lifted.id, node.id
    per_water = gear.mass_of(catalog, WATER, 1)
    empty = storage.capacity(catalog, CANISTER)
    assert empty is not None
    await session.commit()

    held = asyncio.Event()

    async def ask() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            spot = await db.get(Node, node_id)
            assert spot is not None
            return await liquid.room_for(db, catalog, await world.node_container(db, spot), WATER)

    taken, room = await asyncio.gather(
        _lifting(factory, constants, catalog, lifter_id, lifted_id, held=held),
        ask(),
        return_exceptions=True,
    )

    assert taken == pytest.approx(1), taken
    assert room == pytest.approx(empty / per_water), room


async def test_two_hands_reaching_for_one_sack_do_not_take_it_out_of_each_other(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """What is picked up off the floor comes off the floor, not out of a pair of hands.

    Two bodies see one sack lying on the floor. The first picks it up and
    keeps the transaction open, holding its row; the second, having seen the
    sack where it lay, walks into the row. The first commits. Judged by the
    sight from before the wait, the second pick went on, and the move -- which
    rereads the row -- took the sack from wherever it now was: out of the
    first body's hands. Judged after the wait, the sack is no longer here.
    """
    _, first, second, sack = await _two_hands(session, catalog)
    first_id, second_id, sack_id = first.id, second.id, sack.id
    units = amount_float(sack.amount)
    await session.commit()

    held = asyncio.Event()

    async def reach() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            me = await db.get(Body, second_id)
            thing = await db.get(Item, sack_id)
            assert me is not None and thing is not None
            return await storage.pick(db, constants, catalog, me, thing)

    taken, reached = await asyncio.gather(
        _lifting(factory, constants, catalog, first_id, sack_id, held=held),
        reach(),
        return_exceptions=True,
    )

    assert taken == pytest.approx(units), taken
    assert isinstance(reached, storage.StorageError), reached
    assert reached.key == "storage-not-on-ground", reached.key
    async with factory() as db:
        thing = await db.get(Item, sack_id)
        me = await db.get(Body, first_id)
        assert thing is not None and me is not None
        assert thing.container_id == (await world.body_container(db, me)).id


async def test_two_hands_reaching_into_one_chest_do_not_take_out_of_each_other(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """What is taken out of a chest comes out of the chest (D-181).

    The chest's row queues the two hands -- each door takes it before a word
    is read off it -- but the thing inside is read before that queue. The
    first takes the sack out and holds its transaction; the second, having
    seen the sack in the chest, waits on the chest. The first commits. The
    second, let into the chest, judged the sack by the sight from before the
    wait and took it out of the first body's hands.
    """
    node, first, second, sack = await _two_hands(session, catalog)
    chest = await world.grant_item(
        session, await world.node_container(session, node), CHEST, quality=60, origin="test"
    )
    await world.move_stack(
        session, sack, await storage.inside(session, chest), amount_float(sack.amount)
    )
    first_id, second_id, sack_id, chest_id = first.id, second.id, sack.id, chest.id
    units = amount_float(sack.amount)
    await session.commit()

    held = asyncio.Event()

    async def take(body_id: uuid.UUID) -> float:
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            box = await db.get(Item, chest_id)
            thing = await db.get(Item, sack_id)
            assert me is not None and box is not None and thing is not None
            taken = await storage.take(db, constants, catalog, me, box, thing)
            if body_id == first_id:
                held.set()
                await _until_blocked_by(factory, db)
            return taken

    async def reach() -> float:
        await held.wait()
        return await take(second_id)

    taken, reached = await asyncio.gather(take(first_id), reach(), return_exceptions=True)

    assert taken == pytest.approx(units), taken
    assert isinstance(reached, storage.StorageError), reached
    assert reached.key == "storage-not-in-storage", reached.key
    async with factory() as db:
        thing = await db.get(Item, sack_id)
        me = await db.get(Body, first_id)
        assert thing is not None and me is not None
        assert thing.container_id == (await world.body_container(db, me)).id


async def test_an_output_does_not_settle_into_a_canister_burnt_with_the_yard(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A canister burnt while an output waited on it is gone, not an empty vessel.

    The fire takes what lies in the yard -- the canister and what is in it --
    and holds the rows. An output lands in the yard, reads the canister still
    standing and walks into its row. The fire commits. The object the output
    still held reads as a canister with nothing inside and all its room: the
    output opened a new inside for a thing that no longer exists, poured
    itself into it and reported nothing spilled -- the water left the world
    without a word. After the lock the canister is not there, and the output
    spills in the open.
    """
    node, _, _, canister, _ = await _clearing(session, constants, catalog)
    node_id, canister_id = node.id, canister.id
    per_water = gear.mass_of(catalog, WATER, 1)
    await session.commit()

    held = asyncio.Event()

    async def output() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            spot = await db.get(Node, node_id)
            assert spot is not None
            floor = await world.node_container(db, spot)
            made = await world.grant_item(
                db, floor, WATER, amount=POURED_KG / per_water, quality=60, origin="test"
            )
            return await liquid.settle(db, catalog, made, (floor,))

    burnt, spilled = await asyncio.gather(
        _burning(factory, node_id, held), output(), return_exceptions=True
    )

    assert not isinstance(burnt, BaseException), burnt
    assert spilled == pytest.approx(POURED_KG / per_water), spilled
    assert await _insides_of(factory, canister_id) == []


async def test_a_pour_into_a_canister_burnt_with_the_yard_is_refused(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A hand does not pour into a canister the fire took while it reached.

    The fire holds the canister lying in the yard; the filler, with a canister
    of water in the hands, pours into it and walks into its row. The fire
    commits. Poured by the sight from before the wait, the water went into a
    new inside of a canister that no longer exists -- out of the filler's
    hands and out of the world. After the lock the canister is gone, and the
    water stays in the hands.
    """
    node, _, filler, canister, source = await _clearing(session, constants, catalog)
    node_id, filler_id, canister_id, source_id = node.id, filler.id, canister.id, source.id
    per_water = gear.mass_of(catalog, WATER, 1)
    await session.commit()

    held = asyncio.Event()

    async def fill() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            me = await db.get(Body, filler_id)
            assert me is not None
            _, poured = await liquid.pour(
                db,
                constants,
                catalog,
                me,
                await db.get(Item, source_id),
                await db.get(Item, canister_id),
            )
            return poured

    burnt, poured = await asyncio.gather(
        _burning(factory, node_id, held), fill(), return_exceptions=True
    )

    assert not isinstance(burnt, BaseException), burnt
    assert isinstance(poured, liquid.LiquidError), poured
    assert poured.key == "thing-gone", poured.key
    assert await _water_in(factory, source_id) == pytest.approx(POURED_KG / per_water)
    assert await _insides_of(factory, canister_id) == []


async def test_a_machine_put_up_before_the_lift_is_not_picked_off_the_floor(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """What stands is taken down by whoever may dispose of the place, not lifted by a guest.

    The holder puts a workbench up off the floor of the house and keeps the
    transaction open, holding its row. A guest -- let in, so the floor is
    theirs to pick from (D-204) -- sees the bench still lying and walks into
    the row. The put-up commits. Judged by the sight from before the wait, the
    pick went on, and the move, which unbolts what it moves, carried the
    standing machine out of the house into the guest's hands: past the taking
    down (D-278, D-308) that asks whose the place is, and for a rig past the
    hopper it asks about (D-314). After the lock the bench stands.
    """
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session, f"terra.gone.{stamp}", "House", area_m2=400, layer=Layer.PLANET
    )
    holder = await world.create_identity(session, f"Holder-{stamp}")
    node.owner_identity_id = holder.id
    session.add(Building(node_id=node.id, area_m2=20, footprint_m2=20, floors=1))
    await session.flush()
    owner = await world.print_body(session, holder, node)
    guest = await world.print_body(
        session, await world.create_identity(session, f"Guest-{stamp}"), node
    )
    bench = await world.grant_item(
        session, await world.body_container(session, owner), BENCH, quality=60, origin="test"
    )
    await storage.drop(session, constants, catalog, owner, bench, indoors=True)
    floor = (await world.node_container(session, node)).id
    owner_id, guest_id, bench_id = owner.id, guest.id, bench.id
    await session.commit()

    held = asyncio.Event()

    async def put_up() -> None:
        async with factory() as db, db.begin():
            me = await db.get(Body, owner_id)
            thing = await db.get(Item, bench_id)
            assert me is not None and thing is not None
            await station.place(db, catalog, me, thing)
            held.set()
            await _until_blocked_by(factory, db)

    async def reach() -> float:
        await held.wait()
        async with factory() as db, db.begin():
            me = await db.get(Body, guest_id)
            thing = await db.get(Item, bench_id)
            assert me is not None and thing is not None
            return await storage.pick(db, constants, catalog, me, thing)

    placed, reached = await asyncio.gather(put_up(), reach(), return_exceptions=True)

    assert placed is None, placed
    assert isinstance(reached, storage.StorageError), reached
    assert reached.key == "storage-standing", reached.key
    async with factory() as db:
        thing = await db.get(Item, bench_id)
        assert thing is not None
        assert thing.container_id == floor, "the bench stayed in the house"
        assert thing.installed, "and stands"
