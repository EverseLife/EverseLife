# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Oxygen: the second scale of survival, and only where there is no air
(D-233, D-234).

Checked is what the whole mechanic rests on:

* the question exists only where the **planet** says so. On Terra the reading is
  empty and nothing is ever spent -- the same shape the cold has;
* a hull breathes off the life support's **line** (D-288): what stands
  installed aboard, any of it by default, the named vessels when a line is
  drawn. No system -- nothing aboard is breathed; the line runs dry -- the
  crew has one settling of grace before it dies;
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

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import gear, oxygen, ship, storage, travel, world
from src.engine.ship import lines
from src.models.estate import Building
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.ship import Ship
from src.models.world import Layer, Node, Planet, Surface
from src.units import AMOUNT_SCALE, ROUND_AMOUNT, ROUND_REMAINDER, amount_float

AIR = "oxygen"
WATER = "water"
TANK = "fuel_tank"
CYLINDER = "oxygen_tank"
CANISTER = "canister"
CHEST = "chest"
SUIT = "heatproof_suit"
LIFE = "life_support_system"


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


async def _in_tank(session: AsyncSession, node: Node, what: str, amount: float) -> Item:
    """A liquid aboard lives in a vessel: a tank standing in the room, the liquid inside it."""
    yard = await world.node_container(session, node)
    tank = await world.grant_item(session, yard, TANK, quality=60, origin="тест")
    inside = await storage.inside(session, tank)
    await world.grant_item(session, inside, what, amount=amount, quality=60, origin="тест")
    return tank


async def _in_canister(
    session: AsyncSession, node: Node, what: str, amount: float, *, installed: bool = True
) -> Item:
    """A canister in the room, with a liquid in it. Installed, it stands on the
    lines like a tank (D-288); loose, it is luggage."""
    yard = await world.node_container(session, node)
    can = await world.grant_item(
        session, yard, CANISTER, quality=60, origin="тест", installed=installed
    )
    inside = await storage.inside(session, can)
    await world.grant_item(session, inside, what, amount=amount, quality=60, origin="тест")
    return can


async def _held(session: AsyncSession, box: Item) -> float:
    return sum(amount_float(one.amount) for one in await storage.content(session, box))


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


async def _plumb(session: AsyncSession, system: Item, *vessels: Item) -> None:
    """The life support's line, drawn by hand: a port without a line drinks
    from nothing (D-288 as amended 2026-09-04)."""
    await lines.replace(session, system, "oxygen", [one.id for one in vessels])


async def _system(session: AsyncSession, node: Node) -> Item:
    """A life support system standing in the room: what the air line hangs on (D-288)."""
    yard = await world.node_container(session, node)
    return await world.grant_item(session, yard, LIFE, quality=60, origin="тест")


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


# --- the hull breathes off the life support's line -----------------------------


