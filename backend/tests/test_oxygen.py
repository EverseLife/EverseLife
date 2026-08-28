# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Oxygen: the second scale of survival, and only where there is no air
(D-233, D-234).

Checked is what the whole mechanic rests on:

* the question exists only where the **planet** says so. On Terra the reading is
  empty and nothing is ever spent -- the same shape the cold has;
* a hull breathes its **tanks**, and the life support fills them out of water
  and charge. Enough of both and the reserve holds; run out of either and it
  falls, and the crew has one settling of grace before it dies;
* a body outside breathes a **cylinder through a suit**, and neither half alone
  is worth anything: a bare body dies with a full bag;
* the step into a place with nothing to breathe is refused **before** it is
  taken, so nobody dies of a door;
* two settlings of one body in the same second spend one cylinder's worth, not
  two: the reserve is a quantity of a shared thing, and locks are the whole
  reason it stays one (CLAUDE.md).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants, current
from src.constants import registry as R
from src.engine import energy, gear, oxygen, ship, storage, travel, world
from src.models.estate import Building
from src.models.identity import Body, BodyState
from src.models.ship import Ship
from src.models.world import Layer, Node, Planet, Surface

AIR = "Кислород"
WATER = "Вода"
TANK = "Топливный бак"
CYLINDER = "Кислородный баллон"
SUIT = "Жаростойкий скафандр"
BATTERY = "Аккумулятор"
LIFE = "Система жизнеобеспечения"
ENGINE = "Двигатель I класса"


async def _sphere(session: AsyncSession, planet: Planet, *, airless: bool) -> Node:
    """The planet's own node, where its properties live -- climate, air, landing."""
    node = await world.create_node(
        session,
        planet.value,
        planet.value.title(),
        area_m2=1,
        planet=planet,
        layer=Layer.SPACE,
        properties={oxygen.AIRLESS: True} if airless else {},
    )
    return node


async def _ground(session: AsyncSession, planet: Planet, sphere: Node, name="Поле") -> Node:
    node = await world.create_node(
        session,
        f"{planet.value}.field.{uuid.uuid4().hex[:8]}",
        name,
        area_m2=400,
        planet=planet,
        layer=Layer.PLANET,
        parent=sphere,
    )
    session.add(Building(node_id=node.id, area_m2=400))
    await session.flush()
    return node


async def _port(session: AsyncSession, planet=Planet.TERRA) -> Node:
    node = await world.create_node(
        session,
        f"{planet.value}.port.{uuid.uuid4().hex[:8]}",
        "Космодром",
        area_m2=400,
        planet=planet,
    )
    session.add(Building(node_id=node.id, area_m2=400))
    await session.flush()
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "Космическая верфь", quality=60, origin="тест")
    return node


async def _person(session: AsyncSession, node: Node) -> Body:
    identity = await world.create_identity(session, f"Дышащий-{uuid.uuid4().hex[:6]}")
    return await world.print_body(session, identity, node)


async def _in_tank(session: AsyncSession, node: Node, what: str, amount: float) -> None:
    """A liquid aboard lives in a tank: the tank in the room, the liquid inside it."""
    yard = await world.node_container(session, node)
    tank = await world.grant_item(session, yard, TANK, quality=60, origin="тест")
    inside = await storage.inside(session, tank)
    await world.grant_item(session, inside, what, amount=amount, quality=60, origin="тест")


async def _cylinder(session: AsyncSession, body: Body, amount: float) -> None:
    """A cylinder in the hands, with air in it. Nothing is breathed from the bag itself."""
    pocket = await world.body_container(session, body)
    bottle = await world.grant_item(session, pocket, CYLINDER, quality=60, origin="тест")
    inside = await storage.inside(session, bottle)
    await world.grant_item(session, inside, AIR, amount=amount, quality=60, origin="тест")


async def _suited(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body
) -> None:
    """A suit on the body, not in the bag: the bag connects nothing (D-234)."""
    pocket = await world.body_container(session, body)
    suit = await world.grant_item(session, pocket, SUIT, quality=60, origin="тест")
    await gear.equip(session, constants, catalog, body, suit)


