# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Put up or lying (D-278), and one house per plot (D-279).

A machine or a piece of furniture either stands in the building it was put
up in -- counted in the slots, worked at, shown among the machines -- or lies
as cargo. Until now the two were one: a station dropped on the floor was
installed by the drop, past the slot limit and past the owner's door.
Checked:

* dropped, a machine lies: in the floor's list, out of the machines', out of
  the slots, and picked up like a sack;
* put up from the floor it stands; the slot limit and the door hold there;
* taken up, it leaves the slots; a lying one is not "taken", it is picked;
* what falls out of overloaded hands lies; a station built in place stands
  where it was made, a portable one left at the bench lies;
* two hands putting up into the last place: one of them is refused;
* taking down is not taking into the hands (D-308): the machine lies where it
  stood, and the carry limit answers at the pick-up -- a machine heavier than
  the hands is not pocketed by unbolting it; two hands take one machine down
  once; and matter needs room to lie in, on either surface;
* what stands is not picked off the floor by anybody: it is taken up, by the
  holder; a lying machine is out of the scene -- no machine, no programme --
  while a lying chest still opens, and pays in weight instead (D-313);
* a plot with a house, or with a site laid, takes no second house.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
from src.api.commands import views
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import alpha, automat, estate, gear, overload, station, storage, world
from src.engine.estate.building import site as sites
from src.models.estate import Building
from src.models.event import Event, EventKind
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Layer
from src.seed_catchup import _lie_down_cargo

BENCH = "workbench"
CHEST = "chest"
TERMINAL = "market_terminal"
AUTOMAT = "auto_station"
ORE = "iron_ore"
#: Heavier than a pair of hands (`inventory.carry_mass`), and not built in
#: place -- so it is a machine one may take down and may not carry off.
HEAVY = AUTOMAT


async def _plot(session: AsyncSession, constants: Constants, *, area: float = 20, owner=None):
    """A plot with a house of `area` metres: `area / build.slots_per_area` places."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session, f"terra.stand.{stamp}", "Двор", area_m2=400, layer=Layer.PLANET
    )
    identity = owner or await world.create_identity(session, f"Хозяин-{stamp}")
    node.owner_identity_id = identity.id
    session.add(Building(node_id=node.id, area_m2=area, footprint_m2=area, floors=1))
    await session.flush()
    body = await world.print_body(session, identity, node)
    return node, identity, body


async def _in_hands(session: AsyncSession, body: Body, what: str) -> Item:
    pocket = await world.body_container(session, body)
    return await world.grant_item(session, pocket, what, quality=60, origin="тест")


async def _standing(session: AsyncSession, constants: Constants, catalog: Catalog, node) -> int:
    return (await estate.slots(session, constants, node))[1]


async def test_a_dropped_machine_lies_and_a_put_up_one_stands(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Dropping is not installing: the floor's list, not the machines'; no
    slot taken; picked up like a sack. Put up from the floor, it stands."""
    node, _, body = await _plot(session, constants)
    bench = await _in_hands(session, body, BENCH)
    assert await _standing(session, constants, catalog, node) == 0

    await storage.drop(session, constants, catalog, body, bench, indoors=True)
    inside, _ = await estate.split(session, node)
    assert [thing.id for thing in inside] == [bench.id], "лежит на полу как груз"
    assert not bench.installed
    assert await _standing(session, constants, catalog, node) == 0
    assert BENCH not in await world.thing_kinds(session, node), (
        "лежащий станок не делает место мастерской"
    )
    with pytest.raises(station.StationError) as refused:
        await station.take(session, catalog, body, bench)
    assert refused.value.key == "station-not-installed"

    await station.place(session, catalog, body, bench)
    assert bench.installed
    assert await _standing(session, constants, catalog, node) == 1
    inside, _ = await estate.split(session, node)
    assert inside == []
    assert BENCH in await world.thing_kinds(session, node)

    await station.take(session, catalog, body, bench)
    assert not bench.installed
    assert await _standing(session, constants, catalog, node) == 0


