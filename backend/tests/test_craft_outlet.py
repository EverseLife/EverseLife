# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The room on a batch's outlet: shown, not reserved (D-340).

The owner accepted that a manual batch reserves no room for its liquids on
the condition that the player sees it. The forecast and the running batch
both say, per liquid, where it goes and how much room it finds there now:

* on the ground the vessels in the hands and at the machine, and the
  hydrogen's flare;
* aboard a sealed hull the outlet line, and the hydrogen overboard;
* aboard under a sky with air the hydrogen's own line and its room;
* on a running batch the room as it is now, so somebody filling the tank
  meanwhile shows up before the finish spills;
* and both reads -- the forecast and `orders`, where the running batch
  lives -- write nothing.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lines_kit import (
    AIR,
    CANISTER,
    CYLINDER,
    ELECTROLYSER,
    FLARE,
    HYDROGEN,
    WATER,
    _cells,
    _empty,
    _hull,
    _learned,
    _room,
    _seal,
    _vessel,
)
from ship_kit import TANK, _equip
from src.api.commands.views import _batches
from src.api.registry import COMMANDS
from src.constants import Catalog, Constants
from src.engine import craft, ship, storage, world
from src.models.identity import Body


async def _ground(session: AsyncSession, constants: Constants) -> Body:
    """A chemist on Terra with an electrolyser, a flare stack, cells, water in
    a canister and two empty oxygen cylinders in the hands."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.outlet.{stamp}", "Лаборатория", area_m2=200)
    identity = await world.create_identity(session, f"Химик-{stamp}")
    body = await world.print_body(session, identity, node)
    yard = await world.node_container(session, node)
    for machine in (ELECTROLYSER, FLARE):
        await world.grant_item(session, yard, machine, quality=60, origin="тест")
    await _cells(session, constants, node)
    await world.learn(session, identity, AIR)
    pocket = await world.body_container(session, body)
    can = await world.grant_item(session, pocket, CANISTER, quality=60, origin="тест")
    await world.grant_item(
        session, await storage.inside(session, can), WATER, amount=100, quality=60, origin="тест"
    )
    for _ in range(2):
        await world.grant_item(session, pocket, CYLINDER, quality=60, origin="тест")
    return body


async def _aboard(session: AsyncSession, constants: Constants, catalog: Catalog, *, sealed: bool):
    vessel, body, connector = await _hull(session, constants, foundations=2)
    hold = await _room(session, constants, body, vessel)
    machine = await _equip(session, connector, ELECTROLYSER)
    await _cells(session, constants, hold)
    tank = await _vessel(session, hold, TANK, WATER, 200)
    bottles = [await _empty(session, hold), await _empty(session, connector)]
    vent = await _empty(session, hold)
    await _learned(session, body)
    await ship.set_lines(session, constants, catalog, body, vessel, machine, WATER, [tank])
    await ship.set_lines(session, constants, catalog, body, vessel, machine, AIR, bottles)
    await ship.set_lines(session, constants, catalog, body, vessel, machine, HYDROGEN, [vent])
    if sealed:
        _seal(vessel)
    return body


async def test_the_forecast_on_the_ground_names_the_hands_and_the_flare(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    body = await _ground(session, constants)
    plan = await craft.plan(session, constants, catalog, body, AIR, 4)
    assert plan.outlets == (
        {"goods": AIR, "where": "reach", "room": pytest.approx(12)},
        {"goods": HYDROGEN, "where": "flare"},
    )


async def test_the_forecast_aboard_names_the_lines_and_where_the_hydrogen_goes(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Sealed: the oxygen line and overboard. Under a sky with air: the
    hydrogen's line with its room, because there it is the one place."""
    per_cylinder = storage.capacity(catalog, CYLINDER) / catalog.recipes.mass_of(HYDROGEN)
    sealed = await _aboard(session, constants, catalog, sealed=True)
    plan = await craft.plan(session, constants, catalog, sealed, AIR, 2)
    assert plan.outlets == (
        {"goods": AIR, "where": "line", "room": pytest.approx(12)},
        {"goods": HYDROGEN, "where": "void"},
    )
    in_port = await _aboard(session, constants, catalog, sealed=False)
    plan = await craft.plan(session, constants, catalog, in_port, AIR, 2)
    assert plan.outlets[1] == {
        "goods": HYDROGEN,
        "where": "line",
        "room": pytest.approx(per_cylinder),
    }


async def test_a_running_batch_shows_the_room_as_it_is_now(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Somebody fills the cylinders during the hours: the running batch shows
    one unit of room against its four, which is what the window warns of."""
    body = await _ground(session, constants)
    await craft.start(session, constants, catalog, body, AIR, 4)
    (running,) = await _batches(session, body.identity_id)
    assert running["outlets"][0] == {"goods": AIR, "where": "reach", "room": pytest.approx(12)}

    pocket = await world.body_container(session, body)
    bottles = [one for one in await world.contents(session, pocket) if one.type_key == CYLINDER]
    for bottle, amount in zip(bottles, (6, 5), strict=True):
        await world.grant_item(
            session, await storage.inside(session, bottle), AIR, amount=amount, origin="тест"
        )
    (running,) = await _batches(session, body.identity_id)
    assert running["outlets"][0]["room"] == pytest.approx(1)


async def test_the_forecast_and_the_look_with_outlets_write_nothing(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Both reads run under the read-only guard (`db/readonly.py`): the room
    on the outlet, the flare, the running batch are all read, never made."""
    async with factory() as session, session.begin():
        body = await _ground(session, constants)
        await craft.start(session, constants, catalog, body, AIR, 1)
        who = body.identity_id

    async with factory() as db, db.begin():
        answer = await COMMANDS["craft.plan"].run(
            {"identity_id": who}, db, {"cmd": "craft.plan", "output": AIR, "units": 2}
        )
    assert [one["goods"] for one in answer["plan"]["outlets"]] == [AIR, HYDROGEN]
    async with factory() as db, db.begin():
        seen = await COMMANDS["orders"].run({"identity_id": who}, db, {"cmd": "orders"})
    assert "outlets" in seen["orders"]["batches"][0]
