# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""ship: plumbing the hull -- the order that draws a port's lines, and the
reading the console draws the picture from (D-288).

Above `lines`, which only says where a port may draw: this asks who is
giving the order -- the owner, at a console, as for every order a hull takes
(`command`) -- and what may stand on a line at all: an installed machine of
this hull, installed vessels of the same hull. The reading is one answer for
the whole hull, because the hull is one building and the window that shows
the lines shows all of it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import events, storage
from src.engine.ship import lines
from src.engine.ship._base import NotYours, ShipError
from src.engine.ship.belonging import aboard_of, nodes_of
from src.engine.ship.command import _commanded_by
from src.models.event import EventKind
from src.models.identity import Body
from src.models.inventory import Container, ContainerKind, Item
from src.models.ship import Ship
from src.units import ROUND_MASS, amount_float


class NoSuchPort(ShipError):
    """The machine has no port of that name: nothing to hang a line on."""


class NotOnLine(ShipError):
    """The machine or the vessel does not stand installed on this hull."""


async def set_lines(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    ship: Ship,
    machine: Item,
    port: str,
    vessels: Sequence[Item],
) -> int:
    """Plumb one port: these vessels, in this order. An empty list is the port
    drawing from nothing (D-288 as amended 2026-09-04). Returns how many stand
    on it.

    The same door as every order (`_commanded_by`): the owner, at the bridge
    or at a ground console. What may stand on the line is what stands on the
    hull **now** -- installed, aboard -- and a vessel of another hull or one
    lying in the hold is refused by name rather than written and ignored.
    """
    await _commanded_by(session, body, ship)
    if lines.port_of(constants, machine.type_key, port) is None:
        raise NoSuchPort(key="line-no-such-port", goods=machine.type_key)
    #: The machine's row for the transaction: two hands plumbing one port at
    #: once would otherwise delete and insert past each other into the unique
    #: pair, and one of them would get a database error where a refusal or a
    #: quiet second write belongs.
    await session.refresh(machine, with_for_update=True)
    hold = await lines.hold_of(session, ship)
    if not machine.installed or machine.id not in {one.id for one in hold}:
        raise NotOnLine(key="line-machine-not-aboard", goods=machine.type_key)
    aboard = {one.id for one in lines.vessels_among(catalog, hold)}
    chosen: list[uuid.UUID] = []
    for vessel in vessels:
        if vessel.id not in aboard:
            raise NotOnLine(key="line-vessel-not-aboard", goods=vessel.type_key)
        if vessel.id not in chosen:
            chosen.append(vessel.id)
    count = await lines.replace(session, machine, port, chosen)
    await events.record(
        session,
        EventKind.LINE_SET,
        actor_identity_id=body.identity_id,
        node_id=ship.connector_node_id,
        ship_id=str(ship.id),
        item_id=str(machine.id),
        port=port,
        vessels=count,
    )
    return count


async def view(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body, ship: Ship
) -> dict[str, object]:
    """The hull's plumbing in one reading: every machine with ports, every
    vessel a line may stand on, and which stands on which.

    The owner's reading, or a crew member's: it names every vessel aboard
    with what is in it, and a stranger at the pier gets the card (`ship.view`),
    not the hold's inventory (D-240). A port's `lines` empty means nothing
    reached (D-288 as amended 2026-09-04): the client says so itself and is
    told nothing it could derive (D-225).
    Each thing names its compartment -- a vessel in another room is the
    ordinary case, and the room's name is not something the client holds for
    rooms it is not standing in.
    """
    if ship.owner_identity_id != body.identity_id:
        aboard_ship = await aboard_of(session, body)
        if aboard_ship is None or aboard_ship.id != ship.id:
            raise NotYours(key="ship-not-yours")
    nodes = await nodes_of(session, ship)
    yards = (
        (
            await session.execute(
                select(Container).where(
                    Container.kind == ContainerKind.NODE,
                    Container.owner_id.in_([node.id for node in nodes]),
                )
            )
        )
        .scalars()
        .all()
    )
    room_by_node = {node.id: node for node in nodes}
    room_of = {yard.id: room_by_node[yard.owner_id] for yard in yards}
    hold = await lines.hold_of(session, ship)
    hull = lines.vessels_among(catalog, hold)
    aboard = {one.id for one in hull}
    machines = sorted(
        (one for one in hold if one.installed and lines.ports_of(constants, one.type_key)),
        key=lambda one: one.id,
    )
    contents = await storage.contents_of(session, hull)
    drawn = await lines.lines_for(session, [machine.id for machine in machines])

    def where(thing: Item) -> dict[str, str]:
        room = room_of[thing.container_id]
        return {"node": room.key, "node_name": room.name}

    plumbed: list[dict[str, object]] = []
    for machine in machines:
        ports: list[dict[str, object]] = []
        for port in lines.ports_of(constants, machine.type_key):
            rows = drawn.get((machine.id, port.name), [])
            ports.append(
                {
                    "port": port.name,
                    "liquids": list(port.liquids),
                    "lines": [
                        str(row.vessel_item_id) for row in rows if row.vessel_item_id in aboard
                    ],
                }
            )
        plumbed.append(
            {"item": str(machine.id), "goods": machine.type_key, **where(machine), "ports": ports}
        )
    return {
        "ship": str(ship.id),
        "machines": plumbed,
        "vessels": [
            {
                "item": str(vessel.id),
                "goods": vessel.type_key,
                **where(vessel),
                #: What is in it, by liquid: one entry since D-288 forbids
                #: mixing, several for a vessel filled before it.
                "holds": [
                    {
                        "goods": stack.type_key,
                        "amount": round(amount_float(stack.amount), ROUND_MASS),
                    }
                    for stack in sorted(contents.get(vessel.id, []), key=lambda one: one.id)
                ],
            }
            for vessel in hull
        ],
    }
