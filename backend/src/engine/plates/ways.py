# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The map redrawn: ways break, bridges of cooled lava are laid (D-197, D-233).

The rule above every roll: **the planet stays one graph.** A break that would
cut anything off the plateau is cancelled; a break under somebody walking the
way is not -- they die with their pocket, the one sanctioned sink of matter.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import death, net, travel, world
from src.engine.plates._base import ANVIL, _adjacency, _connected, _exempt, _surface
from src.engine.plates.fire import _consume
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.travel import Travel, TravelState
from src.models.world import Edge, Node, Planet, Surface


async def _redraw(
    session: AsyncSession,
    constants: Constants,
    dice: random.Random,
    shaken: list[Node],
    *,
    now: datetime,
) -> tuple[int, int, int]:
    """Break some ways and lay others. Returns what broke, what was laid, and
    who died on a way that went.

    The rule above every roll (D-197, D-233): **the planet stays one graph.**
    A break that would cut anything off -- a camp with people in it, an empty
    field with a vein, anything at all -- is cancelled and the way stays open.
    Being walled in is a death without a window and is forbidden here (P6); an
    orphaned field would be a place nobody could ever reach again, which in an
    eternal world (D-007) is the same wrong done to the map instead of a person.

    What is **not** cancelled: a way breaking under somebody walking it. They
    die, and their pocket is lost for ever -- one walked far from the ship and
    chose that risk.
    """
    ways = await _adjacency(session)
    anchor = await _anchor(session)
    if anchor is None:  # pragma: no cover -- the planet always has its plateau
        return 0, 0, 0

    torn = dead = 0
    for node in shaken:
        for other in sorted(ways.get(node.id, set()), key=str):
            if dice.random() > constants[R.PYROXIS_EDGE_REDRAW_SHARE]:
                continue
            if not _may_lose(ways, node.id, other, anchor):
                continue
            edge = await _edge_between(session, node.id, other, lock=True)
            if edge is None:  # pragma: no cover -- read from the same graph
                continue
            dead += await _kill_on(session, constants, edge, now=now)
            await session.delete(edge)
            ways[node.id].discard(other)
            ways.get(other, set()).discard(node.id)
            torn += 1
    laid = 0
    for node in shaken:
        if await _bridge(session, constants, dice, node, ways):
            laid += 1
    await session.flush()
    if torn or laid:
        #: The Net routes letters along this graph and keeps it in memory
        #: (D-222): an edge gone by anything other than `travel.disconnect`
        #: has to say so itself, or the post keeps walking a way that is gone.
        net.forget_graph()
    return torn, laid, dead


def _may_lose(
    ways: dict[uuid.UUID, set[uuid.UUID]],
    node: uuid.UUID,
    other: uuid.UUID,
    anchor: uuid.UUID,
) -> bool:
    """Whether this way may go without cutting anything off the plateau.

    Checked by **reachability**, not by counting ways out: a node with two ways
    that both lead into the same dead end is as walled in as one with none, and
    that is exactly the case a degree count calls safe.

    Judged against what is reachable **now**, not against every node there is:
    a place already standing apart from the plateau -- an old node the seed
    left unconnected, a find nobody has walked a trail to yet -- would
    otherwise make every way on the planet unbreakable and quietly switch the
    eruptions off altogether.
    """
    before = _connected(ways, anchor)
    without = {where: set(near) for where, near in ways.items()}
    without.get(node, set()).discard(other)
    without.get(other, set()).discard(node)
    return before <= _connected(without, anchor)


async def _anchor(session: AsyncSession) -> uuid.UUID | None:
    """What the planet is measured from: the plateau it never shakes (D-197).

    The one place on Pyroxis that is always there and always reachable, so it
    is the one honest place to ask "is this still connected to anything" from.
    """
    found = await session.scalar(
        select(Node.id).where(
            Node.planet == Planet.PYROXIS.value, Node.properties[ANVIL].as_boolean()
        )
    )
    if found is not None:
        return found
    ground = await _surface(session)
    return ground[0].id if ground else None


async def _edge_between(
    session: AsyncSession, one: uuid.UUID, other: uuid.UUID, *, lock: bool = False
) -> Edge | None:
    """The edge between two nodes, optionally taken for the transaction.

    Locked before it is taken away: somebody may be stepping onto it at this
    very second, and the rule that a way breaking under a walker kills them
    (D-233) is only true if the two cannot pass each other.
    """
    stmt = select(Edge).where(
        or_(
            (Edge.node_a_id == one) & (Edge.node_b_id == other),
            (Edge.node_a_id == other) & (Edge.node_b_id == one),
        )
    )
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalars().first()


async def _kill_on(
    session: AsyncSession, constants: Constants, edge: Edge, *, now: datetime
) -> int:
    """Whoever is on this way when it goes. Returns how many died.

    The pocket goes with them and does not fall to the ground: a sanctioned
    sink of matter, named in the decision itself (D-233, P1). One walked far
    from the ship and chose this risk.
    """
    going = (
        (
            await session.execute(
                select(Travel).where(Travel.edge_id == edge.id, Travel.state == TravelState.GOING)
            )
        )
        .scalars()
        .all()
    )
    died = 0
    for transit in going:
        body = await session.get(Body, transit.body_id, with_for_update=True)
        if body is None or body.state is not BodyState.ALIVE:  # pragma: no cover
            continue
        pocket = await world.body_container(session, body)
        #: Taken under the lock like the things in the fields, and for symmetry
        #: rather than for a known race: a body in transit is not putting
        #: anything down (`travel.require_here` refuses everything in-person),
        #: so nobody should be touching this pocket. "Should" is not a lock.
        held = (
            (
                await session.execute(
                    select(Item)
                    .where(Item.container_id == pocket.id)
                    .order_by(Item.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        await _consume(session, [thing for thing in held if thing.container_id == pocket.id])
        await death.die(session, constants, body, cause="rift", now=now)
        died += 1
    return died


async def _bridge(
    session: AsyncSession,
    constants: Constants,
    dice: random.Random,
    node: Node,
    ways: dict[uuid.UUID, set[uuid.UUID]],
) -> bool:
    """A new way from the shaken node to a place it could not reach before.

    The planet takes as it gives, and it gives on the same roll it takes on. A
    bridge is a cooled flow, so it is a trail: nobody laid it, the lava did.
    It never touches the plateau or the ground a ship stands on -- those are
    outside every draw, and a new edge is a change to a node as much as a lost
    one is.
    """
    if dice.random() > constants[R.PYROXIS_EDGE_REDRAW_SHARE]:
        return False
    spared = await _exempt(session)
    far = [
        one
        for one in await _surface(session)
        if one.id != node.id and one.id not in ways.get(node.id, set()) and one.id not in spared
    ]
    if not far:
        return False
    where = dice.choice(far)
    #: As long as the distance says (D-180): a bridge of cooled lava is a way
    #: through the wild, and the wild is measured the same way everywhere.
    await travel.connect(
        session,
        node,
        where,
        base_seconds=travel.frontier_seconds(constants, travel.reach_of(where) + 1),
        surface=Surface.TRAIL,
    )
    ways.setdefault(node.id, set()).add(where.id)
    ways.setdefault(where.id, set()).add(node.id)
    return True
