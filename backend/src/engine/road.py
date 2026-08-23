# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Roads: surface as work on an edge (D-107, D-158).

Exploration grows the map (D-156), the convoy hauls cargo along it (D-157) --
and at the junction a hole appears: offroad leads to the found node, and
offroad lets no vehicle through at all. The map grew **impassable**, and there
was nothing to turn a trail into a road with.

## Laying

Whoever stands at one end of the edge spends `road.surface_per_edge` of road
surface and `road.build_hours` of time, and the surface rises **by a tier**:

    offroad -> road -> paved highway

Each tier is a separate project and a separate forty units of surface. The
work runs as a journal job, like every long-running one: the surface is
written off at once, the road is laid on schedule, and a closed tab does not
stop it.

## Overgrowing

A surface has a condition 0..100. It falls by `road.decay_rate` per day, and
at zero the surface drops a tier: a highway becomes a road, a road a trail. An
abandoned road returns to offroad in about a hundred days.

**Resurfacing** raises the condition back and costs surface in exactly the
share by which the road sagged: one that sagged by half needs half a laying.

## Why on the edge, not the node

A road-as-building on a node would make connectivity a property of a point,
and geography would reduce to "developed" and "undeveloped" places. A road on
an edge is a relation between two places: it can be fought over, it can be
cut, and it goes to whoever invested in a **direction**, not a point.

## What is not here

**Edge ownership and tolls.** `road.toll_max` exists in the vault, but there
is nobody to charge for passage: the road has no owner, and creating one
silently would decide for game design who gets the shared work. Awaits its
decision (D-107).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events, world
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.job import Job, JobKind, JobState
from src.models.world import Edge, Node, Surface
from src.units import AMOUNT_SCALE, SCALE_MAX, SCALE_MIN, amount, amount_float

#: The thing class of consumables a surface is laid from (D-107, D-215).
SURFACE_GOODS = "Полотно"

#: Surface tiers from bottom to top. The order is the laying ladder itself.
LADDER = (Surface.TRAIL, Surface.ROAD, Surface.PAVED)


class RoadError(Exception):
    pass


class NotHere(RoadError):
    """The edge is not from here. A road is laid on foot, standing at one of its ends."""


class TopSurface(RoadError):
    """There is no surface above a highway: the ladder ended."""


class NoSurfaceGoods(RoadError):
    """Not enough surface. A road is materials, not intent."""


class AlreadyWorking(RoadError):
    """Work is already going on this edge. Two crews do not lay one road."""


def next_step(surface: Surface) -> Surface:
    """The next surface tier. The highway is the ceiling."""
    place = LADDER.index(surface)
    if place + 1 >= len(LADDER):
        raise TopSurface("мощёный тракт — верх лестницы: выше класть нечего")
    return LADDER[place + 1]


def lower_step(surface: Surface) -> Surface | None:
    """A tier down: an overgrown road. There is nothing below offroad."""
    place = LADDER.index(surface)
    return LADDER[place - 1] if place > 0 else None


async def pending(session: AsyncSession, edge: Edge) -> Job | None:
    """The ongoing work on this edge, if any."""
    return (
        await session.execute(
            select(Job).where(
                Job.kind == JobKind.ROAD_WORK.value,
                Job.state == JobState.PENDING,
                Job.payload["edge"].astext == str(edge.id),
            )
        )
    ).scalars().first()


def needed(constants: Constants, edge: Edge, *, mend: bool) -> float:
    """How much surface the work takes: laying -- the full norm, resurfacing -- a share.

    A road that sagged by half needs half a laying: paying for maintenance as
    for construction would make maintenance never worthwhile.
    """
    norm = constants[R.ROAD_SURFACE_PER_EDGE]
    if not mend:
        return norm
    sagged = (SCALE_MAX - float(edge.condition)) / SCALE_MAX
    return norm * sagged


