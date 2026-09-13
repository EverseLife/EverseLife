# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The vent gas on the ground: the flare stack, and never the air (D-340).

The owner refused the hydrogen of electrolysis going into the air. On the
ground there are no lines, so what is outside decides:

* under a sky with air, a manual batch without a flare stack in its node is
  refused before anything is spent; with one, the hydrogen burns in it and no
  vessel is claimed by it -- a flare lying in parts does not count;
* on an airless world the hydrogen is let out, flare or none;
* the ground automat stands for want of a flare, says so once, and runs once
  one stands -- the reason on its row and in `auto.view` while it lasts;
* a flare stack is not made aboard: a hull has none.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from lines_kit import (
    AIR,
    CANISTER,
    CYLINDER,
    ELECTROLYSER,
    FLARE,
    HYDROGEN,
    LUBRICANT,
    REACTOR,
    WATER,
    _cells,
    _events,
    _finish,
    _held,
    _hull,
    _learned,
    _stacks,
)
from src.constants import Catalog, Constants
from src.engine import automat, craft, oxygen, storage, world
from src.engine.craft import CraftError, plumbing
from src.models.event import EventKind
from src.models.identity import Body
from src.models.world import Layer, Node, Planet


async def _lab(
    session: AsyncSession, constants: Constants, *, planet: Planet = Planet.TERRA
) -> tuple[Node, Body]:
    """A ground node with an electrolyser, cells, and a chemist holding a
    canister of water and two empty oxygen cylinders. An airless world gets its
    sphere node marked so (D-234): that is where the air of a planet is read."""
    stamp = uuid.uuid4().hex[:8]
    if planet is not Planet.TERRA:
        await world.create_node(
            session,
            planet.value,
            planet.value.title(),
            area_m2=1,
            planet=planet,
            layer=Layer.SPACE,
            properties={oxygen.AIRLESS: True},
        )
    node = await world.create_node(
        session, f"{planet.value}.lab.{stamp}", "Лаборатория", area_m2=200, planet=planet
    )
    identity = await world.create_identity(session, f"Химик-{stamp}")
    body = await world.print_body(session, identity, node)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, ELECTROLYSER, quality=60, origin="тест")
    await _cells(session, constants, node)
    await world.learn(session, identity, AIR)
    pocket = await world.body_container(session, body)
    can = await world.grant_item(session, pocket, CANISTER, quality=60, origin="тест")
    await world.grant_item(
        session, await storage.inside(session, can), WATER, amount=100, quality=60, origin="тест"
    )
    for _ in range(2):
        await world.grant_item(session, pocket, CYLINDER, quality=60, origin="тест")
    return node, body


async def _flare(session: AsyncSession, node: Node, *, installed: bool = True) -> None:
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, FLARE, quality=60, origin="тест", installed=installed)


async def _water_left(session: AsyncSession, body: Body) -> float:
    pocket = await world.body_container(session, body)
    cans = [one for one in await world.contents(session, pocket) if one.type_key == CANISTER]
    return sum([await _held(session, can) for can in cans])


# --- the manual batch ----------------------------------------------------------


