# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The air machine on the hull's lines (D-288 wave 4, D-340).

Checked is what the owner decided and what the wave had to settle:

* the ports are the air recipe's liquids -- water in, oxygen out, hydrogen
  vented -- and the reactor that stands in for the electrolyser drinks its
  lubricant through a port of its own; the reading says which way each runs;
* a manual batch aboard drinks its water from its line and nowhere else,
  pours its oxygen into its outlet in line order and its hydrogen into its
  vent, and -- from a sealed hull -- what the vent cannot take goes overboard
  without a spill;
* a port without a line and an outlet without room refuse the batch before
  anything is spent;
* the reactor aboard a sealed hull works by itself on its lines, keeps working
  with its hydrogen going overboard, and stands for a full outlet, a dry line
  or flat cells -- telling the crew once when the reason appears or changes;
* under a sky with air the hull keeps its hydrogen in its vent line only
  (`test_lines_vent.py`), and on the ground it burns in a flare (`test_vent.py`);
* the catch-up of 2026-09-04 never plumbs the new ports;
* two ticks of one reactor, and a tick racing a hand into one tank, keep the
  amounts whole.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
from lines_kit import (
    AIR,
    CANISTER,
    CYLINDER,
    ELECTROLYSER,
    HYDROGEN,
    LUBRICANT,
    REACTOR,
    WATER,
    _cells,
    _empty,
    _events,
    _finish,
    _held,
    _hull,
    _learned,
    _room,
    _seal,
    _stacks,
    _vessel,
)
from ship_kit import TANK, _equip
from src.constants import Catalog, Constants
from src.engine import automat, craft, liquid, ship, stock, storage, world
from src.engine.craft import plumbing
from src.engine.ship import lines
from src.models.automat import Automat as AutomatRow
from src.models.event import EventKind
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Node

UNIT = "hydroponic_unit"


# --- the ports ----------------------------------------------------------------