async def _charged(session: AsyncSession, node: Node, cells: int) -> None:
    """Batteries standing in the room, full. Charge is written, not granted."""
    yard = await world.node_container(session, node)
    pile = await world.grant_item(session, yard, BATTERY, amount=cells, quality=60, origin="тест")
    pile.charge = Decimal(str(energy.capacity(current())))
    pile.charged_at = datetime.now(UTC)
    await session.flush()


async def _hull(session: AsyncSession, constants: Constants, port: Node) -> tuple[Ship, Body, Node]:
    """A ship in port with its owner standing aboard."""
    identity = await world.create_identity(session, f"Корабел-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, port)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "Основа узла корабля", origin="тест")
    job = await ship.found(session, constants, body, "Заря")
    await ship.keel_laid(session, job)
    vessel = (await ship.ships_of(session, identity.id))[-1]
    connector = await session.get(Node, vessel.connector_node_id)
    body.node_id = connector.id
    await session.flush()
    return vessel, body, connector


# --- where the question arises at all -----------------------------------------


async def test_terra_breathes_and_pyroxis_does_not(session: AsyncSession) -> None:
    """The planet decides, and it decides in the world rather than in a constant."""
    terra = await _sphere(session, Planet.TERRA, airless=False)
    pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
    field = await _ground(session, Planet.TERRA, terra)
    rock = await _ground(session, Planet.PYROXIS, pyroxis, name="Чёрное поле")

    assert await oxygen.free_air(session, field) is True
    assert await oxygen.free_air(session, rock) is False


async def test_a_hull_is_sealed_in_flight_and_open_in_port(
    session: AsyncSession, constants: Constants
) -> None:
    """In port under a sky that has air the hatch may as well be open (D-233)."""
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, _, connector = await _hull(session, constants, port)

    assert await oxygen.sealed(session, vessel) is False
    assert await oxygen.free_air(session, connector) is True

    vessel.docked_node_id = None
    await session.flush()
    assert await oxygen.sealed(session, vessel) is True
    assert await oxygen.free_air(session, connector) is False


async def test_a_terran_reading_is_empty(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """No air scale where there is air: no key in the look, as with the cold."""
    terra = await _sphere(session, Planet.TERRA, airless=False)
    field = await _ground(session, Planet.TERRA, terra)
    body = await _person(session, field)
    assert await oxygen.view(session, constants, catalog, body, field) is None


# --- the hull breathes its tanks ----------------------------------------------


async def test_life_support_makes_the_air_the_crew_breathes(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Water and charge in, air out: with both, the reserve holds (D-233, D-234)."""
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, body, connector = await _hull(session, constants, port)
    yard = await world.node_container(session, connector)
    await world.grant_item(session, yard, LIFE, quality=60, origin="тест")
    await _charged(session, connector, cells=8)
    await _in_tank(session, connector, WATER, 5000)
    await _in_tank(session, connector, AIR, 10)

    vessel.docked_node_id = None
    vessel.air_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()

    before = await oxygen.reserve(session, vessel)
    made, dead = await oxygen.tick_ships(session, constants, catalog)
    assert dead == 0
    assert made > 0, "жизнеобеспечение работало"
    assert await oxygen.reserve(session, vessel) == pytest.approx(before, abs=0.01), (
        "воздух сделан ровно на дыхание: баки не тронуты"
    )


async def test_without_water_the_tanks_drain_and_then_the_crew_dies(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """No water, no charge -- the reserve is all there is, and after it, death.

    One settling of grace on purpose: a tick landing a second after the last
    unit was spent must not be indistinguishable from suffocation.
    """
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, body, connector = await _hull(session, constants, port)
    await _in_tank(session, connector, AIR, 0.05)

    vessel.docked_node_id = None
    vessel.air_at = datetime.now(UTC) - timedelta(hours=2)
    await session.flush()

    _, dead = await oxygen.tick_ships(session, constants, catalog)
    assert dead == 0, "первый счёт только опустошает баки"
    assert await oxygen.reserve(session, vessel) == pytest.approx(0, abs=0.01)
    assert body.choking_since is not None, "отсчёт до удушья пошёл"

    vessel.air_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()
    _, dead = await oxygen.tick_ships(session, constants, catalog)
    assert dead == 1
    await session.refresh(body)
    assert body.state is BodyState.DEAD


async def test_an_empty_hull_spends_nothing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Nobody aboard breathes nothing: a hull in flight arrives as it left."""
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, body, connector = await _hull(session, constants, port)
    await _in_tank(session, connector, AIR, 20)
    body.node_id = port.id
    vessel.docked_node_id = None
    vessel.air_at = datetime.now(UTC) - timedelta(hours=10)
    await session.flush()

    await oxygen.tick_ships(session, constants, catalog)
    assert await oxygen.reserve(session, vessel) == pytest.approx(20, abs=0.01)


# --- a body outside breathes a cylinder through a suit -------------------------


async def test_a_bare_body_breathes_nothing_however_many_cylinders(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The suit is the connection (D-234): without one the bag is luggage."""
    pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
    rock = await _ground(session, Planet.PYROXIS, pyroxis)
    body = await _person(session, rock)
    await _cylinder(session, body, 100)
    body.air_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()

    breath = await oxygen.settle(session, constants, catalog, body)
    assert breath.uncovered > 0, "дышать нечем: скафандра нет"
    assert await oxygen.carried(session, body) == pytest.approx(100, abs=0.01), "баллон не тронут"


async def test_a_suited_body_spends_its_cylinder(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Outside the draw is `oxygen.body_draw`, and it comes out of the bottle."""
    pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
    rock = await _ground(session, Planet.PYROXIS, pyroxis)
    body = await _person(session, rock)
    await _cylinder(session, body, 6)
    await _suited(session, constants, catalog, body)
    body.air_at = datetime.now(UTC) - timedelta(hours=2)
    await session.flush()

    breath = await oxygen.settle(session, constants, catalog, body)
    spent = 2 * constants[R.OXYGEN_BODY_DRAW]
    assert breath.uncovered == 0
    assert breath.left == pytest.approx(6 - spent, abs=0.01)


async def test_the_step_into_vacuum_is_refused_before_it_is_taken(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Death by ignorance in one click is not this world's way (D-233)."""
    terra = await _sphere(session, Planet.TERRA, airless=False)
    pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
    one = await _ground(session, Planet.PYROXIS, pyroxis, name="Плато")
    other = await _ground(session, Planet.PYROXIS, pyroxis, name="Чёрное поле")
    await travel.connect(session, one, other, base_seconds=60, surface=Surface.TRAIL)
    body = await _person(session, one)

    with pytest.raises(oxygen.NoAir):
        await travel.depart(session, constants, body, other)

    await _suited(session, constants, catalog, body)
    await _cylinder(session, body, 6)
    assert await travel.depart(session, constants, body, other) is not None
    assert terra is not None


# --- the reserve is a quantity, and two hands must not spend it twice ----------


async def test_two_settlings_in_one_second_spend_one_cylinder_once(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The tick and a command land together on one body.

    Without the row lock both read the same stamp, both charge the same stretch
    and the cylinder loses twice what an hour costs -- systematically in the
    world's favour, and invisible until somebody suffocates early.
    """
    async with factory() as session, session.begin():
        pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
        rock = await _ground(session, Planet.PYROXIS, pyroxis)
        body = await _person(session, rock)
        await _cylinder(session, body, 6)
        await _suited(session, constants, catalog, body)
        body.air_at = datetime.now(UTC) - timedelta(hours=2)
        await session.flush()
        body_id = body.id

    ready = asyncio.Barrier(2)

    async def settle() -> None:
        async with factory() as db, db.begin():
            mine = await db.get(Body, body_id)
            await ready.wait()
            await oxygen.settle(db, constants, catalog, mine)

    await asyncio.gather(settle(), settle())

    async with factory() as session:
        body = await session.get(Body, body_id)
        left = await oxygen.carried(session, body)
    spent = 2 * constants[R.OXYGEN_BODY_DRAW]
    assert left == pytest.approx(6 - spent, abs=0.01), (
        f"два счёта списали {6 - left:.2f} вместо {spent:.2f}"
    )


async def test_refilling_gives_the_grace_back(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A body that ran dry once and breathed again is not marked for death.

    Without this the mark stays for ever, and the next stretch the cylinder only
    half covers kills on the spot -- with none of the settling of warning the
    module promises.
    """
    pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
    rock = await _ground(session, Planet.PYROXIS, pyroxis)
    body = await _person(session, rock)
    await _suited(session, constants, catalog, body)
    body.air_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()

    assert await oxygen.tick_bodies(session, constants, catalog) == 0
    assert body.choking_since is not None, "первый пустой счёт ставит отсчёт"

    await _cylinder(session, body, 6)
    body.air_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()
    assert await oxygen.tick_bodies(session, constants, catalog) == 0
    assert body.choking_since is None, "заправился — отсрочка вернулась"


async def test_the_gauge_says_the_tanks_are_going_down_when_there_is_no_water(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A system with nothing to run on makes nothing, and the reading must say so.

    This is the one refusal the whole scale exists for: a full bar and a calm
    rate right up to the tick that starts killing would be exactly the death by
    surprise the module is built against.
    """
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, body, connector = await _hull(session, constants, port)
    yard = await world.node_container(session, connector)
    await world.grant_item(session, yard, LIFE, quality=60, origin="тест")
    await _charged(session, connector, cells=8)
    await _in_tank(session, connector, AIR, 10)
    vessel.docked_node_id = None
    await session.flush()

    dry = await oxygen.gauge(session, constants, catalog, vessel, crew=1)
    assert dry["sealed"] is True
    assert dry["per_hour"] < 0, "воды нет: баки идут вниз, и шкала это говорит"

    await _in_tank(session, connector, WATER, 5000)
    wet = await oxygen.gauge(session, constants, catalog, vessel, crew=1)
    assert wet["per_hour"] == 0, "вода есть: система покрывает дыхание ровно"


async def test_two_hulls_settling_together_do_not_drink_one_tank_twice(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The tick and a second tick land on one hull at once.

    The water a system turns into air is a quantity of a shared thing: read
    without a lock, both passes would report air neither of them made and the
    crew would live through an hour it did not live through.
    """
    async with factory() as session, session.begin():
        await _sphere(session, Planet.TERRA, airless=False)
        port = await _port(session)
        vessel, _, connector = await _hull(session, constants, port)
        yard = await world.node_container(session, connector)
        await world.grant_item(session, yard, LIFE, quality=60, origin="тест")
        await _charged(session, connector, cells=40)
        #: Water for exactly one hour of one system, and no more: the second
        #: pass must find the tank empty rather than the reading it started from.
        await _in_tank(session, connector, WATER, 10 * constants[R.OXYGEN_CREW_DRAW])
        vessel.docked_node_id = None
        vessel.air_at = datetime.now(UTC) - timedelta(hours=1)
        await session.flush()
        ship_id = vessel.id

    ready = asyncio.Barrier(2)

    async def breathe() -> float:
        async with factory() as db, db.begin():
            #: Both transactions open and looking at the same hull before either
            #: writes -- that is the window an unlocked settling drinks the tank
            #: twice in. Nothing is written before the barrier on purpose: two
            #: writes to one row before it would simply deadlock and prove
            #: nothing about the code under test.
            await db.get(Ship, ship_id)
            await ready.wait()
            made, _ = await oxygen.tick_ships(db, constants, catalog)
            return made

    made = sum(await asyncio.gather(breathe(), breathe()))

    async with factory() as session:
        vessel = await session.get(Ship, ship_id)
        water = await oxygen.water_aboard(session, vessel)
    assert water == pytest.approx(0, abs=0.01), "вода списана вся"
    assert made == pytest.approx(constants[R.OXYGEN_CREW_DRAW], abs=0.01), (
        f"воздуха отчитано {made:.3f} при воде на {constants[R.OXYGEN_CREW_DRAW]:.3f}"
    )
