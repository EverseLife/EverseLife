# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The hydrogen of a hull set down under a sky with air (D-340).

A sealed hull lets what its vent line cannot take go overboard
(`test_lines_air.py`). Under a sky with air it may not: the gas is never
released into the air, and a hull has no flare. So its vent line is its one
place, and it binds like the outlet:

* a manual batch is refused at the door when the vent line cannot take its
  whole hydrogen -- by name when the port has no line at all, with the room
  when the line is too small -- and "as much as fits" knows that room;
* a batch that fits pours its hydrogen into the vent line;
* a batch started in the void and landed under a sky before its finish spills
  what its vent cannot take, with an event -- an accident said aloud;
* the reactor stands for its hydrogen with the same words the crew hears for
  the outlet: no line, then the vessels full.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from lines_kit import (
    AIR,
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
from src.engine import automat, craft, ship, storage, world
from src.engine.craft import plumbing
from src.models.event import EventKind


async def _bay(session: AsyncSession, constants: Constants, catalog: Catalog):
    """An electrolyser in port under Terra's sky: water on a line, two
    cylinders on the oxygen outlet, one on the hydrogen vent."""
    vessel, body, connector = await _hull(session, constants, foundations=2)
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
    return vessel, body, machine, tank, first, vent


async def test_under_a_sky_the_batch_needs_its_whole_hydrogen_on_the_vent_line(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """No line on the vent is refused by name, a vent too small with its room,
    and nothing is spent either way; a batch that fits pours its hydrogen into
    the vent line and spills nothing."""
    vessel, body, machine, tank, first, vent = await _bay(session, constants, catalog)
    await ship.set_lines(session, constants, catalog, body, vessel, machine, HYDROGEN, [])
    with pytest.raises(plumbing.PortDry) as dry:
        await craft.start(session, constants, catalog, body, AIR, 1)
    assert dry.value.key == "craft-port-no-line"
    assert dry.value.params["goods"] == HYDROGEN
    assert dry.value.params["way"] == "vent"

    await ship.set_lines(session, constants, catalog, body, vessel, machine, HYDROGEN, [vent])
    per_cylinder = storage.capacity(catalog, CYLINDER) / catalog.recipes.mass_of(HYDROGEN)
    await world.grant_item(
        session,
        await storage.inside(session, vent),
        HYDROGEN,
        amount=per_cylinder - 6,
        quality=60,
        origin="тест",
    )
    with pytest.raises(plumbing.OutletFull) as full:
        await craft.start(session, constants, catalog, body, AIR, 4)
    assert full.value.params["goods"] == HYDROGEN
    assert full.value.params["room"] == pytest.approx(6)
    assert full.value.params["units"] == pytest.approx(8)
    assert await _held(session, tank) == pytest.approx(200), "вода не списана"
    assert await craft.most(session, constants, catalog, body, AIR) == 3, (
        "«сколько влезет» знает место на линии водорода"
    )

    await craft.start(session, constants, catalog, body, AIR, 3)
    await _finish(session)
    assert await _held(session, first) == pytest.approx(3)
    assert await _held(session, vent) == pytest.approx(per_cylinder), "водород — на своей линии"
    assert await _events(session, EventKind.STORAGE_SPILLED) == []


async def test_a_batch_landed_under_a_sky_spills_the_hydrogen_its_vent_cannot_take(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Started in the void, where the hydrogen goes overboard past an empty
    vent line; set down under a sky with air before the finish. The finish
    does not release the gas into the air as if nothing changed: what finds
    no place is a spill, and the journal says so (D-340)."""
    vessel, body, machine, _, first, _ = await _bay(session, constants, catalog)
    port = vessel.docked_node_id
    await ship.set_lines(session, constants, catalog, body, vessel, machine, HYDROGEN, [])
    _seal(vessel)
    await craft.start(session, constants, catalog, body, AIR, 2)
    vessel.docked_node_id = port
    await session.flush()

    await _finish(session)
    assert await _held(session, first) == pytest.approx(2), "кислород на месте"
    (spill,) = await _events(session, EventKind.STORAGE_SPILLED)
    assert spill.payload["type_key"] == HYDROGEN
    assert spill.payload["amount"] == pytest.approx(4)
    assert await _stacks(session, HYDROGEN) == 0


async def test_the_reactor_under_a_sky_stands_for_its_hydrogen(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """No hydrogen line: nothing is made, and the crew hears "a port has no
    line" naming the hydrogen. A line drawn onto a nearly full cylinder: the
    reactor makes what the cylinder takes and stands on "the vessels are full"."""
    vessel, body, connector = await _hull(session, constants, foundations=2)
    hold = await _room(session, constants, body, vessel)
    reactor = await _equip(session, connector, REACTOR)
    await _cells(session, constants, hold)
    water = await _vessel(session, hold, TANK, WATER, 500)
    lube = await _vessel(session, hold, TANK, LUBRICANT, 50)
    bottle = await _empty(session, hold)
    await _learned(session, body)
    await ship.set_lines(session, constants, catalog, body, vessel, reactor, WATER, [water])
    await ship.set_lines(session, constants, catalog, body, vessel, reactor, "lube", [lube])
    await ship.set_lines(session, constants, catalog, body, vessel, reactor, AIR, [bottle])
    row = await automat.program(session, constants, catalog, body, reactor, AIR)

    moment = row.counted_at + timedelta(minutes=30)
    made = await automat.advance(session, constants, row, catalog=catalog, now=moment)
    assert made == 0
    assert row.stall == HYDROGEN
    (unlined,) = await _events(session, EventKind.SHIP_MACHINE_UNLINED)
    assert unlined.payload["goods"] == HYDROGEN
    assert await _held(session, bottle) == 0

    #: A line drawn onto an empty cylinder: the machine works, the mark goes quietly.
    vent = await _empty(session, hold)
    await ship.set_lines(session, constants, catalog, body, vessel, reactor, HYDROGEN, [vent])
    moment += timedelta(minutes=1)
    made = await automat.advance(session, constants, row, catalog=catalog, now=moment)
    assert made > 0
    assert row.stall is None
    assert await _held(session, vent) == pytest.approx(2 * made, abs=0.002)

    #: The vent cylinder topped up to a tenth short of full: the reactor makes
    #: what that tenth takes, and stands on "the vessels are full".
    per_cylinder = storage.capacity(catalog, CYLINDER) / catalog.recipes.mass_of(HYDROGEN)
    await world.grant_item(
        session,
        await storage.inside(session, vent),
        HYDROGEN,
        amount=per_cylinder - 0.1 - await _held(session, vent),
        quality=60,
        origin="тест",
    )
    before = await _held(session, bottle)
    moment += timedelta(hours=2)
    made = await automat.advance(session, constants, row, catalog=catalog, now=moment)
    assert made == pytest.approx(0.05, abs=0.002), "сделано ровно на место водорода"
    assert await _held(session, vent) == pytest.approx(per_cylinder, abs=0.002)
    assert await _held(session, bottle) == pytest.approx(before + made, abs=0.002)
    assert await _events(session, EventKind.STORAGE_SPILLED) == []
    assert row.stall == HYDROGEN
    (full,) = await _events(session, EventKind.SHIP_MACHINE_FULL)
    assert full.payload["goods"] == HYDROGEN