async def test_under_a_sky_a_batch_without_a_flare_is_refused_before_anything(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Nowhere safe for the hydrogen: the forecast and the start both refuse by
    the flare's word, and no water is spent. A flare lying in parts is no flare."""
    node, body = await _lab(session, constants)
    with pytest.raises(plumbing.NoFlare) as refused:
        await craft.plan(session, constants, catalog, body, AIR, 1)
    assert refused.value.key == "craft-no-flare"
    assert refused.value.params == {"goods": HYDROGEN, "aboard": "false"}

    await _flare(session, node, installed=False)
    with pytest.raises(plumbing.NoFlare):
        await craft.start(session, constants, catalog, body, AIR, 1)
    assert await _water_left(session, body) == pytest.approx(100), "вода не списана"


async def test_a_flare_in_the_node_burns_the_hydrogen_and_claims_no_vessel(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """With a flare stack standing in the node the batch runs; the oxygen pours
    into one cylinder, the spare stays empty, and the hydrogen is burned --
    not in a vessel, not on the floor, not a spill."""
    node, body = await _lab(session, constants)
    await _flare(session, node)
    await craft.start(session, constants, catalog, body, AIR, 1)
    await _finish(session)

    pocket = await world.body_container(session, body)
    bottles = [one for one in await world.contents(session, pocket) if one.type_key == CYLINDER]
    held = sorted([await _held(session, one) for one in bottles])
    assert held == [pytest.approx(0), pytest.approx(1)], "кислород в одном баллоне, второй пуст"
    assert await _stacks(session, HYDROGEN) == 0, "водород сгорел в факеле"
    assert await _events(session, EventKind.STORAGE_SPILLED) == []


async def test_on_an_airless_world_the_hydrogen_is_let_out_without_a_flare(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """No air outside: nothing to burn or blow up with, so no flare is asked
    for and the hydrogen goes out without a word."""
    _, body = await _lab(session, constants, planet=Planet.PYROXIS)
    await craft.start(session, constants, catalog, body, AIR, 1)
    await _finish(session)
    assert await _stacks(session, HYDROGEN) == 0
    assert await _stacks(session, AIR) == pytest.approx(1)
    assert await _events(session, EventKind.STORAGE_SPILLED) == []


async def test_a_flare_stack_is_not_made_aboard(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A hull has no flare (D-340): the batch that would stand one in a
    compartment is refused at the door, before the workshop is even asked."""
    _, body, _ = await _hull(session, constants)
    await _learned(session, body, FLARE)
    with pytest.raises(CraftError) as refused:
        await craft.plan(session, constants, catalog, body, FLARE, 1)
    assert refused.value.key == "craft-flare-aboard"


# --- the ground automat --------------------------------------------------------


async def test_the_ground_reactor_stands_without_a_flare_and_runs_once_one_stands(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The limiters are not even counted: no flare, nothing made, the reason on
    the row and in the factory window, told once however many ticks it lasts.
    A flare put up, and the next stretch runs and clears the reason quietly."""
    node, body = await _lab(session, constants)
    yard = await world.node_container(session, node)
    reactor = await world.grant_item(session, yard, REACTOR, quality=70, origin="тест")
    for liquid_name, amount in ((WATER, 300), (LUBRICANT, 20)):
        tank = await world.grant_item(session, yard, CANISTER, quality=60, origin="тест")
        await world.grant_item(
            session,
            await storage.inside(session, tank),
            liquid_name,
            amount=amount,
            quality=60,
            origin="тест",
        )
    bottle = await world.grant_item(session, yard, CYLINDER, quality=60, origin="тест")
    row = await automat.program(session, constants, catalog, body, reactor, AIR)

    moment = row.counted_at
    for _ in range(3):
        moment += timedelta(minutes=30)
        made = await automat.advance(session, constants, row, catalog=catalog, now=moment)
        assert made == 0
    assert row.stall == "flare"
    (stood,) = await _events(session, EventKind.AUTOMAT_NO_FLARE)
    assert stood.payload["goods"] == HYDROGEN
    assert stood.actor_identity_id == body.identity_id
    floor = await automat.view(session, catalog, body)
    assert floor["machines"][0]["stall"] == "flare"
    assert await _held(session, bottle) == 0

    await _flare(session, node)
    moment += timedelta(minutes=30)
    made = await automat.advance(session, constants, row, catalog=catalog, now=moment)
    assert made > 0
    assert row.stall is None
    assert await _held(session, bottle) == pytest.approx(made, abs=0.002)
    assert await _stacks(session, HYDROGEN) == 0, "водород сгорел, в тару двора не лёг"
    assert "stall" not in (await automat.view(session, catalog, body))["machines"][0]
    assert len(await _events(session, EventKind.AUTOMAT_NO_FLARE)) == 1
