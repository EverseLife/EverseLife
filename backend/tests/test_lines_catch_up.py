# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The catch-up that drew the lines of old hulls runs once (D-288 as amended
2026-09-04).

The seed's catch-up runs at every deploy, and the step that gave every port
without a line all the vessels aboard used to run with it: a port its owner
had emptied on purpose was plumbed back at the next deploy, and a hull laid
under the rule that there is no default was plumbed as if it had lived under
the old one. Checked:

* a port its owner emptied stays empty across a second seed run, and a hull
  laid since the step ran stays unplumbed;
* a port its owner ever drew is not drawn even by the one run a world laid
  before the fix still owes -- `line.set` in the journal says so, and an
  emptied port has no rows left to say it;
* a world laid fresh owes no run at all;
* two deploys overlapping run a one-off step once (`seed_once.claim`).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ship_kit import CONSOLE, ENGINE, TANK, _equip, _laid, _port, _shipwright
from src import seed_once
from src.constants import Catalog, Constants
from src.engine import ship, world
from src.engine.ship import lines
from src.models.catchup import CatchUpStep
from src.models.identity import Body
from src.models.inventory import Item
from src.models.ship import Ship
from src.models.world import Node
from src.seed import seed

STEP = seed_once.LINES_DEFAULT_ENDED


async def _marks(session: AsyncSession) -> int:
    return await session.scalar(
        select(func.count()).select_from(CatchUpStep).where(CatchUpStep.step == STEP)
    )


async def _old_world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    """The world as a deploy before the fix leaves it: laid, and with no mark
    that the lines step has run -- it ran at every deploy instead."""

    async def unmarked(session: AsyncSession) -> None:
        return None

    with monkeypatch.context() as patch:
        patch.setattr(seed_once, "born", unmarked)
        await seed(session)
    assert await _marks(session) == 0


async def _hull(session: AsyncSession, constants: Constants) -> tuple[Ship, Body, Node]:
    """A ship in port with its owner at the bridge, and not a line drawn."""
    port = await _port(session)
    _, body = await _shipwright(session, port)
    vessel = await _laid(session, constants, body, port)
    connector = await session.get(Node, vessel.connector_node_id)
    assert connector is not None
    await _equip(session, connector, CONSOLE)
    body.node_id = connector.id
    await session.flush()
    return vessel, body, connector


async def _tank(session: AsyncSession, node: Node) -> Item:
    """An empty tank put up in the room: what a line stands on."""
    yard = await world.node_container(session, node)
    return await world.grant_item(session, yard, TANK, quality=60, origin="тест", installed=True)


async def _drawn(session: AsyncSession, machine: Item) -> list[uuid.UUID]:
    rows = await lines.lines_of(session, machine.id, lines.FUEL_PORT)
    return [row.vessel_item_id for row in rows]


async def test_a_port_emptied_on_purpose_stays_empty_across_a_second_seed(
    session: AsyncSession, constants: Constants, catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The old hull is plumbed once; what its owner does with it afterwards
    is the owner's, and a hull laid since owes nothing."""
    await _old_world(session, monkeypatch)
    vessel, body, connector = await _hull(session, constants)
    engine = await _equip(session, connector, ENGINE)
    tank = await _tank(session, connector)

    await seed(session)
    assert await _drawn(session, engine) == [tank.id], (
        "корпус, живший при умолчании, получает линию догоном"
    )
    assert await _marks(session) == 1

    await ship.set_lines(session, constants, catalog, body, vessel, engine, lines.FUEL_PORT, [])
    _, _, newer = await _hull(session, constants)
    newer_engine = await _equip(session, newer, ENGINE)
    await _tank(session, newer)

    await seed(session)
    assert await _drawn(session, engine) == [], (
        "порт, опустошённый владельцем, второй сид не проводит заново"
    )
    assert await _drawn(session, newer_engine) == [], (
        "корпус, заложенный после догона, заложен без умолчания"
    )
    assert await _marks(session) == 1, "шаг отмечен один раз"


async def test_a_port_its_owner_ever_drew_is_not_drawn_by_the_run_a_world_owes(
    session: AsyncSession, constants: Constants, catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A world laid before the fix owes the step its one run, and by then its
    owners have emptied ports the old step kept plumbing back. That run must
    leave them empty; a port nobody touched it still draws."""
    await _old_world(session, monkeypatch)
    vessel, body, connector = await _hull(session, constants)
    emptied = await _equip(session, connector, ENGINE)
    untouched = await _equip(session, connector, ENGINE)
    tank = await _tank(session, connector)
    await ship.set_lines(session, constants, catalog, body, vessel, emptied, lines.FUEL_PORT, [])

    await seed(session)
    assert await _drawn(session, emptied) == [], (
        "порт, который владелец хоть раз проводил, догон не трогает"
    )
    assert await _drawn(session, untouched) == [tank.id], (
        "порт, которого владелец не касался, догон проводит"
    )


async def test_a_world_laid_fresh_owes_no_run(session: AsyncSession, constants: Constants) -> None:
    """A fresh world never lived under the default: its first deploy does not
    plumb the hulls its players built."""
    await seed(session)
    _, _, connector = await _hull(session, constants)
    engine = await _equip(session, connector, ENGINE)
    await _tank(session, connector)

    await seed(session)
    assert await _drawn(session, engine) == []
    assert await _marks(session) == 1, "мир родился с отметкой, второй не появилось"


async def test_two_deploys_at_once_take_a_one_off_step_once(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two seeds overlapping reach the step at the same moment. Without the
    lock both ask, both hear "not yet" and both run it -- the lines drawn
    twice over one another, and the second mark refused by the key only after
    the damage. With it the second waits for the first's commit and finds the
    step done."""
    taken: list[bool] = []

    async def deploy() -> None:
        async with factory() as db, db.begin():
            if not await seed_once.claim(db, STEP):
                taken.append(False)
                return
            #: The step's own work, widening the window the race needs.
            await asyncio.sleep(0.2)
            await seed_once.done(db, STEP, ports=0)
            taken.append(True)

    outcome = await asyncio.gather(deploy(), deploy(), return_exceptions=True)
    assert not [one for one in outcome if isinstance(one, BaseException)], outcome
    assert sorted(taken) == [False, True], f"шаг берут один раз: {taken}"
    async with factory() as db:
        assert await _marks(db) == 1
