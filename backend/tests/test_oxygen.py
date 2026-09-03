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
from src.units import AMOUNT_SCALE, ROUND_AMOUNT, ROUND_REMAINDER

AIR = "oxygen"
WATER = "water"
TANK = "fuel_tank"
CYLINDER = "oxygen_tank"
CANISTER = "canister"
CHEST = "chest"
SUIT = "heatproof_suit"
BATTERY = "battery"
LIFE = "life_support_system"
ENGINE = "engine_class_1"


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
    await world.grant_item(session, yard, "space_shipyard", quality=60, origin="тест")
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


async def _in_canister(session: AsyncSession, node: Node, what: str, amount: float) -> None:
    """A canister standing in the room, with a liquid in it. Not a tank."""
    yard = await world.node_container(session, node)
    can = await world.grant_item(session, yard, CANISTER, quality=60, origin="тест")
    inside = await storage.inside(session, can)
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
    await world.grant_item(session, pocket, "ship_node_foundation", origin="тест")
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


def test_the_air_grid_is_one_number_in_two_places() -> None:
    """`AMOUNT_SCALE` and `ROUND_AMOUNT` say the same grid, one as a scale and
    one as places. Move either alone and every figure floored to the amount
    grid is floored to the wrong one -- silently, and always downwards.
    """
    assert AMOUNT_SCALE == 10**ROUND_AMOUNT


def test_the_air_debt_is_kept_at_the_scale_it_is_written_with() -> None:
    """`ROUND_REMAINDER` and the debt column are one number in two places."""
    assert Body.__table__.c.air_owed.type.scale == ROUND_REMAINDER


