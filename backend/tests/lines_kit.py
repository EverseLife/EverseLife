# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The plumber's bench the lines tests share (D-288, D-340): a hull with its
owner at the bridge, one more compartment, a vessel with a liquid in it, and
what it holds. Used by `test_lines*.py`; not collected by pytest.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ship_kit import CONSOLE, _equip, _laid, _port, _shipwright
from src.constants import Constants
from src.engine import ship, storage, world
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


async def _empty(session: AsyncSession, node: Node, type_key: str = CYLINDER) -> Item:
    """An installed vessel with nothing in it yet."""
    yard = await world.node_container(session, node)
    return await world.grant_item(
        session, yard, type_key, quality=60, origin="тест", installed=True
    )