async def test_the_life_support_breathes_the_crew_off_its_line(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The system drinks and makes nothing (D-288): an hour aboard costs the
    crew's draw out of the vessels on its line, and a tank of water beside
    them is not so much as looked at -- air is the electrolyser's to make.
    """
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, body, connector = await _hull(session, constants, port)
    system = await _system(session, connector)
    air = await _in_tank(session, connector, AIR, 10)
    water = await _in_tank(session, connector, WATER, 5000)
    await _plumb(session, system, air)

    vessel.docked_node_id = None
    vessel.air_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()

    breathed, dead = await oxygen.tick_ships(session, constants, catalog)
    draw = constants[R.OXYGEN_CREW_DRAW]
    assert dead == 0
    assert breathed == pytest.approx(draw, abs=0.01), "час на одного — расход одного"
    assert await oxygen.reserve(session, constants, catalog, vessel) == pytest.approx(
        10 - draw, abs=0.01
    )
    assert body.choking_since is None
    assert await _held(session, water) == pytest.approx(5000), (
        "вода не тронута: система не электролизёр"
    )


async def test_without_a_system_nothing_aboard_is_breathed(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A hull with oxygen in its tanks and no life support breathes none of it.

    The system is the one thing aboard that breathes for people (D-288), and
    casting off without one is refused for exactly this (`flight._leaving`);
    a hull sealed anyway -- down on Pyroxis, unmoored by a test -- suffocates
    its crew beside full tanks.
    """
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, body, connector = await _hull(session, constants, port)
    tank = await _in_tank(session, connector, AIR, 10)
    vessel.docked_node_id = None
    vessel.air_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()

    assert await oxygen.reserve(session, constants, catalog, vessel) == 0
    _, dead = await oxygen.tick_ships(session, constants, catalog)
    assert dead == 0, "первый счёт только ставит отсчёт"
    assert body.choking_since is not None

    vessel.air_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()
    _, dead = await oxygen.tick_ships(session, constants, catalog)
    assert dead == 1
    assert await _held(session, tank) == pytest.approx(10), (
        "баллоны полны: без системы их никто не пьёт"
    )


async def test_when_the_line_runs_dry_the_crew_dies_after_one_settling_of_grace(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A drop on the line, then nothing: the reserve is all there is, and after it, death.

    One settling of grace on purpose: a tick landing a second after the last
    unit was spent must not be indistinguishable from suffocation.
    """
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, body, connector = await _hull(session, constants, port)
    system = await _system(session, connector)
    await _plumb(session, system, await _in_tank(session, connector, AIR, 0.05))

    vessel.docked_node_id = None
    vessel.air_at = datetime.now(UTC) - timedelta(hours=2)
    await session.flush()

    _, dead = await oxygen.tick_ships(session, constants, catalog)
    assert dead == 0, "первый счёт только опустошает линию"
    assert await oxygen.reserve(session, constants, catalog, vessel) == pytest.approx(0, abs=0.01)
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
    system = await _system(session, connector)
    await _plumb(session, system, await _in_tank(session, connector, AIR, 20))
    body.node_id = port.id
    vessel.docked_node_id = None
    vessel.air_at = datetime.now(UTC) - timedelta(hours=10)
    await session.flush()

    await oxygen.tick_ships(session, constants, catalog)
    assert await oxygen.reserve(session, constants, catalog, vessel) == pytest.approx(20, abs=0.01)


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


async def test_the_gauge_reads_the_line_and_the_sky(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The level is what stands on the line, the rate is the crew's draw --
    and only under a sealed hull: at a Terran pier the hatch is open and
    nothing is spent (D-233). Oxygen no line reaches is not on the gauge,
    because it is not what the crew dies by.
    """
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, body, connector = await _hull(session, constants, port)
    system = await _system(session, connector)
    await _plumb(session, system, await _in_tank(session, connector, AIR, 10))
    await _in_canister(session, connector, AIR, 4, installed=False)

    open_hatch = await oxygen.gauge(session, constants, catalog, vessel, crew=1)
    assert open_hatch["sealed"] is False
    assert open_hatch["per_hour"] == 0
    assert open_hatch["units"] == pytest.approx(10), "канистра на полу — не запас"

    vessel.docked_node_id = None
    await session.flush()
    shut = await oxygen.gauge(session, constants, catalog, vessel, crew=2)
    assert shut["sealed"] is True
    assert shut["per_hour"] == pytest.approx(-2 * constants[R.OXYGEN_CREW_DRAW])


async def test_two_hulls_settling_together_do_not_drink_one_cylinder_twice(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The tick and a second tick land on one hull at once.

    The air on the line is a quantity of a shared thing: read without a lock,
    both passes would breathe the same units and the crew would live through
    an hour it did not live through.
    """
    async with factory() as session, session.begin():
        await _sphere(session, Planet.TERRA, airless=False)
        port = await _port(session)
        vessel, _, connector = await _hull(session, constants, port)
        system = await _system(session, connector)
        #: Air for exactly one hour of one person, and no more: the second
        #: pass must find the line dry rather than the reading it started from.
        await _plumb(
            session, system, await _in_tank(session, connector, AIR, constants[R.OXYGEN_CREW_DRAW])
        )
        vessel.docked_node_id = None
        vessel.air_at = datetime.now(UTC) - timedelta(hours=1)
        await session.flush()
        ship_id = vessel.id

    ready = asyncio.Barrier(2)

    async def breathe() -> float:
        async with factory() as db, db.begin():
            #: Both transactions open and looking at the same hull before either
            #: writes -- that is the window an unlocked settling drinks the line
            #: twice in. Nothing is written before the barrier on purpose: two
            #: writes to one row before it would simply deadlock and prove
            #: nothing about the code under test.
            await db.get(Ship, ship_id)
            await ready.wait()
            breathed, _ = await oxygen.tick_ships(db, constants, catalog)
            return breathed

    breathed = sum(await asyncio.gather(breathe(), breathe()))

    async with factory() as session:
        vessel = await session.get(Ship, ship_id)
        left = await oxygen.reserve(session, constants, catalog, vessel)
    assert left == pytest.approx(0, abs=0.01), "линия выпита вся"
    assert breathed == pytest.approx(constants[R.OXYGEN_CREW_DRAW], abs=0.01), (
        f"воздуха отчитано {breathed:.3f} при запасе {constants[R.OXYGEN_CREW_DRAW]:.3f}"
    )


async def test_the_line_reaches_an_installed_canister_as_readily_as_a_tank(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """What stands on a line is a vessel **installed** aboard (D-288), whatever
    its shape: a canister put up in the room is breathed like a tank, one
    lying in it is luggage -- and the word for the difference is on the
    thing itself, not in the depth of the stowage.
    """
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, body, connector = await _hull(session, constants, port)
    system = await _system(session, connector)
    #: Not one tank aboard: everything is in canisters, one put up, one lying.
    standing = await _in_canister(session, connector, AIR, 4)
    lying = await _in_canister(session, connector, AIR, 9, installed=False)
    #: Both on the line: the lying one is skipped by the line, not by the list.
    await _plumb(session, system, standing, lying)
    vessel.docked_node_id = None
    await session.flush()

    assert await oxygen.reserve(session, constants, catalog, vessel) == pytest.approx(4, abs=0.01)

    vessel.air_at = datetime.now(UTC) - timedelta(hours=2)
    await session.flush()
    await oxygen.tick_ships(session, constants, catalog)
    assert await _held(session, standing) == pytest.approx(
        4 - 2 * constants[R.OXYGEN_CREW_DRAW], abs=0.01
    ), "система пьёт установленную канистру"
    assert await _held(session, lying) == pytest.approx(9), "лежащая — груз, её не пьют"


async def test_a_canister_packed_into_a_chest_is_stowed_cargo(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A vessel is on the line only when it stands installed in a room.

    Packed into a chest it is luggage, and lying on the floor it is luggage
    too -- pinned here because it is exactly the kind of boundary a crew finds
    out about by dying: the spare oxygen was put away tidily.
    """
    await _sphere(session, Planet.TERRA, airless=False)
    port = await _port(session)
    vessel, _, connector = await _hull(session, constants, port)
    system = await _system(session, connector)
    yard = await world.node_container(session, connector)

    chest = await world.grant_item(session, yard, CHEST, quality=60, origin="тест")
    packed = await storage.inside(session, chest)
    can = await world.grant_item(session, packed, CANISTER, quality=60, origin="тест")
    #: On the line from the start: the line is obeyed only where the can stands.
    await _plumb(session, system, can)
    await world.grant_item(
        session, await storage.inside(session, can), AIR, amount=9, quality=60, origin="тест"
    )

    assert await oxygen.reserve(session, constants, catalog, vessel) == 0, (
        "убранное в сундук — груз, а не запас"
    )

    #: Out of the chest and onto the floor: still luggage.
    can.container_id = yard.id
    await session.flush()
    assert await oxygen.reserve(session, constants, catalog, vessel) == 0, (
        "лежащее на полу — тоже груз"
    )

    #: Put up in the room, it stands on the line.
    can.installed = True
    await session.flush()
    assert await oxygen.reserve(session, constants, catalog, vessel) == pytest.approx(9, abs=0.01)
