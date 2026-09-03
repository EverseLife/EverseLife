# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Two hulls meeting in the sky (D-289, wave 3).

A hull may be sent to another as to a planet: the rendezvous is aimed at the
other's forecast, and when the helm has come to rest beside it -- within the
hold's radius, under the hold's speed -- the two fly as one (`helm._hold`).
From the hold either commander may ask to dock; with **both** consents the
connectors are joined by an edge, the way a hull is joined to a pier, and
crew walk across with what they carry -- a canister of fuel for a drifter
first of all. Nobody docks to a hull whose commander has not agreed: the
consent is the whole of the rule, and the hook a boarding would hang on is
left for its own decision.

What is seen from the chart, and what may be aimed at, is the reading half
of this -- `sighting` -- kept apart so the console can read it without
pulling the commands in behind it.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.engine import events, travel
from src.engine.ship import sighting, sim
from src.engine.ship._base import (
    Docked,
    NoPort,
    ShipError,
    TooFar,
    _gangway_seconds,
)
from src.engine.ship.command import _commanded_by
from src.models.event import EventKind
from src.models.identity import Body
from src.models.ship import Ship
from src.models.world import Node, Surface

#: The berth a hull-to-hull gangway counts as: the shortest walk a pier has.
HATCH_BERTH = 1

# --- consent and the edge ---------------------------------------------------------


async def dock(
    session: AsyncSession, constants: Constants, body: Body, ship: Ship, other: Ship
) -> bool:
    """This hull's consent to dock with the hull it flies as one with.

    The hold first, always: two hulls are joined only where they already
    rest beside each other. Then the consent is written on this hull's row;
    if the other commander's is already on theirs, the connectors are joined
    by an edge -- one gangway, the shortest a pier has -- and both crews are
    told. Otherwise the other side is told it is asked. Returns whether the
    edge is there now.

    Both rows are locked, in id order, so two consents given in the same
    second meet under one lock rather than making two edges.
    """
    await _commanded_by(session, body, ship)
    if other.id == ship.id:
        raise TooFar(key="ship-dock-self")
    first, second = sorted((ship, other), key=lambda one: one.id)
    await session.refresh(first, with_for_update=True)
    await session.refresh(second, with_for_update=True)
    if ship.lost_at is not None or other.lost_at is not None:
        raise ShipError(key="ship-lost", ship=(ship if ship.lost_at else other).name)
    if ship.docked_node_id is not None or other.docked_node_id is not None:
        raise Docked(key="ship-dock-at-port")
    if ship.docked_ship_id is not None:
        joined = await session.get(Ship, ship.docked_ship_id)
        if joined is not None:
            raise Docked(key="ship-already-docked-ship", other=joined.name)
        #: A link to a row that is gone: nothing to be joined to. Healed here
        #: rather than refused for.
        ship.docked_ship_id = None
    if not sighting.paired(ship, other):
        world = await sim.system(session, constants)
        raise NoPort(key="ship-not-held", radius=world.dock_radius, speed=world.dock_speed)
    ship.dock_ask_ship_id = other.id
    await session.flush()
    if other.dock_ask_ship_id != ship.id:
        await events.record(
            session,
            EventKind.SHIP_DOCK_ASKED,
            actor_identity_id=other.owner_identity_id,
            node_id=other.connector_node_id,
            ship_id=str(other.id),
            name=other.name,
            other_ship_id=str(ship.id),
            other=ship.name,
        )
        return False
    #: Both consents on the table: the edge, connector to connector.
    mine = await _connector(session, ship)
    theirs = await _connector(session, other)
    await travel.connect(
        session,
        mine,
        theirs,
        base_seconds=_gangway_seconds(constants, HATCH_BERTH),
        surface=Surface.PAVED,
    )
    ship.docked_ship_id = other.id
    other.docked_ship_id = ship.id
    ship.dock_ask_ship_id = None
    other.dock_ask_ship_id = None
    await session.flush()
    for teller, one, two in (
        (ship.owner_identity_id, ship, other),
        (other.owner_identity_id, other, ship),
    ):
        await events.record(
            session,
            EventKind.SHIP_DOCKED_SHIP,
            actor_identity_id=teller,
            node_id=one.connector_node_id,
            ship_id=str(one.id),
            name=one.name,
            other_ship_id=str(two.id),
            other=two.name,
        )
    return True


async def undock(session: AsyncSession, constants: Constants, body: Body, ship: Ship) -> None:
    """Part from the hull this one is docked to: the edge comes off, the hold
    stays -- the two still fly as one until an order parts them."""
    await _commanded_by(session, body, ship)
    await session.refresh(ship, with_for_update=True)
    if ship.docked_ship_id is None:
        raise Docked(key="ship-not-docked-ship")
    await _part(session, ship, told_by=body.identity_id)


async def let_go(session: AsyncSession, constants: Constants, ship: Ship) -> None:
    """A hull leaving on an order leaves its docking and its hold behind: the
    edge comes off, the consents are forgotten. The hold itself is ended by
    `sim.depart`, which writes the hull its own state first."""
    if ship.docked_ship_id is not None:
        await _part(session, ship, told_by=ship.owner_identity_id)
    ship.dock_ask_ship_id = None


async def _part(session: AsyncSession, ship: Ship, *, told_by: uuid.UUID) -> None:
    other = None if ship.docked_ship_id is None else await session.get(Ship, ship.docked_ship_id)
    if other is not None:
        #: Both rows, in id order: two undockings from the two sides in one
        #: second meet under one lock.
        for one in sorted((ship, other), key=lambda row: row.id):
            await session.refresh(one, with_for_update=True)
        await travel.disconnect(
            session, await _connector(session, ship), await _connector(session, other)
        )
        other.docked_ship_id = None
        other.dock_ask_ship_id = None
    ship.docked_ship_id = None
    ship.dock_ask_ship_id = None
    await session.flush()
    #: Both owners are told (D-226): the other console shows "docked" until
    #: it hears otherwise.
    tellers = {told_by, ship.owner_identity_id}
    if other is not None:
        tellers.add(other.owner_identity_id)
    for teller in tellers:
        await events.record(
            session,
            EventKind.SHIP_UNDOCKED_SHIP,
            actor_identity_id=teller,
            node_id=ship.connector_node_id,
            ship_id=str(ship.id),
            name=ship.name,
            other_ship_id="" if other is None else str(other.id),
            other="" if other is None else other.name,
        )


async def _connector(session: AsyncSession, ship: Ship) -> Node:
    node = await session.get(Node, ship.connector_node_id)
    if node is None:  # pragma: no cover -- a ship always has its connector
        raise ShipError(key="ship-no-connector-or-port")
    return node