async def test_the_air_machines_have_the_recipes_ports(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Ports are the air recipe's liquids, by data (D-288, D-340): the
    electrolyser drinks water and gives oxygen and hydrogen; the reactor that
    stands in for it drinks its lubricant too; the hydroponics gives oxygen."""
    ports = {
        kind: {port.name: port.way for port in lines.ports_of(constants, catalog, kind)}
        for kind in (ELECTROLYSER, REACTOR, UNIT)
    }
    assert ports[ELECTROLYSER] == {WATER: "in", AIR: "out", HYDROGEN: "vent"}
    assert ports[REACTOR] == {WATER: "in", AIR: "out", HYDROGEN: "vent", "lube": "in"}
    assert ports[UNIT] == {AIR: "vent"}, "грядка не встаёт из-за полного баллона"
    #: The electrolyser making oxidiser is not plumbed: only the air is.
    assert lines.plumbed_for(constants, catalog, ELECTROLYSER, "oxidizer") == ()
    assert catalog.recipes.byproduct_of(AIR) == {HYDROGEN: 2.0}
    #: Matter does not appear (D-228): the water is the oxygen and the hydrogen.
    water = catalog.recipes.recipe(AIR).amounts[WATER] * catalog.recipes.mass_of(WATER)
    shed = 2 * catalog.recipes.mass_of(HYDROGEN)
    assert water == pytest.approx(catalog.recipes.mass_of(AIR) + shed)


async def test_the_reading_says_which_way_each_port_runs_and_why_a_machine_stands(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`way` per port, the rooms in laying order for the lanes of the scheme,
    and an automat's `stall` while it lasts (D-340)."""
    vessel, body, connector = await _hull(session, constants, foundations=2)
    bay = await _room(session, constants, body, vessel)
    reactor = await _equip(session, connector, REACTOR)
    await _equip(session, bay, UNIT)
    seen = await ship.lines_view(session, constants, catalog, body, vessel)
    assert [room["node"] for room in seen["rooms"]] == [connector.key, bay.key]
    by_goods = {one["goods"]: one for one in seen["machines"]}
    ways = {port["port"]: port["way"] for port in by_goods[REACTOR]["ports"]}
    assert ways == {WATER: "in", AIR: "out", HYDROGEN: "vent", "lube": "in"}
    assert "stall" not in by_goods[REACTOR]

    await _learned(session, body)
    row = await automat.program(session, constants, catalog, body, reactor, AIR)
    row.stall = "power"
    await session.flush()
    seen = await ship.lines_view(session, constants, catalog, body, vessel)
    assert {one["goods"]: one for one in seen["machines"]}[REACTOR]["stall"] == "power"


# --- the manual batch aboard -----------------------------------------------------


async def _electrolysis_bay(
    session: AsyncSession, constants: Constants, catalog: Catalog, *, sealed: bool = True
):
    """A hull with an electrolyser at the bridge, cells in the hold, water on
    a line, two cylinders on the oxygen outlet and one on the hydrogen vent.
    Cast off into the void unless asked to stay in port under the sky of Terra."""
    vessel, body, connector = await _hull(session, constants, foundations=2)
    if sealed:
        _seal(vessel)
    hold = await _room(session, constants, body, vessel)
    machine = await _equip(session, connector, ELECTROLYSER)
    await _cells(session, constants, hold)
    tank = await _vessel(session, hold, TANK, WATER, 200)
    first = await _empty(session, hold)
    second = await _empty(session, connector)
    vent = await _empty(session, hold)
    await _learned(session, body)
    await ship.set_lines(session, constants, catalog, body, vessel, machine, WATER, [tank])
    await ship.set_lines(session, constants, catalog, body, vessel, machine, AIR, [first, second])
    await ship.set_lines(session, constants, catalog, body, vessel, machine, HYDROGEN, [vent])
    return vessel, body, machine, tank, first, second, vent


async def test_a_manual_batch_aboard_drinks_and_pours_through_its_lines(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The water comes off its line, not out of the master's canister; the
    oxygen fills the outlet in line order across compartments; the hydrogen
    goes into its vent (D-340)."""
    vessel, body, machine, tank, first, second, vent = await _electrolysis_bay(
        session, constants, catalog
    )
    pocket = await world.body_container(session, body)
    can = await world.grant_item(session, pocket, CANISTER, quality=60, origin="тест")
    await world.grant_item(
        session, await storage.inside(session, can), WATER, amount=50, quality=60, origin="тест"
    )

    batch = await craft.start(session, constants, catalog, body, AIR, 8)
    #: What the forecast named, waste included -- off the tank on the line.
    assert batch.spent[WATER] >= 8 * catalog.recipes.recipe(AIR).amounts[WATER]
    assert await _held(session, tank) == pytest.approx(200 - batch.spent[WATER], abs=0.002)
    assert await _held(session, can) == pytest.approx(50), "вода в руках не тронута"

    await _finish(session)
    assert await _held(session, first) == pytest.approx(6), "первый баллон линии полон"
    assert await _held(session, second) == pytest.approx(2), "остаток — во второй, в другом отсеке"
    assert await _held(session, vent) == pytest.approx(16), "водород — на своей линии"
    assert await _events(session, EventKind.STORAGE_SPILLED) == []


async def test_hydrogen_without_room_goes_overboard_without_a_spill(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The vent pours what fits and lets the rest go (owner, 2026-09-13):
    the batch is not refused for it and nothing is said as spilled."""
    vessel, body, machine, tank, first, second, vent = await _electrolysis_bay(
        session, constants, catalog
    )
    await ship.set_lines(session, constants, catalog, body, vessel, machine, HYDROGEN, [])
    await craft.start(session, constants, catalog, body, AIR, 4)
    await _finish(session)
    assert await _held(session, first) == pytest.approx(4)
    assert await _stacks(session, HYDROGEN) == 0, "водород ушёл за борт"
    assert await _events(session, EventKind.STORAGE_SPILLED) == []


async def test_a_port_without_a_line_or_an_outlet_without_room_refuses_before_anything(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Refused by name, and nothing is spent: not the water, not the charge."""
    vessel, body, machine, tank, first, second, vent = await _electrolysis_bay(
        session, constants, catalog
    )
    await ship.set_lines(session, constants, catalog, body, vessel, machine, WATER, [])
    with pytest.raises(plumbing.PortDry) as dry:
        await craft.plan(session, constants, catalog, body, AIR, 1)
    assert dry.value.key == "craft-port-no-line"
    assert dry.value.params["goods"] == WATER

    await ship.set_lines(session, constants, catalog, body, vessel, machine, WATER, [tank])
    with pytest.raises(plumbing.OutletFull) as full:
        await craft.start(session, constants, catalog, body, AIR, 13)
    assert full.value.key == "craft-outlet-full"
    assert full.value.params["room"] == pytest.approx(12)
    assert await _held(session, tank) == pytest.approx(200), "вода не списана"
    assert await craft.most(session, constants, catalog, body, AIR) == 12, (
        "«сколько влезет» знает место на выходе"
    )


# --- the reactor aboard ------------------------------------------------------------


async def _reactor_bay(
    session: AsyncSession, constants: Constants, catalog: Catalog, *, sealed: bool = True
):
    """A hull with the reactor programmed with the air, its water and lubricant
    on lines, one cylinder on the outlet and no line on the hydrogen. Cast off
    into the void unless asked to stay in port under the sky of Terra."""
    vessel, body, connector = await _hull(session, constants, foundations=2)
    if sealed:
        _seal(vessel)
    hold = await _room(session, constants, body, vessel)
    reactor = await _equip(session, connector, REACTOR)
    cell = await _cells(session, constants, hold)
    water = await _vessel(session, hold, TANK, WATER, 500)
    lube = await _vessel(session, hold, TANK, LUBRICANT, 50)
    bottle = await _empty(session, hold)
    await _learned(session, body)
    await ship.set_lines(session, constants, catalog, body, vessel, reactor, WATER, [water])
    await ship.set_lines(session, constants, catalog, body, vessel, reactor, "lube", [lube])
    await ship.set_lines(session, constants, catalog, body, vessel, reactor, AIR, [bottle])
    row = await automat.program(session, constants, catalog, body, reactor, AIR)
    return vessel, body, reactor, row, cell, water, lube, bottle


async def test_the_reactor_aboard_works_on_its_lines_and_vents_its_hydrogen(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Water and lubricant off their lines, oxygen into the outlet, and with no
    hydrogen line the hydrogen goes overboard and the air is made all the same."""
    vessel, body, reactor, row, cell, water, lube, bottle = await _reactor_bay(
        session, constants, catalog
    )
    start = row.counted_at
    made = await automat.advance(
        session, constants, row, catalog=catalog, now=start + timedelta(minutes=30)
    )
    assert made > 0
    per = catalog.recipes.recipe(AIR).amounts[WATER]
    assert await _held(session, bottle) == pytest.approx(made, abs=0.002)
    assert await _held(session, water) == pytest.approx(500 - made * per, abs=0.03)
    assert await _held(session, lube) < 50, "смазка ушла с линии"
    assert await _stacks(session, HYDROGEN) == 0, "водород за бортом, машина работает"
    assert row.stall is None


async def test_the_reactor_stands_and_tells_the_crew_once_per_reason(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A full outlet, a dry line, flat cells: each said once when it appears
    or changes, to everybody aboard, and working again clears it silently."""
    vessel, body, reactor, row, cell, water, lube, bottle = await _reactor_bay(
        session, constants, catalog
    )
    moment = row.counted_at
    for _ in range(3):
        moment += timedelta(hours=2)
        await automat.advance(session, constants, row, catalog=catalog, now=moment)
    assert await _held(session, bottle) == pytest.approx(6)
    assert row.stall == AIR
    full = await _events(session, EventKind.SHIP_MACHINE_FULL)
    assert len(full) == 1, "сказано один раз, а не каждый тик"
    assert full[0].payload["goods"] == AIR
    assert full[0].payload["crew0_identity_id"] == str(body.identity_id)

    #: The crew empties the cylinder: the machine works, the mark goes quietly.
    for stack in await storage.content(session, bottle):
        await session.delete(stack)
    await session.flush()
    moment += timedelta(minutes=10)
    await automat.advance(session, constants, row, catalog=catalog, now=moment)
    assert row.stall is None
    assert len(await _events(session, EventKind.SHIP_MACHINE_FULL)) == 1

    #: The water line runs dry: the tank on it is emptied.
    for stack in await storage.content(session, water):
        await session.delete(stack)
    await session.flush()
    moment += timedelta(minutes=10)
    await automat.advance(session, constants, row, catalog=catalog, now=moment)
    assert row.stall == WATER
    (dry,) = await _events(session, EventKind.SHIP_MACHINE_DRY)
    assert dry.payload["goods"] == WATER

    #: The line itself taken off: not an empty tank, a missing line.
    await ship.set_lines(session, constants, catalog, body, vessel, reactor, WATER, [])
    moment += timedelta(minutes=10)
    await automat.advance(session, constants, row, catalog=catalog, now=moment)
    assert row.stall == WATER, "причина та же, порт тот же -- второй раз не сказано"

    #: Water again on a line, and the cells go flat.
    refill = await _vessel(session, (await ship.nodes_of(session, vessel))[-1], TANK, WATER, 500)
    await ship.set_lines(session, constants, catalog, body, vessel, reactor, WATER, [refill])
    for stack in await storage.content(session, bottle):
        await session.delete(stack)
    cell.charge = Decimal(0)
    await session.flush()
    moment += timedelta(minutes=10)
    await automat.advance(session, constants, row, catalog=catalog, now=moment)
    assert row.stall == "power"
    (flat,) = await _events(session, EventKind.SHIP_MACHINE_UNPOWERED)
    assert flat.payload["goods"] == REACTOR


async def test_the_old_default_catch_up_leaves_the_new_ports_alone(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The one-time backfill of 2026-09-04 draws the fuel and the life
    support's oxygen only: an electrolyser's ports never had a default, and
    plumbing every vessel aboard onto them would pour hydrogen into the
    crew's cylinders (D-340)."""
    from src.seed_catchup import _lines_catch_up

    vessel, body, connector = await _hull(session, constants)
    machine = await _equip(session, connector, ELECTROLYSER)
    await _empty(session, connector)
    await _lines_catch_up(session, constants)
    for port in lines.ports_of(constants, catalog, ELECTROLYSER):
        assert await lines.lines_of(session, machine.id, port.name) == []


# --- races -----------------------------------------------------------------------


async def test_two_ticks_of_one_reactor_aboard_pour_its_hours_once(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The row is taken for the transaction on the lines as on the ground: the
    second tick sees the first's stamp and pours nothing twice."""
    async with factory() as session, session.begin():
        _, _, _, row, _, _, _, bottle = await _reactor_bay(session, constants, catalog)
        row_id, bottle_id, moment = row.id, bottle.id, row.counted_at + timedelta(minutes=20)

    async def tick() -> float:
        async with factory() as db, db.begin():
            own = await db.get(AutomatRow, row_id)
            return await automat.advance(db, constants, own, catalog=catalog, now=moment)

    made = await asyncio.gather(tick(), tick())
    async with factory() as session:
        held = await _held(session, await session.get(Item, bottle_id))
    assert sorted(made)[0] == pytest.approx(0.0)
    assert held == pytest.approx(sum(made), abs=0.002)


async def test_a_tick_and_a_hand_pouring_into_one_tank_neither_overfill_nor_spill(
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reactor locks its line's vessels before it reads their room, in
    the order a hand's pour takes them: the two queue, and the cylinder never
    holds more than it holds."""
    async with factory() as session, session.begin():
        vessel, body, _, row, _, _, _, bottle = await _reactor_bay(session, constants, catalog)
        body.node_id = (await session.get(Node, vessel.connector_node_id)).id
        pocket = await world.body_container(session, body)
        can = await world.grant_item(session, pocket, CYLINDER, quality=60, origin="тест")
        await world.grant_item(
            session, await storage.inside(session, can), AIR, amount=5, quality=60, origin="тест"
        )
        #: The outlet stands in the hold; the hand pours from the bridge, so
        #: the cylinder is moved up to the bridge where the hand reaches it.
        bottle.container_id = (
            await world.node_container(session, await session.get(Node, vessel.connector_node_id))
        ).id
        ids = (row.id, body.id, can.id, bottle.id)
        moment = row.counted_at + timedelta(hours=2)

    _slow(monkeypatch, liquid, "free_in")

    async def tick() -> None:
        async with factory() as db, db.begin():
            own = await db.get(AutomatRow, ids[0])
            await automat.advance(db, constants, own, catalog=catalog, now=moment)

    async def hand() -> None:
        async with factory() as db, db.begin():
            me = await db.get(Body, ids[1])
            source = await db.get(Item, ids[2])
            target = await db.get(Item, ids[3])
            #: Refused for no room is a fair outcome of the race; overfilling is not.
            with contextlib.suppress(liquid.LiquidError):
                await liquid.pour(db, constants, catalog, me, source, target, AIR)

    await asyncio.gather(tick(), hand())
    async with factory() as session:
        held = await _held(session, await session.get(Item, ids[3]))
        #: Not only never overfilled -- a pour locks its vessel whatever -- but
        #: never paid for air that then had nowhere to go: the tick's room was
        #: read under the lock it pours under, so nothing spilled.
        spilled = await _events(session, EventKind.STORAGE_SPILLED)
    assert held <= 6 + 0.001, "баллон не переполнен"
    assert spilled == [], "реактор не насчитал воздуха, которому некуда литься"


async def test_a_port_with_no_line_is_said_as_such(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """An outlet with no line is not "the vessels are full": the journal says
    what to do -- draw a line (review 2026-09-13)."""
    vessel, body, reactor, row, cell, water, lube, bottle = await _reactor_bay(
        session, constants, catalog
    )
    await ship.set_lines(session, constants, catalog, body, vessel, reactor, AIR, [])
    await automat.advance(
        session, constants, row, catalog=catalog, now=row.counted_at + timedelta(minutes=10)
    )
    assert row.stall == AIR
    (unlined,) = await _events(session, EventKind.SHIP_MACHINE_UNLINED)
    assert unlined.payload["goods"] == AIR
    assert await _events(session, EventKind.SHIP_MACHINE_FULL) == []


async def test_a_stack_of_cells_rounding_its_charge_is_no_stall(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Thirty cells in one stack round their charge a thousandth a cell: a
    minute's draw comes back a hair short with the cells still full. That is
    not "the batteries are flat" (review 2026-09-13)."""
    vessel, body, reactor, row, cell, water, lube, bottle = await _reactor_bay(
        session, constants, catalog
    )
    cell.amount = 30_000
    await session.flush()
    moment = row.counted_at
    for _ in range(20):
        moment += timedelta(minutes=1)
        await automat.advance(session, constants, row, catalog=catalog, now=moment)
    assert await _events(session, EventKind.SHIP_MACHINE_UNPOWERED) == []
    assert row.stall is None


async def test_a_guest_does_not_work_the_owners_lines(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The lines reach the owner's tanks: a batch on them is work that reaches
    the place, and only whoever may dispose of it runs one (D-315). A guest at
    the owner's electrolyser is refused before anything is read off them."""
    vessel, body, machine, tank, first, second, vent = await _electrolysis_bay(
        session, constants, catalog
    )
    stranger = await world.create_identity(session, f"Гость-{uuid.uuid4().hex[:6]}")
    guest = await world.print_body(
        session, stranger, await session.get(Node, vessel.connector_node_id)
    )
    await world.learn(session, stranger, AIR)
    with pytest.raises(plumbing.LinesNotYours) as refused:
        await craft.start(session, constants, catalog, guest, AIR, 1)
    assert refused.value.key == "craft-lines-not-yours"
    assert await _held(session, tank) == pytest.approx(200)


async def test_an_automat_set_to_another_recipe_shows_no_air_ports(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The window shows what the engine works through (D-288): a reactor set
    to spirit drinks off its room, and its air ports are not drawn."""
    vessel, body, connector = await _hull(session, constants)
    reactor = await _equip(session, connector, REACTOR)
    await _learned(session, body, "alcohol")
    await automat.program(session, constants, catalog, body, reactor, "alcohol")
    seen = await ship.lines_view(session, constants, catalog, body, vessel)
    assert all(one["goods"] != REACTOR for one in seen["machines"]), "портов воздуха не видно"


async def test_the_beds_and_the_reactor_share_two_cylinders_drawn_the_other_way_round(
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two machines filling the same two cylinders through lines in opposite
    orders, in two transactions at once: every pour takes all its vessels in
    id order before it reads them, so the two queue -- neither holds one and
    waits for the other (review 2026-09-13)."""
    from src.engine import oxygen
    from src.models.farm import Plot, PlotState

    async with factory() as session, session.begin():
        vessel, body, reactor, row, _, _, _, _ = await _reactor_bay(
            session, constants, catalog, sealed=False
        )
        rooms = await ship.nodes_of(session, vessel)
        bay = rooms[-1]
        low, high = sorted(
            [await _empty(session, bay), await _empty(session, bay)], key=lambda one: one.id
        )
        for full in (low, high):
            await world.grant_item(
                session,
                await storage.inside(session, full),
                AIR,
                amount=5.99,
                quality=60,
                origin="тест",
            )
        unit = await _equip(session, bay, UNIT)
        await ship.set_lines(session, constants, catalog, body, vessel, reactor, AIR, [low, high])
        await ship.set_lines(session, constants, catalog, body, vessel, unit, AIR, [high, low])
        session.add(
            Plot(
                node_id=bay.id,
                owner_identity_id=body.identity_id,
                name="Грядка",
                area_m2=Decimal(5000),
                state=PlotState.SOWN,
                fertility=Decimal(50),
                culture_id="spelt",
                growth=Decimal(40),
                health=Decimal(80),
                settled_at=datetime.now(UTC),
            )
        )
        body.node_id = vessel.docked_node_id
        vessel.docked_node_id = None
        moment = row.counted_at + timedelta(minutes=30)
        vessel.air_at = moment - timedelta(minutes=30)
        row_id = row.id

    _slow(monkeypatch, liquid, "free_in")

    async def beds() -> None:
        async with factory() as db, db.begin():
            await oxygen.tick_ships(db, constants, catalog, now=moment)

    async def tick() -> None:
        async with factory() as db, db.begin():
            own = await db.get(AutomatRow, row_id)
            await automat.advance(db, constants, own, catalog=catalog, now=moment)

    await asyncio.gather(beds(), tick())


async def test_a_manual_start_and_the_reactor_on_the_same_tanks_do_not_deadlock(
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A batch starting at the electrolyser and the reactor's tick share a
    water tank and an oxygen cylinder: both take the vessel rows before the
    stacks in them, so one waits for the other's commit (review 2026-09-13)."""
    async with factory() as session, session.begin():
        vessel, body, reactor, row, _, water, _, bottle = await _reactor_bay(
            session, constants, catalog
        )
        machine = await _equip(
            session, await session.get(Node, vessel.connector_node_id), ELECTROLYSER
        )
        await ship.set_lines(session, constants, catalog, body, vessel, machine, WATER, [water])
        await ship.set_lines(session, constants, catalog, body, vessel, machine, AIR, [bottle])
        ids = (body.id, row.id)
        moment = row.counted_at + timedelta(minutes=5)

    _slow(monkeypatch, liquid, "lock_vessels")
    _slow(monkeypatch, stock, "lock_items")

    async def start() -> None:
        async with factory() as db, db.begin():
            me = await db.get(Body, ids[0])
            with contextlib.suppress(plumbing.OutletFull):
                await craft.start(db, constants, catalog, me, AIR, 1)

    async def tick() -> None:
        async with factory() as db, db.begin():
            own = await db.get(AutomatRow, ids[1])
            await automat.advance(db, constants, own, catalog=catalog, now=moment)

    await asyncio.gather(start(), tick())
