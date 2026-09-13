# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The plumber's bench the lines tests share (D-288, D-340): a hull with its
owner at the bridge, one more compartment, a vessel with a liquid in it, what
it holds, the cells, the journal -- and the hull sealed or under a sky with
air, which is where its hydrogen goes (D-340). Used by `test_lines*.py` and
`test_vent*.py`; not collected by pytest.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ship_kit import CONSOLE, _equip, _laid, _port, _shipwright
from src.constants import Constants
from src.engine import battery, craft, ship, storage, world
from src.models.event import Event, EventKind
from src.models.identity import Body, Identity
from src.models.inventory import Item
from src.models.job import Job, JobKind, JobState
from src.models.ship import Ship
from src.models.world import Node
from src.units import amount_float

AIR = "oxygen"
WATER = "water"
HYDROGEN = "hydrogen"
LUBRICANT = "lubricant"
CYLINDER = "oxygen_tank"
CANISTER = "canister"
BATTERY = "battery"
SOLAR = "solar_panel"
ELECTROLYSER = "electrolyzer"
REACTOR = "auto_reactor"
FLARE = "flare_stack"


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


def _seal(vessel: Ship) -> None:
    """Cast the hull off into the void: sealed, nothing outside to burn with,
    and its hydrogen goes overboard past its vent line (D-340)."""
    vessel.docked_node_id = None


async def _cells(session: AsyncSession, constants: Constants, node: Node) -> Item:
    """A battery standing in the room, full: the hull's bus (D-288), or the
    node's own current where no grid reaches (D-071)."""
    yard = await world.node_container(session, node)
    cell = await world.grant_item(session, yard, BATTERY, quality=60, origin="тест")
    cell.charge = Decimal(str(battery.capacity(constants)))
    cell.charged_at = datetime.now(UTC)
    await session.flush()
    return cell


async def _learned(session: AsyncSession, body: Body, key: str = AIR) -> Identity:
    identity = await session.get(Identity, body.identity_id)
    await world.learn(session, identity, key)
    return identity


async def _events(session: AsyncSession, kind: EventKind) -> list[Event]:
    return list((await session.execute(select(Event).where(Event.kind == kind))).scalars().all())


async def _stacks(session: AsyncSession, type_key: str) -> float:
    """Every unit of this thing in the world, wherever it lies."""
    rows = (await session.execute(select(Item).where(Item.type_key == type_key))).scalars().all()
    return sum(float(one.amount) for one in rows) / 1000


async def _finish(session: AsyncSession) -> None:
    """Land the batch's job by hand: the test is one transaction."""
    job = (
        (await session.execute(select(Job).where(Job.kind == JobKind.CRAFT_BATCH))).scalars().one()
    )
    await craft.finish(session, job)
