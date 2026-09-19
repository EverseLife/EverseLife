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

from sqlalchemy import case, inspect, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current
from src.constants import registry as R
from src.engine import biome, events, occupation, stock, travel, works, world
from src.engine.city import land as city_land
from src.engine.city import line as city_line
from src.engine.errors import Refusal
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.job import Job, JobKind, JobState
from src.models.world import Edge, Node, Surface
from src.units import AMOUNT_SCALE, SCALE_MAX, SCALE_MIN, amount, amount_float

#: The thing class of consumables a surface is laid from (D-107, D-215).
SURFACE_GOODS = "roadbed"

#: Surface tiers from bottom to top. The order is the laying ladder itself.
LADDER = (Surface.WILD, Surface.TRAIL, Surface.ROAD, Surface.PAVED)
#: The two rungs no crew lays (D-319): the wild is the world's, the trail is
#: the feet's, and neither has a condition to mend or to lose.
UNPAVED = (Surface.WILD, Surface.TRAIL)


class RoadError(Refusal):
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
    """The next surface a crew can lay. The highway is the ceiling.

    Work starts at the road: a trail is worn, never laid (D-319), so from the
    wild and from a trail alike the first thing a crew makes is a road.
    """
    if surface in UNPAVED:
        return Surface.ROAD
    place = LADDER.index(surface)
    if place + 1 >= len(LADDER):
        raise TopSurface(key="road-top-surface")
    return LADDER[place + 1]


def lower_step(surface: Surface) -> Surface | None:
    """A tier down: an overgrown road. Below the road only feet decide (D-319)."""
    if surface in UNPAVED:
        return None
    place = LADDER.index(surface)
    return LADDER[place - 1]


