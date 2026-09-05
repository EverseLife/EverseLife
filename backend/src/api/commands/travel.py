# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Roads, exploring, gates, rest and the cold.

Split out of `api/session.py` (review 2026-08-23, wave 3): the
socket loop stayed there, the commands live by domain.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.commands.common import _alive, _alive_read, _identity, _node, _own_item
from src.api.commands.views import _identity_by_name
from src.api.registry import Refused, command
from src.constants import current, current_catalog
from src.engine import (
    access,
    frost,
    rest,
    road,
    travel,
)
from src.models.world import Edge, Node
from src.units import ROUND_HOURS


async def _lists(db: AsyncSession, node: Node) -> dict:
    """The location's door as the client sees it: shut or not, and both lists."""

    return {
        "gated": node.gated,
        "allowed": await access.roster(db, node, allowed=True),
        "barred": await access.roster(db, node, allowed=False),
    }


@command("gate.set")
async def _gate_set(state: dict, db: AsyncSession, message: dict) -> dict:
    """Shut your own location for entry, or open it (D-199, D-204).

    In person: the door is on the spot. Passage through the location is not
    touched -- shutting stops entry alone.
    """

    body = await _alive(state, db)
    identity = await _identity(state, db)
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused(key="cmd-body-off-node")
    await access.set_gate(db, node, identity, closed=bool(message["closed"]))
    return await _lists(db, node)


@command("gate.list")
async def _gate_list(state: dict, db: AsyncSession, message: dict) -> dict:
    """Name a person in a list, or strike them out of both (D-204).

    `allowed` picks the list: the white one lets into a shut location, the black
    one lets in nowhere. A name moves between the lists -- it is never in both.
    """

    body = await _alive(state, db)
    identity = await _identity(state, db)
    node = await db.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise Refused(key="cmd-body-off-node")
    who = await _identity_by_name(db, str(message["who"]))
    if message.get("strike"):
        await access.remove(db, node, identity, who)
    else:
        await access.add(db, node, identity, who, allowed=bool(message.get("allowed", True)))
    return await _lists(db, node)


@command("rest.sleep")
async def _rest_sleep(state: dict, db: AsyncSession, message: dict) -> dict:
    """Go to sleep. Recovery runs offline -- it needs no tick (D-091)."""
    body = await _alive(state, db)
    await rest.sleep(db, current(), body)
    return {"sleeping": True, "home": body.sleeping_home}


@command("rest.wake")
async def _rest_wake(state: dict, db: AsyncSession, message: dict) -> dict:
    """Wake up before the sleep is over: what was slept counts, the rest does not."""
    body = await _alive(state, db)
    restored = await rest.wake(db, current(), body)
    return {"woke": True, "restored": round(restored, 2), "stamina": float(body.stamina)}


@command("frost.warm")
async def _frost_warm(state: dict, db: AsyncSession, message: dict) -> dict:
    """Break a warmer: hours straight into the heat reserve (D-231).

    The answer is a confirmation, not a state: how many hours it gave. What the
    reserve is now comes back with `look`, which the `body.warmed` event asks
    the client to reread.
    """
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    gained = await frost.use_warmer(db, current(), current_catalog(), body, item)
    return {"warmed": round(gained, ROUND_HOURS)}


@command("travel.go")
async def _travel_go(state: dict, db: AsyncSession, message: dict) -> dict:
    """Go to a node -- even a non-adjacent one: the route builds itself (D-045, D-107)."""
    body = await _alive(state, db)
    goal = await _node(db, message["node"])
    transit = await travel.depart(db, current(), body, goal)
    return {
        "travel": str(transit.id),
        "to": goal.name,
        "arrives_at": transit.arrives_at.isoformat(),
        "legs_left": len(transit.plan or []),
    }


@command("travel.cancel")
async def _travel_cancel(state: dict, db: AsyncSession, message: dict) -> dict:
    """Turn back from the road: the body stays where it left from (D-194)."""
    body = await _alive(state, db)
    await travel.turn_back(db, body)
    node = await db.get(Node, body.node_id)
    return {"cancelled": True, "node": None if node is None else node.key}


@command("road.lay")
async def _road_lay(state: dict, db: AsyncSession, message: dict) -> dict:
    """Lay a surface tier on an edge or resurface a sagged one (D-158).

    The surface is written off at once, the road is laid on schedule: the work
    runs offline like every long-running one.
    """
    body = await _alive(state, db)
    edge = await db.get(Edge, uuid.UUID(message["edge"]))
    if edge is None:
        raise Refused(key="cmd-no-such-edge")
    job = await road.lay(
        db,
        current(),
        current_catalog(),
        body,
        edge,
        mend=bool(message.get("mend")),
    )
    return {"road": str(job.id), "ready_at": job.run_at.isoformat()}


@command("road.here", readonly=True)
async def _road_here(state: dict, db: AsyncSession, message: dict) -> dict:
    """Roads from this node: what is laid, what sagged and what it costs."""
    body = await _alive_read(state, db)
    return {"roads": await road.view(db, current(), body)}
