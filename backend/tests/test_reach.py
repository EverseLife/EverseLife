# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""How far the hands reach when a work gathers its materials (D-315).

The reach used to be one place -- the pocket -- and everything else in the
node did not exist for a batch: the chest two steps away, the wagon in the
yard, the harvest on the floor. What is checked here is the rule that
replaced it, and each of its edges:

* the pocket, one's own convoy and -- where the place is ours -- the floor,
  the yard and the chests standing here all feed a batch;
* somebody else's place feeds nothing, and neither does somebody else's
  harness: a guest's loaded wagon in our yard stays the guest's;
* the reach of the work is the reach of the hand: what stands is not spent,
  and neither is fuel lying where a fuel plant stands;
* the wider reach is a shared one, so two batches over one heap end with one
  refused rather than both fed;
* and the forecast still writes nothing -- it reaches further now, and a read
  that furnishes an empty wagon on the way is still a read that writes.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import craft, energy, reach, storage, transport, world
from src.engine.craft import power
from src.models.estate import Building
from src.models.identity import Body
from src.models.inventory import Container, ContainerKind, Item
from src.models.world import PLOT, Node
from src.units import amount_float

INGOT = "iron_ingot"
NAILS = "nails"
FORGE = "forge"
CHEST = "chest"
CART = "cart"


async def _forge(session: AsyncSession, *, owned: bool = True):
    """A plot with a forge on it, and a master standing at it.

    Civic land handed over to the owner: outside a city land belongs to nobody
    and everybody may build on it (D-198), and there the reach would be open to
    all -- which is exactly what half of these tests must not be measuring.
    """
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session, f"terra.reach.{stamp}", "Кузница", area_m2=200, properties={PLOT: True}
    )
    node.owner_city_id = uuid.uuid4()
    session.add(Building(node_id=node.id, area_m2=200))
    await session.flush()
    identity = await world.create_identity(session, f"Мастер-{stamp}")
    body = await world.print_body(session, identity, node)
    if owned:
        await world.grant_node(session, node, identity)
    await world.learn(session, identity, NAILS)
    yard = await world.node_container(session, node)
    await world.grant_item(
        session, yard, FORGE, quality=60, origin="сценарий теста", installed=True
    )
    return node, identity, body


async def _into_chest(session: AsyncSession, node: Node, quantity: float) -> Item:
    """Ingots inside a chest put up here."""
    yard = await world.node_container(session, node)
    chest = await world.grant_item(
        session, yard, CHEST, quality=60, origin="сценарий теста", installed=True
    )
    inside = await storage.inside(session, chest)
    return await world.grant_item(
        session, inside, INGOT, amount=quantity, quality=70, origin="сценарий теста"
    )


async def _left(session: AsyncSession, container_id: uuid.UUID) -> float:
    total = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == container_id, Item.type_key == INGOT
        )
    )
    return amount_float(int(total or 0))


# --- the three places ---------------------------------------------------------


async def test_a_batch_eats_out_of_a_chest_standing_here(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The chest by the bench is a store, and now a store the work reaches into.

    Before D-315 this was a refusal with the iron two steps away: the master
    had to carry it into the hands first, by the load limit -- and the limit
    guarded nothing, since carrying it all across the yard is always possible,
    merely slow.
    """
    node, _, body = await _forge(session)
    stack = await _into_chest(session, node, 20)

    plan = await craft.plan(session, constants, catalog, body, NAILS, 1)
    assert plan.consumes[INGOT] > 0
    await craft.start(session, constants, catalog, body, NAILS, 1)
    assert await _left(session, stack.container_id) < 20


async def test_a_batch_eats_off_the_floor_and_the_yard(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Both surfaces of one's own place (D-244): a heap is a heap, roofed or not."""
    node, _, body = await _forge(session)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, INGOT, amount=20, quality=70, origin="сценарий теста")

    await craft.start(session, constants, catalog, body, NAILS, 1)
    assert await _left(session, yard.id) < 20


