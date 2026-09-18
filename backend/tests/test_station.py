# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A machine is taken by one worker, and it is placed at home -- in a building (D-106, D-150).

Checked is what the rule exists for:

* while a batch runs, the machine is taken and not given to a second;
* the batch ended -- free;
* a machine is placed in **own** node, not any; one busy with work is not carried away;
* without a building a machine does not stand, and in a cramped building places run out.

The consequence all this was made for: the city workshop stops being a free
shop floor for the whole town, and the craftsman comes to need a machine of
their own at home.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import craft, estate, station, world
from src.models.craft import BatchState, CraftBatch
from src.models.estate import Building
from src.models.identity import BodyState
from src.models.inventory import Item

BENCH = "workbench"
#: Made at the workbench out of wood alone -- the simplest honest batch.
MAKE = "handle"


async def _workshop(session: AsyncSession, *, machine_count: int = 1):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.shop.{stamp}", "workshop", area_m2=200)
    #: A machine lives in a building (D-106): the test's workshop is fully built.
    session.add(Building(node_id=node.id, area_m2=200))
    await session.flush()
    yard = await world.node_container(session, node)
    for _ in range(machine_count):
        await world.grant_item(session, yard, BENCH, quality=60, origin="тест")
    return node


async def _master(session: AsyncSession, node, name: str):
    identity = await world.create_identity(session, f"{name}-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    await world.learn(session, identity, MAKE)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "wood", amount=50, quality=60, origin="тест")
    return identity, body


async def test_machine_busy_with_one(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A hundred players at one anvil is not a workshop but a queue that does not exist."""
    node = await _workshop(session)
    _, first = await _master(session, node, "Первый")
    _, second = await _master(session, node, "Второй")

    await craft.start(session, constants, catalog, first, MAKE, 1)
    with pytest.raises(craft.Busy):
        await craft.start(session, constants, catalog, second, MAKE, 1)


async def test_one_machine_one_work(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Taken means taken, including for the master themselves.

    Otherwise the machine's owner would start any number of batches at it at
    once, and the rule "one person works at a machine" would hold only against
    strangers. Since D-209 the second work is not refused but **waits its turn**:
    one body works one batch, and the machine stays with the running one.
    """
    node = await _workshop(session)
    _, master = await _master(session, node, "Мастер")
    first = await craft.start(session, constants, catalog, master, MAKE, 1)
    second = await craft.start(session, constants, catalog, master, MAKE, 1)
    assert first.state is BatchState.RUNNING
    assert second.state is BatchState.WAITING
    assert second.station_item_id is None
    assert second.ready_at is None


async def test_second_machine_clears_queue(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """There are exactly as many places in a workshop as machines."""
    node = await _workshop(session, machine_count=2)
    _, first = await _master(session, node, "Первый")
    _, second = await _master(session, node, "Второй")

    one = await craft.start(session, constants, catalog, first, MAKE, 1)
    other = await craft.start(session, constants, catalog, second, MAKE, 1)
    assert one.station_item_id != other.station_item_id


async def test_machine_freed_when_batch_ends(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node = await _workshop(session)
    _, master = await _master(session, node, "Мастер")
    batch = await craft.start(session, constants, catalog, master, MAKE, 1)

    machine = await session.get(Item, batch.station_item_id)
    assert machine.busy_body_id == master.id

    from sqlalchemy import select

    from src.models.job import Job, JobKind, JobState

    job = (
        (
            await session.execute(
                select(Job).where(
                    Job.kind == JobKind.CRAFT_BATCH.value, Job.state == JobState.PENDING
                )
            )
        )
        .scalars()
        .first()
    )
    await craft.finish(session, job)
    await session.refresh(machine)
    assert machine.busy_body_id is None


async def test_machine_placed_at_own_place(
    session: AsyncSession, catalog: Catalog, own_plot
) -> None:
    """You cannot build up somebody else's node: that is the point of a home."""
    node = await _workshop(session, machine_count=0)
    identity, body = await _master(session, node, "Хозяин")
    stranger, stranger_body = await _master(session, node, "Чужой")
    pocket = await world.body_container(session, body)
    machine = await world.grant_item(session, pocket, BENCH, quality=60, origin="тест")

    await own_plot(node, stranger)
    with pytest.raises(station.NotYours):
        await station.place(session, catalog, body, machine)

    #: The plot changes hands -- and only then does the machine stand.
    node.owner_identity_id = identity.id
    await session.flush()
    await station.place(session, catalog, body, machine)
    yard = await world.node_container(session, node)
    assert machine.container_id == yard.id


async def test_machine_stands_on_nobodys_land(session: AsyncSession, catalog: Catalog) -> None:
    """Land outside a city has no owner, and work on it is open to all (D-198)."""
    node = await _workshop(session, machine_count=0)
    assert node.owner_identity_id is None and node.owner_city_id is None

    _, body = await _master(session, node, "Пришлый")
    pocket = await world.body_container(session, body)
    machine = await world.grant_item(session, pocket, BENCH, quality=60, origin="тест")

    await station.place(session, catalog, body, machine)
    yard = await world.node_container(session, node)
    assert machine.container_id == yard.id


async def test_authority_does_not_run_foreign_house(
    session: AsyncSession, catalog: Catalog
) -> None:
    """A bought plot stands on city land, but its owner is a person.

    The `laws` right is about the city's buildings; somebody's house is taken
    by court, not by power (D-089, D-116, D-166).
    """
    from src.engine import city as town
    from src.models.world import Layer

    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.state.{stamp}", "Столица", area_m2=1, layer=Layer.PLANET
    )
    plot = await world.create_node(
        session,
        f"terra.state.{stamp}.lot",
        "Участок",
        area_m2=200,
        layer=Layer.PLANET,
        parent=planet,
    )
    city = await town.found(session, catalog, planet, "Столица")
    plot.owner_city_id = city.id
    await session.flush()

    ruler, ruler_body = await _master(session, plot, "Правитель")
    await town.install_founder(session, city, ruler)
    #: While the land is civic -- the authority disposes of it.
    assert await station.may_build(session, ruler_body, plot)

    #: A private owner appeared -- and the authority stops being the owner.
    owner, _ = await _master(session, plot, "Хозяин")
    plot.owner_identity_id = owner.id
    await session.flush()
    assert not await station.may_build(session, ruler_body, plot)


async def test_machine_not_placed_without_building(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Build first, then furnish (D-106): a yard is not a workshop."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.bare.{stamp}", "Пустырь", area_m2=200)
    identity, body = await _master(session, node, "Хозяин")
    pocket = await world.body_container(session, body)
    machine = await world.grant_item(session, pocket, BENCH, quality=60, origin="тест")

    with pytest.raises(estate.NoBuilding):
        await station.place(session, catalog, body, machine)


async def test_machines_take_building_area(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A place is `build.slots_per_area` m2 per thing: in a cramped house machines do not
    multiply."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.tiny.{stamp}", "Тесный дом", area_m2=100)
    #: A building for one place: a second does not fit.
    session.add(Building(node_id=node.id, area_m2=10))
    await session.flush()
    identity, body = await _master(session, node, "Хозяин")
    pocket = await world.body_container(session, body)

    first = await world.grant_item(session, pocket, BENCH, quality=60, origin="тест")
    await station.place(session, catalog, body, first)

    second = await world.grant_item(session, pocket, BENCH, quality=60, origin="тест")
    with pytest.raises(estate.NoRoom):
        await station.place(session, catalog, body, second)


async def test_furniture_placed_like_machine_but_as_furniture(
    session: AsyncSession, catalog: Catalog
) -> None:
    """A bed is furniture, not a machine (D-090): placed in a building the same way."""
    node = await _workshop(session, machine_count=0)
    _, body = await _master(session, node, "Хозяин")
    pocket = await world.body_container(session, body)
    bed = await world.grant_item(session, pocket, "bed", quality=60, origin="тест")

    assert station.is_furniture(catalog, "bed")
    assert not station.is_station(catalog, "bed")
    await station.place(session, catalog, body, bed)
    yard = await world.node_container(session, node)
    assert bed.container_id == yard.id


async def test_non_machine_not_placed_in_node(session: AsyncSession, catalog: Catalog) -> None:
    node = await _workshop(session, machine_count=0)
    _, body = await _master(session, node, "Хозяин")
    pocket = await world.body_container(session, body)
    sack = await world.grant_item(session, pocket, "wood", amount=1, quality=60, origin="тест")
    with pytest.raises(station.NotStation):
        await station.place(session, catalog, body, sack)


async def test_the_last_machine_a_waiting_batch_needs_is_not_taken_down(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A batch frozen while its master is away holds no machine (D-209): it
    takes a free one of its name on return, so `busy` cannot see it. Taking
    the last such machine down left it waiting for ever on materials already
    written off (OQ-181). With a second one beside it, one of the two may go;
    the last one stays until the batch is done (D-351)."""
    node = await _workshop(session, machine_count=2)
    _, master = await _master(session, node, "Мастер")
    batch = await craft.start(session, constants, catalog, master, MAKE, 1)
    await craft.freeze(session, master)
    await session.flush()
    waiting = await session.get(CraftBatch, batch.id)
    assert waiting is not None
    assert waiting.state is BatchState.WAITING
    assert waiting.station_item_id is None, "ждущая партия станка не держит"

    yard = await world.node_container(session, node)
    benches = [thing for thing in await world.contents(session, yard) if thing.type_key == BENCH]
    assert len(benches) == 2
    await station.take(session, catalog, master, benches[0])
    with pytest.raises(station.Busy) as waits:
        await station.take(session, catalog, master, benches[1])
    assert waits.value.key == "station-batch-waits"


async def test_a_batch_elsewhere_or_done_does_not_hold_a_machine(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The rule reaches only an unfinished batch of **this** node that needs a
    machine of **this** name (D-351): a batch done or cancelled, one in
    another node, and one waiting for another kind of machine each leave the
    last bench free to come down."""
    node = await _workshop(session)
    _, master = await _master(session, node, "Мастер")
    elsewhere = await _workshop(session)
    _, stranger = await _master(session, elsewhere, "Чужой")
    await craft.start(session, constants, catalog, stranger, MAKE, 1)
    await craft.freeze(session, stranger)

    batch = await craft.start(session, constants, catalog, master, MAKE, 1)
    await craft.freeze(session, master)
    await session.flush()
    for state in (BatchState.DONE, BatchState.CANCELLED):
        batch.state = state
        await session.flush()
        bench = await _bench(session, node)
        assert not await station._awaited(session, node, bench), state
    batch.state = BatchState.WAITING
    batch.station = "forge"
    await session.flush()
    bench = await _bench(session, node)
    assert not await station._awaited(session, node, bench), "другое имя станка"
    await station.take(session, catalog, master, bench)


async def test_a_dead_masters_batch_does_not_pin_the_machine(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Death freezes the batch, and the printed body never takes it up again
    (D-209): a new body is a new body. Counted, one death at work would pin the
    last machine of its name for ever, and the house with it (D-308, D-351)."""
    node = await _workshop(session)
    _, master = await _master(session, node, "Мастер")
    await craft.start(session, constants, catalog, master, MAKE, 1)
    await craft.freeze(session, master)
    master.state = BodyState.DEAD
    await session.flush()
    _, heir = await _master(session, node, "Наследник")
    bench = await _bench(session, node)
    await station.take(session, catalog, heir, bench)
    assert not bench.installed


async def _bench(session: AsyncSession, node) -> Item:
    yard = await world.node_container(session, node)
    return next(thing for thing in await world.contents(session, yard) if thing.type_key == BENCH)


async def test_busy_machine_not_carried_away(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A machine cannot be carried out from under a worker -- even your own."""
    node = await _workshop(session)
    _, master = await _master(session, node, "Мастер")
    batch = await craft.start(session, constants, catalog, master, MAKE, 1)

    machine = await session.get(Item, batch.station_item_id)
    with pytest.raises(station.Busy):
        await station.take(session, catalog, master, machine)
