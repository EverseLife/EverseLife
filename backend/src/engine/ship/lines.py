# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: the lines from a machine to the vessels it drinks from (D-288).

**Ports.** A machine that eats or gives a liquid has ports, and they are not a
new thing in the vault: an engine's port is the fuel class it burns
(`ship.thrust` names the engine, the ship-fuel class names the fuel), the life
support's is the oxygen it breathes for the crew. A port has a name -- `fuel`,
`oxygen` -- and the name is what a line is keyed by. The electrolyser's water
and its two outlets arrive with wave 4 of D-288.

**Lines.** A line is one vessel standing on one port, in a chosen order. An
inlet drinks from its vessels in that order, and a port with **no line at
all** drinks from nothing (D-288 as amended 2026-09-04): the line is a duty,
not an upgrade -- a hull nobody has plumbed has no fuel to burn and no air
to breathe, however full its tanks.

**What stands on a line.** Only an **installed** vessel aboard: a tank, a
canister or a cylinder put up in a compartment the way furniture is
(`station.place`). In the hands, on the floor or packed in a chest it is
luggage, and no line reaches it. One word in place of the depth-of-stowage
rule of D-234 -- and one the crew can see on the thing itself.

**The hull is one building.** Lines cross compartments: the rooms aboard are
the sub-nodes of one delegate node, and a line from the engine room to a tank
in the hold is the ordinary case.

Nothing here is locked: this module says **where** a port may draw, and the
spender locks what it takes (`stock.lock_items`), exactly as before. The floor
of the ship package -- it asks `belonging` and the catalog and nothing above
itself: `physics` burns through it, `oxygen` breathes through it and `feed`,
the orders, writes through it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import storage, world
from src.engine.ship._base import AIR, FUEL, LIFE_SUPPORT
from src.engine.ship.belonging import nodes_of
from src.models.inventory import Container, ContainerKind, Item
from src.models.lines import FeedLine
from src.models.ship import Ship

#: The port names: keys of the schema (`FeedLine.port`), never words of the locale.
FUEL_PORT = "fuel"
AIR_PORT = "oxygen"


@dataclass(frozen=True, slots=True)
class Port:
    """One port of a machine: its name and the liquids it takes, by goods key."""

    name: str
    liquids: tuple[str, ...]


def fuel_port() -> Port:
    """The engines' port: what a passage burns, by the fuel class (D-230, D-252)."""
    return Port(FUEL_PORT, tuple(world.station_names(FUEL)))


def air_port() -> Port:
    """The life support's port: the one air there is (D-233)."""
    return Port(AIR_PORT, (AIR,))


def ports_of(constants: Constants, type_key: str) -> tuple[Port, ...]:
    """The ports a machine of this kind has. Empty for one that drinks nothing.

    By table and by class, never by name (D-215): an engine is whatever
    `ship.thrust` names, the life support whatever stands in its class.
    """
    found: list[Port] = []
    if type_key in constants[R.SHIP_THRUST]:
        found.append(fuel_port())
    if type_key in world.station_names(LIFE_SUPPORT):
        found.append(air_port())
    return tuple(found)


def port_of(constants: Constants, type_key: str, name: str) -> Port | None:
    """The named port of a machine of this kind, or nothing."""
    return next((port for port in ports_of(constants, type_key) if port.name == name), None)


async def hold_of(session: AsyncSession, ship: Ship) -> list[Item]:
    """What lies and stands in the rooms aboard -- one level, no insides.

    The lines want the vessels themselves and the machines beside them;
    `physics._things` walks into the vessels as well, for the mass. Read here
    without that second level, so the oxygen floor can ask for a reading of
    the hull without pulling physics in behind it.
    """
    nodes = await nodes_of(session, ship)
    if not nodes:  # pragma: no cover -- a ship always has its connector
        return []
    yards = select(Container.id).where(
        Container.kind == ContainerKind.NODE, Container.owner_id.in_([node.id for node in nodes])
    )
    rows = await session.execute(select(Item).where(Item.container_id.in_(yards)))
    return list(rows.scalars().all())


def vessels_among(catalog: Catalog, things: Sequence[Item]) -> list[Item]:
    """The installed vessels in a reading of the hold, in id order: what a
    line may stand on. A reading that walked into the vessels (`_things`) is
    fine too -- what lies inside one is never installed."""
    return sorted(
        (one for one in things if one.installed and storage.is_vessel(catalog, one.type_key)),
        key=lambda one: one.id,
    )


async def hull_vessels(
    session: AsyncSession, catalog: Catalog, ship: Ship, *, things: Sequence[Item] | None = None
) -> list[Item]:
    """The installed vessels aboard, in id order."""
    hold = things if things is not None else await hold_of(session, ship)
    return vessels_among(catalog, hold)