async def test_a_lying_machine_is_picked_like_a_sack(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node, _, body = await _plot(session, constants)
    bench = await _in_hands(session, body, BENCH)
    await storage.drop(session, constants, catalog, body, bench, indoors=True)
    await storage.pick(session, constants, catalog, body, bench)
    pocket = await world.body_container(session, body)
    assert bench.container_id == pocket.id and not bench.installed


async def test_the_slot_limit_and_the_door_hold_at_putting_up_only(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A guest lets a machine down on the floor -- the floor is theirs to put
    down on (D-204) -- and cannot stand it; the owner stands it while there is
    a place, and the second beyond the limit lies on."""
    per = constants[R.BUILD_SLOTS_PER_AREA]
    #: One place, and half a place of floor to spare: the second machine has
    #: metres to lie on but no place to stand in.
    node, owner, body = await _plot(session, constants, area=per + per / 2)
    guest = await world.create_identity(session, f"Гость-{uuid.uuid4().hex[:6]}")
    guest_body = await world.print_body(session, guest, node)
    one = await _in_hands(session, guest_body, BENCH)
    await storage.drop(session, constants, catalog, guest_body, one, indoors=True)
    with pytest.raises(station.NotYours) as shut:
        await station.place(session, catalog, guest_body, one)
    assert shut.value.key == "station-node-not-yours"

    await station.place(session, catalog, body, one)
    two = await _in_hands(session, body, BENCH)
    with pytest.raises(estate.NoRoom) as full:
        await station.place(session, catalog, body, two)
    assert full.value.key == "station-no-room"
    await storage.drop(session, constants, catalog, body, two, indoors=True)
    assert await _standing(session, constants, catalog, node) == 1
    inside, _ = await estate.split(session, node)
    assert [thing.id for thing in inside] == [two.id]


async def test_a_lying_chest_is_cargo_and_a_standing_one_a_store(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node, _, body = await _plot(session, constants)
    chest = await _in_hands(session, body, CHEST)
    await storage.drop(session, constants, catalog, body, chest, indoors=True)
    inside, _ = await estate.split(session, node)
    assert [thing.id for thing in inside] == [chest.id]
    await station.place(session, catalog, body, chest)
    inside, _ = await estate.split(session, node)
    assert inside == []


async def test_what_falls_lies_and_what_is_built_stands(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A fall (D-265) is a drop; a print onto the floor of a portable machine
    lies too; a station built in place stands where it appears (D-268)."""
    node, _, body = await _plot(session, constants, area=40)
    pocket = await world.body_container(session, body)
    limit = constants[R.INVENTORY_CARRY_MASS]
    heavy = await world.grant_item(
        session,
        pocket,
        ORE,
        amount=limit / catalog.recipes.mass_of(ORE) + 1,
        quality=60,
        origin="тест",
    )
    bench = await _in_hands(session, body, BENCH)
    #: The hands are already over the limit with the ore; the bench is what
    #: arrives on top and what the settlement lets fall (D-265).
    del heavy
    await overload.settle_load(session, constants, catalog, body, [bench])
    yard = await world.node_container(session, node)
    fallen = [thing for thing in await world.node_things(session, node) if thing.type_key == BENCH]
    assert fallen and all(not thing.installed for thing in fallen), "упавшее лежит"
    assert yard.id == fallen[0].container_id

    printed = await alpha.spawn(
        session, constants, catalog, body, type_key=BENCH, where=alpha.FLOOR
    )
    assert not printed.installed

    built = await alpha.spawn(session, constants, catalog, body, type_key=TERMINAL)
    assert catalog.recipes.built(TERMINAL) and built.installed, "построенное на месте стоит"
    assert TERMINAL in await world.thing_kinds(session, node)


async def test_two_hands_do_not_both_take_the_last_place(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The plot row serialises the two: one stands its machine, the other is
    refused -- never two machines in one place."""
    _slow(monkeypatch, estate, "slots")
    per = constants[R.BUILD_SLOTS_PER_AREA]
    node, owner, one = await _plot(session, constants, area=per)
    two = await world.print_body(session, owner, node)
    first = await _in_hands(session, one, BENCH)
    second = await _in_hands(session, two, BENCH)
    pairs = [(one.id, first.id), (two.id, second.id)]
    node_id = node.id
    await session.commit()

    async def go(body_id: uuid.UUID, item_id: uuid.UUID) -> bool:
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            thing = await db.get(Item, item_id)
            assert me is not None and thing is not None
            try:
                await station.place(db, catalog, me, thing)
            except estate.NoRoom:
                return False
            return True

    done = await asyncio.gather(*(go(*pair) for pair in pairs))
    assert sorted(done) == [False, True], "one place, one machine"
    async with factory() as db:
        again = await db.get(type(node), node_id)
        assert again is not None
        assert (await estate.slots(db, constants, again))[1] == 1


async def test_a_plot_takes_one_house(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Where a house stands, or a site is laid, no second house is laid (D-279)."""
    node, _, body = await _plot(session, constants)
    with pytest.raises(estate.EstateError) as stands:
        await sites.lay(session, constants, body, node, constants[R.BUILD_AREA_MIN])
    assert stands.value.key == "estate-build-house-stands"

    stamp = uuid.uuid4().hex[:8]
    bare = await world.create_node(
        session, f"terra.bare.{stamp}", "Пустырь", area_m2=400, layer=Layer.PLANET
    )
    builder = await world.create_identity(session, f"Строитель-{stamp}")
    hands = await world.print_body(session, builder, bare)
    await sites.lay(session, constants, hands, bare, constants[R.BUILD_AREA_MIN])
    with pytest.raises(estate.EstateError) as laid:
        await sites.lay(session, constants, hands, bare, constants[R.BUILD_AREA_MIN])
    assert laid.value.key == "estate-build-house-stands"


async def test_what_stands_is_not_picked_off_the_floor(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Two doors, one each way (D-278): a standing machine is taken up by
    `station.take`, which asks whose the place is; a guest's `storage.pick`
    does not carry the host's workbench off past that door."""
    node, _, body = await _plot(session, constants)
    bench = await _in_hands(session, body, BENCH)
    await station.place(session, catalog, body, bench)
    guest = await world.print_body(
        session, await world.create_identity(session, f"Гость-{uuid.uuid4().hex[:6]}"), node
    )
    with pytest.raises(storage.StorageError) as refused:
        await storage.pick(session, constants, catalog, guest, bench)
    assert refused.value.key == "storage-standing"
    with pytest.raises(storage.StorageError) as own:
        await storage.pick(session, constants, catalog, body, bench)
    assert own.value.key == "storage-standing"
    await station.take(session, catalog, body, bench)
    assert not bench.installed


async def test_a_lying_machine_is_out_of_the_scene(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The look's machines are what stands (D-278): a bench dropped on the
    floor is in the floor's list and nowhere else, and an automat lying there
    takes no programme.

    A chest is not among them (D-313). Standing is what a machine is worked
    at for, and a storage is not worked at: it opens where it lies, because in
    an open field there is nowhere to stand one. What a lying chest costs is
    weight -- it is picked up with everything in it -- not a shut lid.
    """
    node, _, body = await _plot(session, constants, area=60)
    bench = await _in_hands(session, body, BENCH)
    chest = await _in_hands(session, body, CHEST)
    robot = await _in_hands(session, body, AUTOMAT)
    for thing in (bench, chest, robot):
        await storage.drop(session, constants, catalog, body, thing, indoors=True)
    assert await views._bench(session, node, body) == []
    assert [one["goods"] for one in await views._storages(session, constants, node, body)] == [
        CHEST
    ]
    with pytest.raises(automat.NotAnAutomat) as refused:
        await automat.program(session, constants, catalog, body, robot, "nails")
    assert refused.value.key == "auto-not-installed"

    for thing in (bench, chest, robot):
        await station.place(session, catalog, body, thing)
    assert {one["goods"] for one in await views._bench(session, node, body)} == {BENCH, AUTOMAT}
    assert [one["goods"] for one in await views._storages(session, constants, node, body)] == [
        CHEST
    ]


async def test_the_catch_up_lays_the_heaps_back_down(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The migration stands up everything in a node's store, machines and
    heaps alike (it knows containers, not the catalog); the seed's catch-up
    lays the heaps down and leaves the machines standing -- and a second
    pass finds nothing to do."""
    node, _, _ = await _plot(session, constants)
    yard = await world.node_container(session, node)
    bench = await world.grant_item(session, yard, BENCH, quality=60, origin="тест")
    ore = await world.grant_item(session, yard, ORE, amount=5, quality=60, origin="тест")
    #: As the migration leaves it: the heap stood up beside the bench.
    ore.installed = True
    await session.flush()

    assert await _lie_down_cargo(session) == 1
    await session.refresh(ore)
    await session.refresh(bench)
    assert not ore.installed and bench.installed
    assert await _lie_down_cargo(session) == 0, "второй проход ничего не меняет"
    #: And a fresh heap of the same ore folds into the old one again (D-214).
    more = await world.grant_item(session, yard, ORE, amount=3, quality=60, origin="тест")
    await world.stack_up(session, more)
    heaps = [thing for thing in await world.node_things(session, node) if thing.type_key == ORE]
    assert len(heaps) == 1


async def test_a_machine_is_taken_down_onto_the_floor_not_into_the_hands(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Two doors, not one (D-308). Taking down leaves the machine lying where
    it stood; the hands answer at the second door, and a machine heavier than
    they are is not pocketed by unbolting it."""
    node, _, body = await _plot(session, constants, area=60)
    yard = await world.node_container(session, node)
    #: Standing it up off the floor, the way a machine too heavy to lift gets
    #: put up at all (D-278): made at the bench, it fell and lay here.
    robot = await world.grant_item(session, yard, HEAVY, quality=60, origin="тест", installed=False)
    await station.place(session, catalog, body, robot)
    assert robot.installed
    assert gear.mass_of(catalog, HEAVY, 1) > await gear.capacity(
        session, constants, catalog, body
    ), "иначе тест ничего не ловит: машина должна быть тяжелее рук"

    await station.take(session, catalog, body, robot)
    pocket = await world.body_container(session, body)
    assert robot.container_id == yard.id and robot.container_id != pocket.id, "легла на пол"
    assert not robot.installed and not robot.outdoors
    inside, _ = await estate.split(session, node)
    assert [thing.id for thing in inside] == [robot.id], "в списке пола, по весу"
    assert await _standing(session, constants, catalog, node) == 0

    with pytest.raises(gear.Overloaded) as refused:
        await storage.pick(session, constants, catalog, body, robot)
    assert refused.value.key == "gear-overloaded"


async def test_a_light_machine_goes_down_and_then_up_into_the_hands(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The second door is the ordinary one: what the hands hold, they take off
    the floor. A house of exactly one place takes its only machine down --
    standing, the machine holds that place, and it gives it back by lying."""
    node, _, body = await _plot(session, constants, area=constants[R.BUILD_SLOTS_PER_AREA])
    bench = await _in_hands(session, body, BENCH)
    await station.place(session, catalog, body, bench)
    assert (await estate.slots(session, constants, node))[0] == 1, "дом ровно на одно место"

    await station.take(session, catalog, body, bench)
    yard = await world.node_container(session, node)
    assert bench.container_id == yard.id and not bench.installed

    await storage.pick(session, constants, catalog, body, bench)
    pocket = await world.body_container(session, body)
    assert bench.container_id == pocket.id


async def test_two_hands_do_not_both_take_one_machine_down(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The thing's own row serialises the two: one takes the machine down, the
    other finds it already lying and is told so -- never two takings of one
    machine, and never two journal lines for it."""
    _slow(monkeypatch, storage, "require_room")
    node, owner, one = await _plot(session, constants, area=60)
    two = await world.print_body(session, owner, node)
    bench = await _in_hands(session, one, BENCH)
    await station.place(session, catalog, one, bench)
    hands = [one.id, two.id]
    item_id, node_id = bench.id, node.id
    await session.commit()

    async def go(body_id: uuid.UUID) -> bool:
        async with factory() as db, db.begin():
            me = await db.get(Body, body_id)
            thing = await db.get(Item, item_id)
            assert me is not None and thing is not None
            try:
                await station.take(db, catalog, me, thing)
            except station.StationError as refused:
                assert refused.key == "station-not-installed"
                return False
            return True

    done = await asyncio.gather(*(go(body) for body in hands))
    assert sorted(done) == [False, True], "одна машина — одно снятие"
    async with factory() as db:
        again = await db.get(Item, item_id)
        assert again is not None and not again.installed
        lines = await db.execute(
            select(Event).where(Event.kind == EventKind.STATION_TAKEN, Event.node_id == node_id)
        )
        assert len(lines.scalars().all()) == 1, "и одна строка в журнале"


async def test_a_full_ground_refuses_to_take_a_machine_down(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Out on the ground the machine gives nothing back: standing on a vein it
    was never charged to the yard (D-244), and lying it is new weight. A yard
    with no metres left refuses the take-down the way it refuses any putting
    down -- and says how many metres are missing."""
    stamp = uuid.uuid4().hex[:8]
    #: A bare plot, as a vein site is: no building, so no floor -- everything
    #: lying here is out under the sky whatever its mark says.
    bare = await world.create_node(
        session, f"terra.vein.{stamp}", "Жила", area_m2=2, layer=Layer.PLANET
    )
    body = await world.print_body(
        session, await world.create_identity(session, f"Буровик-{stamp}"), bare
    )
    yard = await world.node_container(session, bare)
    robot = await world.grant_item(session, yard, HEAVY, quality=60, origin="тест")
    assert robot.installed, "гранты в узел ставят машину стоять (D-278)"
    #: Almost the whole yard under ore: `build.floor_per_m2` kilograms to the metre.
    fills = 2 * constants[R.BUILD_FLOOR_PER_M2] - 1
    await world.grant_item(
        session, yard, ORE, amount=fills / catalog.recipes.mass_of(ORE), origin="тест"
    )

    with pytest.raises(storage.NoRoom) as refused:
        await station.take(session, catalog, body, robot)
    assert refused.value.key == "storage-no-room"
    assert refused.value.params["inside"] == "false"
    assert robot.installed, "отказ ничего не двигает"


async def test_an_overfull_floor_refuses_too_but_counts_the_place_given_back(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Indoors the machine gives its place back before it lies, so a house of
    one place takes its machine down. But the place given back is not a licence:
    on a floor already over its weight the metres are counted honestly, from the
    true remainder, and the take-down is refused."""
    per = constants[R.BUILD_SLOTS_PER_AREA]
    node, _, body = await _plot(session, constants, area=2 * per)
    yard = await world.node_container(session, node)
    robot = await world.grant_item(session, yard, HEAVY, quality=60, origin="тест")
    #: More cargo than the floor holds -- a state the world reaches without any
    #: door asking (D-265): what falls out of overloaded hands lies anyway.
    over = 2 * per * constants[R.BUILD_FLOOR_PER_M2]
    await world.grant_item(
        session, yard, ORE, amount=over / catalog.recipes.mass_of(ORE), origin="тест"
    )

    with pytest.raises(storage.NoRoom) as refused:
        await station.take(session, catalog, body, robot)
    assert refused.value.key == "storage-no-room"
    assert refused.value.params["inside"] == "true"
    assert robot.installed
