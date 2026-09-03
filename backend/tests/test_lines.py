# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The lines of a hull (D-288).

Checked is what the plumbing rests on:

* a port with no line drinks from **any** installed vessel aboard, in every
  room; a line drawn narrows it to the named vessels, in the order given;
  drawn empty, the port is back to any;
* what stands on a line is a vessel **installed** aboard: a canister lying on
  the floor is luggage, the same canister put up is a tank's equal;
* a loose vessel, a vessel of another hull and a port the machine has not are
  refused by name, never written and ignored;
* the reading names ports, lines and rooms -- the console draws the rest;
* two hands plumbing one port at once do not collide on the unique pair;
* the hull is one building for its batteries and its generators too: a cell
  in the hold feeds the workshop, a panel off the grid charges it;
* two liquids are not mixed in one vessel.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ship_kit import CONSOLE, ENGINE, FUEL, LIFE, TANK, _equip, _laid, _port, _shipwright
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import battery, energy, liquid, oxygen, ship, station, storage, world
from src.models.identity import Body
from src.models.inventory import Item
from src.models.job import JobState
from src.models.ship import Ship
from src.models.world import Node
from src.units import amount_float

AIR = "oxygen"
WATER = "water"
CYLINDER = "oxygen_tank"
CANISTER = "canister"
BATTERY = "battery"
SOLAR = "solar_panel"


async def _hull(
    session: AsyncSession, constants: Constants, *, foundations: int = 1
) -> tuple[Ship, Body, Node]:
    """A ship in port, its owner standing at the bridge -- where lines are drawn from."""
    port = await _port(session)
    _, body = await _shipwright(session, port, foundations=foundations)
    vessel = await _laid(session, constants, body, port)
    connector = await session.get(Node, vessel.connector_node_id)
    await _equip(session, connector, CONSOLE)
    body.node_id = connector.id
    await session.flush()
    return vessel, body, connector


async def _room(session: AsyncSession, constants: Constants, body: Body, vessel: Ship) -> Node:
    """One more compartment, laid from where the body stands (D-202)."""
    job = await ship.extend(session, constants, body)
    await ship.keel_laid(session, job)
    job.state = JobState.DONE
    job.finished_at = job.run_at
    await session.flush()
    return (await ship.nodes_of(session, vessel))[-1]


async def _vessel(
    session: AsyncSession,
    node: Node,
    type_key: str,
    liquid_name: str,
    amount: float,
    *,
    installed: bool = True,
) -> Item:
    """A vessel in the room with a liquid in it. Installed by default: that is
    what a line stands on."""
    yard = await world.node_container(session, node)
    box = await world.grant_item(
        session, yard, type_key, quality=60, origin="тест", installed=installed
    )
    inside = await storage.inside(session, box)
    await world.grant_item(session, inside, liquid_name, amount=amount, quality=60, origin="тест")
    return box


async def _held(session: AsyncSession, box: Item) -> float:
    return sum(amount_float(one.amount) for one in await storage.content(session, box))


# --- where a port draws from --------------------------------------------------