async def lines_of(session: AsyncSession, machine_id: uuid.UUID, port: str) -> list[FeedLine]:
    """The rows of one port, in rank order."""
    rows = await session.execute(
        select(FeedLine)
        .where(FeedLine.machine_item_id == machine_id, FeedLine.port == port)
        .order_by(FeedLine.rank, FeedLine.id)
    )
    return list(rows.scalars().all())


async def lines_for(
    session: AsyncSession, machine_ids: Sequence[uuid.UUID]
) -> dict[tuple[uuid.UUID, str], list[FeedLine]]:
    """Every row of these machines at once, by (machine, port), each in rank
    order -- one query for a reading of the whole hull."""
    if not machine_ids:
        return {}
    rows = await session.execute(
        select(FeedLine)
        .where(FeedLine.machine_item_id.in_(list(machine_ids)))
        .order_by(FeedLine.rank, FeedLine.id)
    )
    found: dict[tuple[uuid.UUID, str], list[FeedLine]] = {}
    for row in rows.scalars().all():
        found.setdefault((row.machine_item_id, row.port), []).append(row)
    return found


async def sources(
    session: AsyncSession, machine: Item, port: str, hull: Sequence[Item]
) -> list[Item]:
    """The vessels this port draws from, in order: its lines, and nothing else.

    `hull` is the installed vessels aboard (`hull_vessels`). A line whose
    vessel is not among them -- taken down, carried off, packed away -- is
    skipped, not obeyed: the row is a memory, and what answers is what stands.
    A port without a line draws from **nothing** (D-288 as amended 2026-09-04):
    the line is a duty, not an upgrade, and a crew beside full cylinders
    nobody plumbed has not been given air. The rows stay, so the bottle put
    back stands on its line again -- and the reading (`feed.view`) says
    exactly what this does, because it filters the rows the same way.
    """
    rows = await lines_of(session, machine.id, port)
    aboard = {one.id: one for one in hull}
    return [aboard[row.vessel_item_id] for row in rows if row.vessel_item_id in aboard]


async def stacks_for(
    session: AsyncSession,
    catalog: Catalog,
    ship: Ship,
    machines: Sequence[Item],
    port: Port,
    *,
    things: Sequence[Item] | None = None,
) -> list[Item]:
    """The stacks of the port's liquids these machines can reach, in line order.

    Several machines of one kind -- two engines -- share one port and draw from
    the union of their lines, each vessel once, in the order the first machine
    names it. No machine at all reaches nothing, and so does a machine with no
    line (D-288 as amended 2026-09-04).
    Unlocked: the spender relocks by id (`stock.lock_items`), so a stale
    reading cannot overspend.
    """
    if not machines:
        return []
    hull = await hull_vessels(session, catalog, ship, things=things)
    order: list[Item] = []
    seen: set[uuid.UUID] = set()
    for machine in machines:
        for vessel in await sources(session, machine, port.name, hull):
            if vessel.id not in seen:
                seen.add(vessel.id)
                order.append(vessel)
    return await stacks_in(session, order, port.liquids)


async def stacks_in(
    session: AsyncSession, vessels: Sequence[Item], liquids: Sequence[str]
) -> list[Item]:
    """The stacks of these liquids inside these vessels, in the vessels' order
    and then by id. Unlocked."""
    if not vessels or not liquids:
        return []
    insides = (
        (
            await session.execute(
                select(Container).where(
                    Container.kind == ContainerKind.STORAGE,
                    Container.owner_id.in_([vessel.id for vessel in vessels]),
                )
            )
        )
        .scalars()
        .all()
    )
    box_of = {box.owner_id: box.id for box in insides}
    rank = {box_of[vessel.id]: place for place, vessel in enumerate(vessels) if vessel.id in box_of}
    if not rank:
        return []
    found = (
        (
            await session.execute(
                select(Item).where(
                    Item.container_id.in_(list(rank)), Item.type_key.in_(tuple(liquids))
                )
            )
        )
        .scalars()
        .all()
    )
    return sorted(found, key=lambda one: (rank[one.container_id], one.id))


async def replace(
    session: AsyncSession, machine: Item, port: str, vessel_ids: Sequence[uuid.UUID]
) -> int:
    """Write the port's lines afresh: these vessels, in this order. Empty --
    the port draws from nothing. The caller holds the machine's row."""
    await session.execute(
        delete(FeedLine).where(FeedLine.machine_item_id == machine.id, FeedLine.port == port)
    )
    for rank, vessel_id in enumerate(vessel_ids):
        session.add(
            FeedLine(machine_item_id=machine.id, port=port, vessel_item_id=vessel_id, rank=rank)
        )
    await session.flush()
    return len(vessel_ids)