async def lay(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    edge: Edge,
    *,
    mend: bool = False,
    now: datetime | None = None,
) -> Job:
    """Lay a surface tier or resurface a sagged road.

    Surface is written off up front, like batch materials: work that lacked
    material does not start at all.
    """
    from src.engine import travel

    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise RoadError("мёртвое тело дорог не кладёт")
    await travel.require_here(session, body)

    if body.node_id not in (edge.node_a_id, edge.node_b_id):
        raise NotHere("дорогу кладут стоя в одном из концов ребра")
    if mend:
        if float(edge.condition) >= SCALE_MAX:
            raise RoadError("дорога цела: подсыпать нечего")
        if edge.surface is Surface.TRAIL:
            raise RoadError("бездорожью подсыпать нечего: сначала уложить дорогу")
        goal = edge.surface
    else:
        goal = next_step(edge.surface)
    if await pending(session, edge) is not None:
        raise AlreadyWorking("на этом ребре уже идёт работа: дождитесь конца")

    #: The check comes before the write-off: a refusal must not eat half the surface.
    need_amount = needed(constants, edge, mend=mend)
    in_hands = await _surface_at_hand(session, body)
    if in_hands + _EPS < need_amount:
        raise NoSurfaceGoods(
            f"нужно {need_amount:.0f} «{SURFACE_GOODS}», а в руках {in_hands:.0f}: "
            "дорога — это материалы, а не намерение"
        )
    written_off = await _take_surface(session, body, need_amount)

    ready_ = moment + timedelta(hours=constants[R.ROAD_BUILD_HOURS])
    event = await events.record(
        session,
        EventKind.ROAD_WORK_STARTED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        edge_id=str(edge.id),
        surface=goal.value,
        mend=mend,
        spent=written_off,
        ready_at=ready_.isoformat(),
    )
    job = await enqueue(
        session,
        JobKind.ROAD_WORK,
        ready_,
        payload={"edge": str(edge.id), "surface": goal.value, "mend": mend},
        dedup_key=f"road.work:{edge.id}:{event.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover -- the key is unique per event
        raise AlreadyWorking("работа уже поставлена")
    return job


@handler(JobKind.ROAD_WORK)
async def finished(session: AsyncSession, job: Job) -> None:
    """Work is done: the surface rose, the condition is as new."""
    edge = await session.get(Edge, uuid.UUID(job.payload["edge"]))
    if edge is None:  # pragma: no cover -- an edge is eternal, like the map
        raise RoadError(f"задание {job.id}: ребра нет")

    before = edge.surface
    edge.surface = Surface(job.payload["surface"])
    edge.condition = Decimal(str(SCALE_MAX))
    await session.flush()

    await events.record(
        session,
        EventKind.ROAD_LAID,
        edge_id=str(edge.id),
        was=before.value,
        surface=edge.surface.value,
        mend=bool(job.payload.get("mend")),
    )


async def decay(session: AsyncSession, constants: Constants) -> int:
    """Daily overgrowing. Returns the number of edges that lost a tier.

    A road nobody tends returns to offroad in about a hundred days. That is
    the very constant sink of materials which maintenance exists for at all (D-107).
    """
    edges = (
        await session.execute(select(Edge).where(Edge.surface != Surface.TRAIL))
    ).scalars().all()

    step = constants[R.ROAD_DECAY_RATE]
    overgrown = 0
    for edge in edges:
        left = float(edge.condition) - step
        if left > SCALE_MIN:
            edge.condition = Decimal(str(left))
            continue
        below = lower_step(edge.surface)
        if below is None:  # pragma: no cover -- the trail is filtered out by the query
            continue
        before = edge.surface
        edge.surface = below
        #: A sagged surface exposes what is under it: the new tier starts with
        #: fresh condition, not zero -- otherwise a road would crumble down to
        #: offroad in two days.
        edge.condition = Decimal(str(SCALE_MAX))
        overgrown += 1
        await events.record(
            session,
            EventKind.ROAD_DECAYED,
            edge_id=str(edge.id),
            was=before.value,
            surface=below.value,
        )
    await session.flush()
    return overgrown


async def view(
    session: AsyncSession, constants: Constants, body: Body
) -> list[dict]:
    """Edges from this node through the client's eyes: what is laid and what can be laid."""
    from src.engine import travel

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body always stands in a node
        return []
    edges = (
        await session.execute(
            select(Edge).where(
                or_(Edge.node_a_id == node.id, Edge.node_b_id == node.id)
            )
        )
    ).scalars().all()

    in_hands = await _surface_at_hand(session, body)
    result: list[dict] = []
    for edge in edges:
        other = await session.get(
            Node, edge.node_b_id if edge.node_a_id == node.id else edge.node_a_id
        )
        if other is None:  # pragma: no cover -- an edge to nowhere is a bug
            continue
        try:
            further: str | None = next_step(edge.surface).value
            need_amount: float | None = needed(constants, edge, mend=False)
        except TopSurface:
            further, need_amount = None, None
        resurface = (
            None
            if edge.surface is Surface.TRAIL or float(edge.condition) >= SCALE_MAX
            else needed(constants, edge, mend=True)
        )
        result.append(
            {
                "edge": str(edge.id),
                "to": other.name,
                "surface": edge.surface.value,
                "condition": float(edge.condition),
                "seconds": round(travel.edge_seconds(constants, edge)),
                "next": further,
                "needs": need_amount,
                "mend_needs": resurface,
                "at_hand": in_hands,
                "working": await pending(session, edge) is not None,
            }
        )
    return sorted(result, key=lambda path: path["to"])


#: Surface splits into thousandths, like every raw material: the "was it
#: enough" comparison must tolerate the last digit, otherwise exactly forty
#: units turn out insufficient due to representation.
_EPS = 1 / AMOUNT_SCALE


async def _surface_at_hand(session: AsyncSession, body: Body) -> float:
    pocket = await world.body_container(session, body)
    stacks = (
        await session.execute(
            select(Item).where(
                Item.container_id == pocket.id,
                Item.type_key.in_(world.station_names(SURFACE_GOODS)),
            )
        )
    ).scalars().all()
    return sum(amount_float(stack.amount) for stack in stacks)


async def _take_surface(session: AsyncSession, body: Body, need_amount: float) -> float:
    """Write off surface from the hands. Returns how much could be taken."""
    pocket = await world.body_container(session, body)
    stacks = await world.locked_stacks(
        session, pocket.id, world.station_names(SURFACE_GOODS)
    )
    return amount_float(await world.consume(session, stacks, amount(need_amount)))