async def test_a_batch_eats_out_of_ones_own_hold(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The convoy walks with the body and is held by its own harness, so it is
    reached wherever the body works -- somebody else's land included."""
    node, _, body = await _forge(session, owned=False)
    yard = await world.node_container(session, node)
    cart = await world.grant_item(session, yard, CART, amount=1, origin="сценарий теста")
    await transport.harness(session, constants, catalog, body, cart)
    hold = await transport.cargo(session, cart)
    await world.grant_item(session, hold, INGOT, amount=20, quality=70, origin="сценарий теста")

    await craft.start(session, constants, catalog, body, NAILS, 1)
    assert await _left(session, hold.id) < 20


# --- and where the reach stops ------------------------------------------------


async def test_a_guest_does_not_reach_the_hosts_chest(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Somebody else's chest is not opened by a batch either (D-181): a work is
    not a way around the door."""
    node, _, body = await _forge(session, owned=False)
    host = await world.create_identity(session, f"Хозяин-{uuid.uuid4().hex[:8]}")
    await world.grant_node(session, node, host)
    await _into_chest(session, node, 20)

    with pytest.raises(craft.NotEnough):
        await craft.plan(session, constants, catalog, body, NAILS, 1)


async def test_a_guests_harness_keeps_its_cargo(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A loaded wagon standing in our yard under somebody else's harness stays
    theirs -- or calling on the smith would be a way of handing over cargo."""
    node, _, body = await _forge(session)
    yard = await world.node_container(session, node)
    cart = await world.grant_item(session, yard, CART, amount=1, origin="сценарий теста")
    guest = await world.create_identity(session, f"Гость-{uuid.uuid4().hex[:8]}")
    visitor = await world.print_body(session, guest, node)
    await transport.harness(session, constants, catalog, visitor, cart)
    hold = await transport.cargo(session, cart)
    await world.grant_item(session, hold, INGOT, amount=20, quality=70, origin="сценарий теста")

    with pytest.raises(craft.NotEnough):
        await craft.plan(session, constants, catalog, body, NAILS, 1)


async def test_what_stands_is_not_a_material(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Put up, a thing works and is not spent (D-278). Without this the reach
    would eat the machine it was standing at.

    A standing **ingot** is a scene this test builds and the world cannot:
    `station.place` puts up only what `placeable` admits, and a station built
    in place is equipment too. So the filter is asked here of the column
    itself, which is what the engine reads -- and the window's own list of
    what lies loose (`estate.split`) drops only standing equipment and chests,
    which in a live world is the same set.
    """
    node, _, body = await _forge(session)
    yard = await world.node_container(session, node)
    await world.grant_item(
        session, yard, INGOT, amount=20, quality=70, origin="сценарий теста", installed=True
    )

    with pytest.raises(craft.NotEnough):
        await craft.plan(session, constants, catalog, body, NAILS, 1)


async def test_fuel_at_a_fuel_plant_is_the_plants_tank(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Fuel lying where a fuel plant stands is loaded, not stored (D-189).

    The hand is refused it (`storage.pick`), and the work must be refused it
    too: since D-248 the treasury pays for the haul, and a heap that could be
    poured in and crafted back out is a money pump.

    **Only the heap.** A chest standing beside the plant is not its bunker --
    `storage.take` hands its coal over without a word -- so the bar stops at
    the lid, or the work would be narrower than the hand for no reason.
    """
    node, _, body = await _forge(session)
    yard = await world.node_container(session, node)
    #: The class, and a member of it: `fuel_plant` is a thing class (D-215),
    #: and what stands in a yard is one of its machines.
    await world.grant_item(
        session,
        yard,
        world.station_names(energy.FUEL_PLANT)[0],
        origin="сценарий теста",
        installed=True,
    )
    fuel = next(iter(constants[R.ENERGY_FUEL_ENERGY]))
    await world.grant_item(session, yard, fuel, amount=50, origin="сценарий теста")
    chest = await world.grant_item(
        session, yard, CHEST, quality=60, origin="сценарий теста", installed=True
    )
    inside = await storage.inside(session, chest)
    await world.grant_item(session, inside, fuel, amount=50, origin="сценарий теста")

    reached = await reach.at_work(session, constants, catalog, body)
    assert reached.pile == yard.id, "куча станции — двор узла"
    assert reached.of(catalog, INGOT) != reached.carried, "прочее со двора берётся"
    #: The yard is out for the fuel, the chest inside it is not.
    for_fuel = set(reached.of(catalog, fuel))
    assert yard.id not in for_fuel, "куча у станции — её бак, не склад"
    assert inside.id in for_fuel, "сундук рядом со станцией — не бункер"


# --- the shared reach is a shared race ----------------------------------------


async def test_two_batches_over_one_heap_leave_one_refused(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pocket belonged to one body; a yard and a chest belong to everybody
    entitled (D-315).

    Nobody's land, where everybody may build (D-198), is the shortest way to
    two masters over one heap. Without the row lock both read the same ingots,
    both write off the same ingots, and the world ends the minute with two
    batches paid for once.
    """
    _slow(monkeypatch, power, "draw")
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.common.{stamp}", "Общий двор", area_m2=200)
    yard = await world.node_container(session, node)
    await world.grant_item(
        session, yard, FORGE, quality=60, origin="сценарий теста", installed=True
    )

    masters = []
    for number in range(2):
        who = await world.create_identity(session, f"Мастер-{stamp}-{number}")
        await world.learn(session, who, NAILS)
        masters.append((await world.print_body(session, who, node)).id)

    #: Exactly one batch's worth on the floor: what the first takes, the second
    #: must not find.
    per_batch = craft.procedure(catalog, NAILS).per_unit[INGOT]
    await world.grant_item(
        session, yard, INGOT, amount=per_batch, quality=70, origin="сценарий теста"
    )
    await session.commit()

    async def forge(body_id: uuid.UUID) -> None:
        async with factory() as db, db.begin():
            body = await db.get(Body, body_id)
            assert body is not None
            await craft.start(db, constants, catalog, body, NAILS, 1)

    outcomes = await asyncio.gather(*(forge(one) for one in masters), return_exceptions=True)
    refused = [one for one in outcomes if isinstance(one, craft.NotEnough)]
    assert len(refused) == 1, f"вторая партия должна уйти ни с чем: {outcomes}"

    async with factory() as db:
        assert await _left(db, yard.id) == 0


# --- and the forecast still writes nothing ------------------------------------


async def test_the_forecast_makes_no_hold_and_no_chest(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The reach is walked by `craft.plan` too, and that is a read (CLAUDE.md).

    A hold and a chest's inside are both made on first need, and a forecast
    asking for them by the wrong door would furnish an empty wagon from a
    glance -- the family of leaks `test_reads.py` sweeps for.
    """
    node, _, body = await _forge(session)
    yard = await world.node_container(session, node)
    cart = await world.grant_item(session, yard, CART, amount=1, origin="сценарий теста")
    await transport.harness(session, constants, catalog, body, cart)
    await world.grant_item(
        session, yard, CHEST, quality=60, origin="сценарий теста", installed=True
    )
    await world.grant_item(session, yard, INGOT, amount=20, quality=70, origin="сценарий теста")
    await session.flush()

    await craft.plan(session, constants, catalog, body, NAILS, 1)

    holds = await session.scalar(
        select(func.count()).select_from(Container).where(Container.kind == ContainerKind.VEHICLE)
    )
    insides = await session.scalar(
        select(func.count()).select_from(Container).where(Container.kind == ContainerKind.STORAGE)
    )
    assert holds == 0, "прогноз завёл трюм пустой повозке"
    assert insides == 0, "прогноз завёл нутро пустому сундуку"