async def pending(session: AsyncSession, edge: Edge) -> Job | None:
    """The ongoing work on this edge, if any."""
    return (
        (
            await session.execute(
                select(Job).where(
                    Job.kind == JobKind.ROAD_WORK.value,
                    Job.state == JobState.PENDING,
                    Job.payload["edge"].astext == str(edge.id),
                )
            )
        )
        .scalars()
        .first()
    )


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

    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise RoadError(key="road-dead")
    await travel.require_here(session, body)

    if body.node_id not in (edge.node_a_id, edge.node_b_id):
        raise NotHere(key="road-stand-at-an-end")
    if mend:
        if float(edge.condition) >= SCALE_MAX:
            raise RoadError(key="road-intact")
        if edge.surface in UNPAVED:
            raise RoadError(key="road-trail-not-mended")
        goal = edge.surface
    else:
        goal = next_step(edge.surface)
    if await pending(session, edge) is not None:
        raise AlreadyWorking(key="road-edge-busy")
    #: Laying a surface is an occupation (D-310): a day of these hands, the same
    #: as a build. Asked after the edge's own guard, so that a crew already on
    #: this very road hears about the road rather than about itself -- and
    #: before the write-off, because a refusal must eat nothing.
    await occupation.require_free(session, body)

    #: The check comes before the write-off: a refusal must not eat half the surface.
    need_amount = needed(constants, edge, mend=mend)
    in_hands = await _surface_at_hand(session, body)
    if in_hands + _EPS < need_amount:
        raise NoSurfaceGoods(
            key="road-no-goods",
            need=need_amount,
            goods=SURFACE_GOODS,
            have=in_hands,
        )
    written_off, paving = await _take_surface(session, constants, body, need_amount)

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
        paving=paving,
        ready_at=ready_.isoformat(),
    )
    job = await enqueue(
        session,
        JobKind.ROAD_WORK,
        ready_,
        payload={"edge": str(edge.id), "surface": goal.value, "mend": mend, "paving": paving},
        dedup_key=f"road.work:{edge.id}:{event.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    if job is None:  # pragma: no cover -- the key is unique per event
        raise AlreadyWorking(key="road-already-queued")
    return job


@handler(JobKind.ROAD_WORK)
async def finished(session: AsyncSession, job: Job) -> None:
    """Work is done: the surface rose, the condition is as new."""
    edge = await session.get(Edge, uuid.UUID(job.payload["edge"]))
    if edge is None:  # pragma: no cover -- an edge is eternal, like the map
        raise RoadError(key="road-job-no-edge", job=str(job.id))

    before = edge.surface
    edge.surface = Surface(job.payload["surface"])
    edge.condition = Decimal(str(SCALE_MAX))
    #: The edge remembers what it was laid from (D-252): decay reads its
    #: multiplier off the mark. A mend overwrites it too -- what you patch
    #: with is what the road is covered with now. Jobs queued before the
    #: mark existed carry none and change nothing.
    paving = job.payload.get("paving")
    if paving:
        edge.paving = paving
    await session.flush()

    #: The crew is the actor: the event reaches it live (`push.pump`, by
    #: party) and on return (`world.TOLD`, by actor). Without one it reached
    #: nobody -- an edge is no room, and the digest asked by actor found no
    #: row -- and the crew learnt of its own road at its next step.
    worker = None if job.body_id is None else await session.get(Body, job.body_id)
    await events.record(
        session,
        EventKind.ROAD_LAID,
        actor_identity_id=None if worker is None else worker.identity_id,
        edge_id=str(edge.id),
        was=before.value,
        surface=edge.surface.value,
        mend=bool(job.payload.get("mend")),
        paving=edge.paving,
    )
    #: A paved way from a city's land takes the node at its far end into the
    #: city (D-332): the city grows where it paves. Asked whenever the work
    #: leaves the edge paved -- a mend of a paved way laid before the rule
    #: takes the node in as well; a road is not enough. The crew is the
    #: event's actor: it is told on return that the land it paved to is the
    #: city's now. Its ends are locked before the pay below takes the
    #: accounts, the order a purchase keeps (node, then money); the city's
    #: line is asked after both, last (`city.cover`).
    paved = edge.surface is Surface.PAVED
    if paved:
        await city_land.annex_by_way(
            session,
            current(),
            edge,
            by=None if worker is None else worker.identity_id,
            ask_line=False,
        )
    #: A mend with an open state order on this edge collects its pay (D-248):
    #: the engine just verified the work in its own data -- the condition is
    #: back at full. Laying a new tier is a different project, no order pays for it.
    if bool(job.payload.get("mend")):
        await works.pay_road_order(
            session,
            current(),
            edge,
            None if worker is None else worker.identity_id,
            now=job.run_at,
        )
    if paved:
        ends = [await session.get(Node, end) for end in (edge.node_a_id, edge.node_b_id)]
        if all(end is not None for end in ends):
            await city_line.cover_way(session, current(), *ends)


async def decay(session: AsyncSession, constants: Constants) -> int:
    """Daily overgrowing. Returns the number of edges that lost a tier.

    A road nobody tends returns to offroad in about a hundred days. That is
    the very constant sink of materials which maintenance exists for at all (D-107).
    """
    edges = (
        (await session.execute(select(Edge).where(Edge.surface.not_in(UNPAVED)))).scalars().all()
    )

    step = constants[R.ROAD_DECAY_RATE]
    #: By what the edge was laid from (D-252): asphalt sags at half the pace
    #: of gravel, and that is the whole reason it is a separate paving. An
    #: unmarked edge -- the world's own road, laid by nobody -- goes at the
    #: base rate, like a paving the table does not name.
    slower = constants[R.ROAD_DECAY_BY_PAVING]
    overgrown = 0
    for edge in edges:
        left = float(edge.condition) - step * float(slower.get(edge.paving, 1.0))
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
        #: offroad in two days. The covering is gone with the tier, and the
        #: mark goes with it (D-252).
        edge.condition = Decimal(str(SCALE_MAX))
        edge.paving = None
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


async def tread(
    session: AsyncSession,
    constants: Constants,
    edge_id: uuid.UUID,
    *,
    node_id: uuid.UUID | None = None,
) -> bool:
    """One more pair of feet over the edge (D-319). Returns whether it became a trail.

    Written by the arrival job and by nothing else -- a read does not write.
    One statement, no read before it: the wear is a counter, and two arrivals
    in the same second must both count (`wear = wear + 1`); whether the
    threshold is crossed is decided in the same statement, on the value the
    database has, not on the one this session read a moment ago.
    """
    threshold = int(constants[R.PATH_WEAR_THRESHOLD])
    trodden = (Edge.surface == Surface.WILD) & (Edge.wear + 1 >= threshold)
    row = (
        await session.execute(
            update(Edge)
            .where(Edge.id == edge_id)
            .values(
                wear=Edge.wear + 1,
                surface=case((trodden, Surface.TRAIL.value), else_=Edge.surface),
            )
            .returning(Edge.surface, Edge.wear)
        )
    ).one_or_none()
    if row is None:  # pragma: no cover -- the leg's edge is gone with its gangway (D-201)
        return False
    surface, wear = row
    became_trail = Surface(surface) is Surface.TRAIL and wear == threshold
    if became_trail:
        await events.record(session, EventKind.ROAD_TRODDEN, node_id=node_id, edge_id=str(edge_id))
    return became_trail


async def fade(session: AsyncSession, constants: Constants) -> int:
    """Daily: every edge is a little less trodden, and a forgotten trail grows over.

    Returns how many trails went back to the wild. Two marks, not one
    (`path.fade_threshold` under `path.wear_threshold`), so an edge walked
    about once a day does not flicker between the two every tick.
    """
    per_day = int(constants[R.PATH_FADE_PER_DAY])
    floor = int(constants[R.PATH_FADE_THRESHOLD])
    await session.execute(
        update(Edge)
        .where(Edge.wear > 0)
        .values(wear=case((Edge.wear > per_day, Edge.wear - per_day), else_=0))
    )
    overgrown = (
        (
            await session.execute(
                update(Edge)
                .where(Edge.surface == Surface.TRAIL, Edge.wear < floor)
                .values(surface=Surface.WILD.value)
                .returning(Edge.id)
            )
        )
        .scalars()
        .all()
    )
    for edge_id in overgrown:
        await events.record(session, EventKind.ROAD_OVERGROWN, edge_id=str(edge_id))
    return len(overgrown)


async def view(session: AsyncSession, constants: Constants, body: Body) -> list[dict]:
    """Edges from this node through the client's eyes: what is laid and what can be laid."""

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body always stands in a node
        return []
    edges = (
        (
            await session.execute(
                select(Edge).where(or_(Edge.node_a_id == node.id, Edge.node_b_id == node.id))
            )
        )
        .scalars()
        .all()
    )

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
            if edge.surface in UNPAVED or float(edge.condition) >= SCALE_MAX
            else needed(constants, edge, mend=True)
        )
        result.append(
            {
                "edge": str(edge.id),
                #: The neighbour by key as well as by name: a name is not an
                #: identifier (D-251) and a domed city has fifteen rooms called
                #: «Квартира», so a column that picked the road by the name
                #: showed every one of them -- and a found node, which has no
                #: name at all (D-321), matched nothing and showed the lot.
                "node": other.key,
                #: The name comes with the road and is not left to the client to
                #: look up (D-225 asks whether it could): this answer is read on
                #: its own, by a window that has not been given the map and may
                #: be drawn before the map has arrived at all. A road whose
                #: neighbour had no name yet would read as a road to nowhere --
                #: and a find has no name at all, so it is spoken of by its
                #: biome, in the word the refusals use (D-321).
                "to": biome.word_of(constants, other),
                "surface": edge.surface.value,
                "condition": float(edge.condition),
                #: The way as laid (D-338): what a road would cut is the
                #: surface's, and the season's snow is the walk's.
                "seconds": round(travel.edge_seconds(constants, edge, snow=0.0)),
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
        (
            await session.execute(
                select(Item).where(
                    Item.container_id == pocket.id,
                    Item.type_key.in_(world.station_names(SURFACE_GOODS)),
                )
            )
        )
        .scalars()
        .all()
    )
    return sum(amount_float(stack.amount) for stack in stacks)


async def _take_surface(
    session: AsyncSession, constants: Constants, body: Body, need_amount: float
) -> tuple[float, str | None]:
    """Write off surface from the hands. Returns (taken, the dominant kind).

    Kinds may mix in one laying -- the norm is taken off whatever stacks of
    the class are carried, as before D-252 -- and the edge is marked by the
    kind that made up most of it. A tie goes to the slower-sagging one: the
    builder who brought half asphalt gets the benefit of the doubt.
    """
    pocket = await world.body_container(session, body)
    stacks = await stock.locked_stacks(session, pocket.id, world.station_names(SURFACE_GOODS))
    before = {stack.id: (stack.type_key, amount_float(stack.amount)) for stack in stacks}
    taken = amount_float(await stock.consume(session, stacks, amount(need_amount)))
    spent: dict[str, float] = {}
    for stack in stacks:
        kind, had = before[stack.id]
        #: A stack drained whole is deleted with its amount untouched
        #: (`stock.consume`): what it still shows is not what is left of it.
        left_now = 0.0 if inspect(stack).deleted else amount_float(stack.amount)
        spent[kind] = spent.get(kind, 0.0) + had - left_now
    slower = constants[R.ROAD_DECAY_BY_PAVING]
    dominant = max(
        (kind for kind, used in spent.items() if used > 0),
        key=lambda kind: (spent[kind], -float(slower.get(kind, 1.0))),
        default=None,
    )
    return taken, dominant