async def test_breathing_often_costs_the_same_air_as_breathing_once(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A body settled every second on Pyroxis spends the hour it stood there.

    Air is split into thousandths, and at `oxygen.body_draw` a stretch under
    seven seconds cannot be taken out of a cylinder. The breath used to be
    asked for, rounded away and forgotten, and every step settles the
    breathing -- the gangway off a landed ship is seven tenths of a second --
    so a suited body could stand on an airless world for ever on a drop.
    """
    pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
    rock = await _ground(session, Planet.PYROXIS, pyroxis)
    often = await _person(session, rock)
    once = await _person(session, rock)
    started = datetime.now(UTC)
    for who in (often, once):
        await _cylinder(session, who, 6)
        await _suited(session, constants, catalog, who)
        who.air_at = started
    await session.flush()

    #: Two seconds apart, well under the seven a thousandth of air buys.
    steps, every = 60, timedelta(seconds=2)
    for tick in range(1, steps + 1):
        await oxygen.settle(session, constants, catalog, often, now=started + every * tick)
    await oxygen.settle(session, constants, catalog, once, now=started + every * steps)

    spent = constants[R.OXYGEN_BODY_DRAW] * (steps * every) / timedelta(hours=1)
    left_once = await oxygen.carried(session, once)
    left_often = await oxygen.carried(session, often)
    #: The two minutes really cost something, or the test proves nothing.
    assert 6 - left_once == pytest.approx(spent, abs=0.001)
    #: And the busy body paid exactly what the quiet one paid.
    assert left_often == pytest.approx(left_once, abs=0.001)


async def test_a_body_out_of_air_cannot_step_onto_airless_ground(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """What keeps a walker from outrunning suffocation is the door, not the debt.

    Every step settles the breathing, and the tick settles it again -- and the
    tick clears the mark of choking whenever its own stretch came up covered.
    A step's stretch is far too short to come up short, so a walker could in
    principle keep the reaper at bay by walking. It cannot, but not for the
    reason the debt suggests: `require_air` refuses the step outright when the
    bottle is empty, so a body with nothing to breathe cannot take one. This
    pins that door, since removing it would make the hole real.
    """
    pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
    rock = await _ground(session, Planet.PYROXIS, pyroxis)
    beyond = await _ground(session, Planet.PYROXIS, pyroxis, name="Дальше")
    await travel.connect(session, rock, beyond, base_seconds=1, surface=Surface.PAVED)
    body = await _person(session, rock)
    await _suited(session, constants, catalog, body)
    await _cylinder(session, body, 0.001)
    body.air_at = datetime.now(UTC)
    await session.flush()

    #: Breathe the drop away, then try to walk.
    await oxygen.settle(session, constants, catalog, body, now=body.air_at + timedelta(minutes=1))
    #: By the key, not merely by the class: `require_air` has three refusals,
    #: and the one this pins is the empty bottle. The other two would satisfy
    #: the same assertion while leaving the hole open -- and which of them
    #: answers depends on the length of the road, which is this test's choice.
    with pytest.raises(oxygen.NoAir) as refused:
        await travel.depart(session, constants, body, beyond)
    assert refused.value.key == "oxygen-tanks-empty"


async def test_stepping_aboard_does_not_forgive_the_air_owed_outside(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The gangway is not a way of breathing free.

    A ship landed on bare ground makes an edge of seven tenths of a second,
    and every step settles the breathing. If what the ground breathed but
    could not be charged for rode on the stamp, arriving in air would move
    that stamp to now and forgive it -- so a body stepping off and back on
    would pay nothing, for ever. The debt is the body's own, and it survives
    the crossing.
    """
    pyroxis = await _sphere(session, Planet.PYROXIS, airless=True)
    rock = await _ground(session, Planet.PYROXIS, pyroxis)
    #: The other side of the gangway is simply somewhere with air: `settle`
    #: asks the node it stands on and nothing else.
    terra = await _sphere(session, Planet.TERRA, airless=False)
    inside = await _ground(session, Planet.TERRA, terra, name="Палуба")
    body = await _person(session, rock)
    await _cylinder(session, body, 6)
    await _suited(session, constants, catalog, body)
    started = datetime.now(UTC)
    body.air_at = started
    await session.flush()

    #: Off the ship and back, twenty times: two seconds on the ground each
    #: time, and two aboard, where breathing is free.
    moment = started
    for _ in range(20):
        moment += timedelta(seconds=2)
        body.node_id = rock.id
        await oxygen.settle(session, constants, catalog, body, now=moment)
        moment += timedelta(seconds=2)
        body.node_id = inside.id
        await oxygen.settle(session, constants, catalog, body, now=moment)
    body.node_id = rock.id

    #: Forty seconds on the rock, and every one of them paid for.
    spent = constants[R.OXYGEN_BODY_DRAW] * timedelta(seconds=40) / timedelta(hours=1)
    left = await oxygen.carried(session, body)
    assert 6 - left == pytest.approx(spent, abs=0.001)


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


async def test_the_life_support_reaches_a_canister_as_readily_as_a_tank(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Air and its water come from any vessel **standing in a compartment**.

    Deliberately wider than the fuel a passage burns (D-230): the engines are
    plumbed to the tanks, the life support is a machine somebody carries a
    canister to. A crew suffocating beside a hold full of oxygen because the
    bottles were the wrong shape would be a bug with an explanation. Where that
    reach stops is pinned by the test below.
    """
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, body, connector = await _hull(session, constants, port)
    yard = await world.node_container(session, connector)
    await world.grant_item(session, yard, LIFE, quality=60, origin="тест")
    await _charged(session, connector, cells=8)
    #: Not one tank aboard: everything is in canisters.
    await _in_canister(session, connector, AIR, 4)
    await _in_canister(session, connector, WATER, 5000)
    vessel.docked_node_id = None
    await session.flush()

    assert await oxygen.reserve(session, vessel) == pytest.approx(4, abs=0.01)
    assert await oxygen.water_aboard(session, vessel) == pytest.approx(5000, abs=0.01)
    reading = await oxygen.gauge(session, constants, catalog, vessel, crew=1)
    assert reading["per_hour"] == 0, "воду из канистры система видит и покрывает дыхание"

    #: And spends it: with the water gone the canister of air is the reserve.
    vessel.air_at = datetime.now(UTC) - timedelta(hours=2)
    await session.flush()
    await oxygen.tick_ships(session, constants, catalog)
    assert await oxygen.water_aboard(session, vessel) < 5000, "вода из канистры израсходована"
    assert await oxygen.reserve(session, vessel) == pytest.approx(4, abs=0.01), (
        "воздуха хватило: баллоны трогать не пришлось"
    )


async def test_a_canister_packed_into_a_chest_is_stowed_cargo(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The reach is one level: what stands in the room, and what is inside it.

    The rule is the same one step along -- nothing rummages through luggage --
    and it is pinned here because it is exactly the kind of boundary a crew
    finds out about by dying: the spare oxygen was put away tidily.
    """
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, _, connector = await _hull(session, constants, port)
    yard = await world.node_container(session, connector)

    chest = await world.grant_item(session, yard, CHEST, quality=60, origin="тест")
    packed = await storage.inside(session, chest)
    can = await world.grant_item(session, packed, CANISTER, quality=60, origin="тест")
    await world.grant_item(
        session, await storage.inside(session, can), AIR, amount=9, quality=60, origin="тест"
    )

    assert await oxygen.reserve(session, vessel) == 0, "убранное в сундук — груз, а не запас"

    #: The same canister standing in the room is the reserve.
    can.container_id = yard.id
    await session.flush()
    assert await oxygen.reserve(session, vessel) == pytest.approx(9, abs=0.01)