async def test_a_port_without_a_line_drinks_from_any_installed_vessel(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The hull nobody plumbed behaves as it always did: the engines reach every
    installed vessel aboard, in every room, and a canister standing in the
    hold is as good as a tank."""
    vessel, body, connector = await _hull(session, constants, foundations=2)
    hold = await _room(session, constants, body, vessel)
    await _equip(session, connector, ENGINE)
    await _vessel(session, connector, TANK, FUEL, 50)
    await _vessel(session, hold, CANISTER, FUEL, 10)

    assert await ship.fuel_aboard(session, constants, catalog, vessel) == pytest.approx(60)
    burnt = await ship.spend_fuel(session, constants, catalog, vessel, 55)
    assert burnt == pytest.approx(55)
    assert await ship.fuel_aboard(session, constants, catalog, vessel) == pytest.approx(5)


async def test_a_line_narrows_the_port_to_the_named_vessels_in_order(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Drawn, a line is the whole of what the port reaches, in the order given;
    drawn empty, the port is back to any."""
    vessel, body, connector = await _hull(session, constants)
    engine = await _equip(session, connector, ENGINE)
    first = await _vessel(session, connector, TANK, FUEL, 50)
    second = await _vessel(session, connector, TANK, FUEL, 50)

    stood = await ship.set_lines(
        session, constants, catalog, body, vessel, engine, "fuel", [second]
    )
    assert stood == 1
    assert await ship.fuel_aboard(session, constants, catalog, vessel) == pytest.approx(50)
    await ship.spend_fuel(session, constants, catalog, vessel, 20)
    assert await _held(session, second) == pytest.approx(30)
    assert await _held(session, first) == pytest.approx(50), "бак не на линии не тронут"

    await ship.set_lines(session, constants, catalog, body, vessel, engine, "fuel", [first, second])
    assert await ship.fuel_aboard(session, constants, catalog, vessel) == pytest.approx(80)
    await ship.spend_fuel(session, constants, catalog, vessel, 60)
    assert await _held(session, first) == pytest.approx(0), "первый на линии пьётся первым"
    assert await _held(session, second) == pytest.approx(20)

    await ship.set_lines(session, constants, catalog, body, vessel, engine, "fuel", [])
    await _vessel(session, connector, CANISTER, FUEL, 5)
    assert await ship.fuel_aboard(session, constants, catalog, vessel) == pytest.approx(25), (
        "пустая линия — снова любая тара на борту"
    )


async def test_a_loose_vessel_is_luggage_and_an_installed_one_stands_on_the_line(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One word for what a line reaches -- `installed` -- and a vessel is
    placeable for exactly that reason (`station.placeable`)."""
    assert station.placeable(catalog, CANISTER) and station.placeable(catalog, CYLINDER)

    vessel, body, connector = await _hull(session, constants)
    await _equip(session, connector, ENGINE)
    can = await _vessel(session, connector, CANISTER, FUEL, 10, installed=False)
    assert await ship.fuel_aboard(session, constants, catalog, vessel) == 0, (
        "канистра на полу — груз"
    )
    can.installed = True
    await session.flush()
    assert await ship.fuel_aboard(session, constants, catalog, vessel) == pytest.approx(10)

    #: The same word for the air: a cylinder put up in the room is what the
    #: life support breathes; lying there it is a bottle in the way.
    await _equip(session, connector, LIFE)
    bottle = await _vessel(session, connector, CYLINDER, AIR, 3, installed=False)
    assert await oxygen.reserve(session, constants, catalog, vessel) == 0
    bottle.installed = True
    await session.flush()
    assert await oxygen.reserve(session, constants, catalog, vessel) == pytest.approx(3)


async def test_what_is_not_installed_on_this_hull_is_refused_on_a_line(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A loose vessel, another hull's vessel, a lying machine, a port that is
    not there: each refused by name, none written and ignored."""
    vessel, body, connector = await _hull(session, constants)
    engine = await _equip(session, connector, ENGINE)
    loose = await _vessel(session, connector, CANISTER, FUEL, 5, installed=False)
    with pytest.raises(ship.NotOnLine):
        await ship.set_lines(session, constants, catalog, body, vessel, engine, "fuel", [loose])

    _, _, far = await _hull(session, constants)
    theirs = await _vessel(session, far, TANK, FUEL, 5)
    with pytest.raises(ship.NotOnLine):
        await ship.set_lines(session, constants, catalog, body, vessel, engine, "fuel", [theirs])

    with pytest.raises(ship.NoSuchPort):
        await ship.set_lines(session, constants, catalog, body, vessel, engine, "oxygen", [])

    engine.installed = False
    await session.flush()
    with pytest.raises(ship.NotOnLine):
        await ship.set_lines(session, constants, catalog, body, vessel, engine, "fuel", [])


async def test_the_life_support_drinks_only_from_its_line(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The air port narrows like the fuel port: a cylinder off the line is not
    the crew's, however full."""
    vessel, body, connector = await _hull(session, constants)
    system = await _equip(session, connector, LIFE)
    first = await _vessel(session, connector, CYLINDER, AIR, 5)
    second = await _vessel(session, connector, CYLINDER, AIR, 4)
    assert await oxygen.reserve(session, constants, catalog, vessel) == pytest.approx(9)

    await ship.set_lines(session, constants, catalog, body, vessel, system, "oxygen", [first])
    assert await oxygen.reserve(session, constants, catalog, vessel) == pytest.approx(5)

    vessel.docked_node_id = None
    vessel.air_at = datetime.now(UTC) - timedelta(hours=1)
    await session.flush()
    await oxygen.tick_ships(session, constants, catalog)
    draw = constants[R.OXYGEN_CREW_DRAW]
    assert await _held(session, first) == pytest.approx(5 - draw, abs=0.01)
    assert await _held(session, second) == pytest.approx(4), "баллон не на линии не тронут"


# --- the reading ------------------------------------------------------------


async def test_the_reading_names_ports_lines_and_rooms(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Every machine with ports, every vessel a line may stand on, the lines
    between them -- and the room each stands in, by name (D-225: the client
    holds no names for rooms it is not standing in)."""
    vessel, body, connector = await _hull(session, constants, foundations=2)
    hold = await _room(session, constants, body, vessel)
    engine = await _equip(session, connector, ENGINE)
    system = await _equip(session, connector, LIFE)
    tank = await _vessel(session, hold, TANK, FUEL, 40)
    bottle = await _vessel(session, connector, CYLINDER, AIR, 3)
    await ship.set_lines(session, constants, catalog, body, vessel, engine, "fuel", [tank])

    seen = await ship.lines_view(session, constants, catalog, vessel)
    assert seen["ship"] == str(vessel.id)
    machines = {one["item"]: one for one in seen["machines"]}
    assert set(machines) == {str(engine.id), str(system.id)}
    fuel_port = next(p for p in machines[str(engine.id)]["ports"] if p["port"] == "fuel")
    assert fuel_port["lines"] == [str(tank.id)]
    assert FUEL in fuel_port["liquids"]
    (air_port,) = machines[str(system.id)]["ports"]
    assert air_port["port"] == "oxygen" and air_port["lines"] == [], "линии нет — любая"
    assert machines[str(engine.id)]["node"] == connector.key

    vessels = {one["item"]: one for one in seen["vessels"]}
    assert set(vessels) == {str(tank.id), str(bottle.id)}
    assert vessels[str(tank.id)]["node"] == hold.key
    assert vessels[str(tank.id)]["node_name"] == hold.name
    assert vessels[str(tank.id)]["holds"] == [{"goods": FUEL, "amount": 40.0}]
    assert vessels[str(bottle.id)]["holds"] == [{"goods": AIR, "amount": 3.0}]


# --- two hands on one port ------------------------------------------------------


async def test_two_hands_plumbing_one_port_do_not_collide(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Two orders on one port at once: the machine's row serialises them, and
    the last to write is the one that stands -- never a unique-pair error
    thrown at a player."""
    async with factory() as session, session.begin():
        vessel, body, connector = await _hull(session, constants)
        engine = await _equip(session, connector, ENGINE)
        first = await _vessel(session, connector, TANK, FUEL, 10)
        second = await _vessel(session, connector, TANK, FUEL, 10)
        ids = (vessel.id, body.id, engine.id, first.id, second.id)

    ready = asyncio.Barrier(2)

    async def plumb(order: tuple[int, int]) -> int:
        async with factory() as db, db.begin():
            own = await db.get(Ship, ids[0])
            me = await db.get(Body, ids[1])
            machine = await db.get(Item, ids[2])
            chosen = [await db.get(Item, ids[place]) for place in order]
            await ready.wait()
            return await ship.set_lines(db, constants, catalog, me, own, machine, "fuel", chosen)

    counts = await asyncio.gather(plumb((3, 4)), plumb((4, 3)))
    assert list(counts) == [2, 2]

    async with factory() as session:
        rows = await ship.lines.lines_of(session, ids[2], "fuel")
        assert [row.rank for row in rows] == [0, 1]
        assert {row.vessel_item_id for row in rows} == {ids[3], ids[4]}


# --- the hull is one building ---------------------------------------------------


async def test_a_cell_in_another_room_feeds_the_machine(
    session: AsyncSession, constants: Constants
) -> None:
    """Charge is a bus of the hull (D-288): a battery in the hold is behind
    every machine aboard, whichever compartment it stands in."""
    vessel, body, connector = await _hull(session, constants, foundations=2)
    hold = await _room(session, constants, body, vessel)
    yard = await world.node_container(session, hold)
    cell = await world.grant_item(session, yard, BATTERY, quality=60, origin="тест")
    cell.charge = Decimal(str(battery.capacity(constants)))
    cell.charged_at = datetime.now(UTC)
    await session.flush()

    assert [one.id for one in await battery.batteries_in(session, connector)] == [cell.id]
    assert await battery.drain_batteries(session, constants, connector, 100) == pytest.approx(100)
    assert float(cell.charge) == pytest.approx(battery.capacity(constants) - 100)


async def test_a_panel_off_the_grid_charges_the_hull(
    session: AsyncSession, constants: Constants
) -> None:
    """A panel aboard has no pool to fill: its hours go into the hull's cells,
    settled by its own stamp, and the same moment is never charged twice."""
    vessel, body, connector = await _hull(session, constants, foundations=2)
    hold = await _room(session, constants, body, vessel)
    panel = await _equip(session, connector, SOLAR)
    yard = await world.node_container(session, hold)
    cell = await world.grant_item(session, yard, BATTERY, quality=60, origin="тест")
    moment = datetime.now(UTC)
    cell.charge = Decimal(0)
    cell.charged_at = moment
    panel.charged_at = moment - timedelta(hours=1)
    await session.flush()

    rate = constants[R.ENERGY_SOLAR_RATE]
    assert await energy.tick_offgrid(session, constants, now=moment) == pytest.approx(
        rate, abs=0.01
    )
    assert float(cell.charge) == pytest.approx(rate, abs=0.01)
    assert await energy.tick_offgrid(session, constants, now=moment) == 0, (
        "тот же час дважды не идёт"
    )


# --- one liquid per vessel -----------------------------------------------------


async def test_two_liquids_are_not_mixed_in_one_vessel(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A tank of fuel with water in it is nonsense, not a reserve (D-288): the
    pour is refused, and the same liquid pours as before."""
    vessel, body, connector = await _hull(session, constants)
    pocket = await world.body_container(session, body)
    yard = await world.node_container(session, connector)

    water = await world.grant_item(session, yard, CANISTER, quality=60, origin="тест")
    await world.grant_item(
        session, await storage.inside(session, water), WATER, amount=10, quality=60, origin="тест"
    )
    fuel = await world.grant_item(session, pocket, CANISTER, quality=60, origin="тест")
    await world.grant_item(
        session, await storage.inside(session, fuel), FUEL, amount=10, quality=60, origin="тест"
    )
    with pytest.raises(liquid.LiquidError):
        await liquid.pour(session, constants, catalog, body, fuel, water)
    assert await _held(session, water) == pytest.approx(10), "ничего не долито"

    more = await world.grant_item(session, pocket, CANISTER, quality=60, origin="тест")
    await world.grant_item(
        session, await storage.inside(session, more), WATER, amount=5, quality=60, origin="тест"
    )
    _, poured = await liquid.pour(session, constants, catalog, body, more, water)
    assert poured == pytest.approx(5)
