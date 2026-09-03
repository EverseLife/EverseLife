# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The walk itself: the route over the graph, the departure that spends
stamina and takes the body out of the world, the turn back at the price of
the way already walked, and the arrival the journal fires exactly once.
"""

from __future__ import annotations

import heapq
import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current_catalog
from src.constants import current as constants_now
from src.engine import (
    access,
    bank,
    chat,
    craft,
    customs,
    events,
    food,
    justice,
    transport,
)
from src.engine.errors import left_to_say
from src.engine.jobs import enqueue, handler
from src.engine.travel._base import (
    AlreadyGoing,
    Imprisoned,
    NoEdge,
    NoRoute,
    NoStrength,
    NotGoing,
    TravelError,
    _edge_between,
    current,
    edge_seconds,
    has_transport,
    require_here,
    stamina_cost,
)
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.job import Job, JobKind, JobState
from src.models.travel import Travel, TravelState
from src.models.world import Edge, Node
from src.units import ROUND_REMAINDER, ROUND_STAMINA, on_grid


async def route(
    session: AsyncSession,
    constants: Constants,
    from_node_id: uuid.UUID,
    to_node_id: uuid.UUID,
    *,
    vehicle: str | None = None,
) -> list[uuid.UUID]:
    """The fastest path by time between nodes: a list of nodes, without the start.

    Autopath (D-045) is a convenience, not new physics: the route consists of
    the same edges, is walked in the same time and can be walked by hand leg by
    leg. Dijkstra by seconds with surface in mind; the whole graph in memory --
    it is small, and when it grows large there will be a reason for indexes.

    With a convoy the graph is poorer: offroad lets no vehicle through at all,
    and a heavy one needs a paved highway (D-107). The route is built over
    passable edges -- no point leading a carter into a dead end to stop there.

    Somebody else's shut location is **not** cut out of the graph (D-204):
    shutting stops entry, not passage, so the route goes straight through it.
    The path therefore does not depend on who walks it -- only the destination
    does, and it is checked by `depart`, where a refusal can name the reason
    instead of reporting "no road at all".
    """

    edges = (await session.execute(select(Edge))).scalars().all()
    graph: dict[uuid.UUID, list[tuple[uuid.UUID, float]]] = {}
    for edge in edges:
        if vehicle is not None and not transport.passable(constants, edge.surface, vehicle):
            continue
        seconds = edge_seconds(constants, edge)
        if vehicle is not None:
            seconds /= transport.speed(constants, vehicle)
        graph.setdefault(edge.node_a_id, []).append((edge.node_b_id, seconds))
        graph.setdefault(edge.node_b_id, []).append((edge.node_a_id, seconds))

    best: dict[uuid.UUID, float] = {from_node_id: 0.0}
    came: dict[uuid.UUID, uuid.UUID] = {}
    queue: list[tuple[float, bytes]] = [(0.0, from_node_id.bytes)]
    while queue:
        cost, raw = heapq.heappop(queue)
        here = uuid.UUID(bytes=raw)
        if here == to_node_id:
            break
        if cost > best.get(here, float("inf")):
            continue
        for neighbour, seconds in graph.get(here, ()):
            step = cost + seconds
            if step < best.get(neighbour, float("inf")):
                best[neighbour] = step
                came[neighbour] = here
                heapq.heappush(queue, (step, neighbour.bytes))

    if to_node_id not in best:
        raise NoRoute(key="travel-no-route", how="foot" if vehicle is None else "convoy")
    path: list[uuid.UUID] = []
    cursor = to_node_id
    while cursor != from_node_id:
        path.append(cursor)
        cursor = came[cursor]
    path.reverse()
    return path


async def depart(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    target: Node,
    *,
    now: datetime | None = None,
    _plan: list[uuid.UUID] | None = None,
) -> Travel:
    """Go to a node. To a non-adjacent one by autopath: the route builds itself (D-045).

    From then on the transit goes by itself, including offline: each leg is a
    journal job, and the leg's arrival itself sends the body into the next.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise TravelError(key="travel-dead-goes-nowhere")
    if await current(session, body) is not None:
        raise AlreadyGoing(key="travel-already-going")
    #: Setting out is an in-person start, and its door is **the same** as for
    #: all in-person actions: a sleeper does not go (D-091), a scout does not go
    #: (D-152) -- they are not in the node, they are in the field. Keeping this
    #: list as a separate copy would mean forgetting a line in it sooner or
    #: later: the scout did exactly that, walking away while staying "in the field".
    await require_here(session, body)
    if target.id == body.node_id:
        raise NoEdge(key="travel-same-node")

    #: Imprisonment is a forced restriction of movement to the node (D-095,
    #: D-166). The engine enforces it, not guards: the verdict does not depend
    #: on whether anyone is online.

    sits = await justice.imprisoned(session, body.identity_id)
    if sits is not None:
        raise Imprisoned(
            key="travel-imprisoned",
            term="date" if sits.until else "verdict",
            #: A term is told as how long is left, like every other deadline:
            #: the day here is of the world's own length (D-029), and an ISO
            #: stamp in it is a conversion nobody does in their head.
            inner={} if sits.until is None else {"left": [left_to_say(sits.until)]},
        )

    #: Insolvency holds in the node the same way, but it is imposed not by the
    #: authority but by the banking system: world physics, not a verdict (D-063, D-168).

    holds = await bank.restrained(session, constants, body.identity_id, now=moment)
    if holds is not None:
        raise Imprisoned(key="travel-in-default")

    #: Somebody else's shut location is refused at departure, not at the fence
    #: (D-199): a road one cannot finish is not worth setting out on. Only the
    #: destination is refused -- the legs in between are passage, and passage is
    #: free (D-204).

    await access.require_entry(session, target, body)

    #: A convoy changes both speed and the passability of edges (D-107, D-157).

    convoy = await transport.harnessed(session, body)

    plan = list(_plan or [])
    edge = await _edge_between(session, body.node_id, target.id)
    if edge is None:
        #: No adjacent edge -- build a route. The first leg is walked now, the
        #: tail goes into the plan and is walked by leg arrivals.
        legs = await route(
            session,
            constants,
            body.node_id,
            target.id,
            vehicle=None if convoy is None else convoy.type_key,
        )
        edge = await _edge_between(session, body.node_id, legs[0])
        assert edge is not None  # noqa: S101 -- a route consists of edges
        next_node = await session.get(Node, legs[0])
        if next_node is None:  # pragma: no cover -- the route is over live nodes
            raise TravelError(key="travel-route-node-gone")
        target = next_node
        plan = legs[1:] + plan

    #: Nothing to breathe where the leg ends (D-233): refused **before** the
    #: step, never at the far end -- death by ignorance in one click is not this
    #: world's way. The leg and not the destination, because every leg is
    #: departed in its turn and each one asks this for itself.
    #:
    #: Lazy: `oxygen` reads the hull through `engine.ship`, and that reaches
    #: back here for docking and the gangway.
    from src.engine import oxygen  # noqa: PLC0415 -- lazy: breaks the cycle with ship

    await oxygen.require_air(
        session,
        constants,
        current_catalog(),
        body,
        target,
        #: The leg's own length: a cylinder must hold the whole of the walk, not
        #: merely be non-empty when it starts.
        seconds=edge_seconds(constants, edge),
    )

    #: Left the workshop -- left the conversation: the circle does not follow (D-043).

    await chat.leave_groups(session, body.identity_id)

    seconds = edge_seconds(constants, edge)
    if convoy is not None:
        #: Surface decides not only time but the very possibility to drive through.
        if not transport.passable(constants, edge.surface, convoy.type_key):
            raise transport.Impassable(
                key="travel-impassable",
                vehicle=convoy.type_key,
                surface=edge.surface.value,
            )
        seconds /= transport.speed(constants, convoy.type_key)

    #: The border is settled **before** leaving: both sides are already known,
    #: and paying on arrival would let into the city what cannot be paid for (D-123).

    origin_node = await session.get(Node, body.node_id)
    if origin_node is not None:
        await customs.cross(
            session,
            constants,
            current_catalog(),
            body,
            origin_node,
            target,
            now=moment,
        )

    #: The reserve is settled **before** the body steps out (D-231): until this
    #: moment it stood in this node and warmed or froze by it, and on the road
    #: there is no shelter at all. Settled here rather than on arrival, because
    #: only here is it still known where the hours were spent.
    from src.engine import frost  # noqa: PLC0415 -- lazy: breaks the cycle with frost

    await frost.settle(session, constants, current_catalog(), body, now=moment)
    #: And the breathing, for the same reason and at the same moment: the hours
    #: just spent were spent **here**, and only here is it known whether here
    #: had air (D-233).
    await oxygen.settle(session, constants, current_catalog(), body, now=moment)

    #: The road costs stamina, and it is paid up front (D-147). Satiety slows
    #: the spend exactly as at work: lunch is lunch, and the cold makes every
    #: step dearer the same way (D-231).

    spend = (
        stamina_cost(constants, seconds, transport=await has_transport(session, body))
        * food.drain_multiplier(constants, body, moment)
        * await frost.drain_multiplier(session, constants, body)
    )
    if spend > float(body.stamina):
        raise NoStrength(key="travel-no-strength", need=spend, have=float(body.stamina))
    #: What the last steps cost and the column could not be charged for is
    #: paid with this one. Stamina keeps hundredths and the road is priced by
    #: time, so a step under nine seconds costs less than half of one -- and
    #: most paved edges in a city are shorter than that, to say nothing of a
    #: ship's corridor. Charged and rounded away, the step was free; the
    #: engine believed it had taken the strength and the row disagreed.
    #: Both sides on the grid before they are compared, and not merely the
    #: answer: capped by the reserve, what is left owing is `owed` less what
    #: the reserve could give, and that stays under a hundredth only while the
    #: reserve itself sits on the grid. It does today -- `frost.settle` above
    #: re-read the row -- but a bound that holds because of what someone else
    #: did two lines up is not held at all, and breaking it is not a refusal
    #: in words but the check rejecting the write.
    #:
    #: The row is locked for the whole command by `_alive`, and by
    #: `session.get(..., with_for_update=True)` on the worker's path; the
    #: reserve and its debt are one pair and want one lock.
    have = float(on_grid(body.stamina, ROUND_STAMINA, ROUND_FLOOR))
    owed = spend + float(body.stamina_owed)
    takes = float(on_grid(min(owed, have), ROUND_STAMINA, ROUND_FLOOR))
    body.stamina = on_grid(have - takes, ROUND_STAMINA)
    body.stamina_owed = on_grid(max(0.0, owed - takes), ROUND_REMAINDER, ROUND_FLOOR)

    travel = Travel(
        body_id=body.id,
        from_node_id=body.node_id,
        to_node_id=target.id,
        edge_id=edge.id,
        plan=[str(node_id) for node_id in plan] or None,
        arrives_at=moment + timedelta(seconds=seconds),
    )
    session.add(travel)
    await session.flush()

    #: The master left the machine: the running batch freezes with the time
    #: left in it and frees the bench (D-209). Not on the plan's later legs --
    #: there the body has already left, and there is nothing running.
    if _plan is None:
        await craft.freeze(session, body, now=moment)
        #: And the wall the mason was mending: a repair runs only while the
        #: body stands in the node, and what is left of it waits here for
        #: whoever comes back (D-211).
        from src.engine import estate  # noqa: PLC0415 -- lazy: estate imports travel

        await estate.pause(session, body, now=moment)

    event = await events.record(
        session,
        EventKind.TRAVEL_STARTED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        travel_id=str(travel.id),
        to_node=target.key,
        seconds=seconds,
        surface=edge.surface.value,
        stamina=spend,
    )
    await enqueue(
        session,
        JobKind.TRAVEL_LEG,
        travel.arrives_at,
        payload={"travel": str(travel.id)},
        dedup_key=f"travel.leg:{travel.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    return travel


async def turn_back(
    session: AsyncSession, body: Body, *, forced: bool = False, now: datetime | None = None
) -> Travel:
    """Turn back from the road: the body stays where it left from (D-194).

    There is no half of an edge in this world -- a node is the unit of place,
    so cancelling returns rather than stops midway. What was spent is not
    returned: stamina was written off up front and time has passed.

    The autopath tail goes with it: otherwise the body would walk on along a
    plan its owner has already cancelled.

    One place one does not turn back from: somebody else's shut location one is
    only passing through (D-204). Turning back there would leave the body
    standing where the holder does not let it stop -- and standing means the
    floor, the chest and everything else the door was shut for. Passage is
    walked to its end.

    `forced` is the world doing the turning rather than the walker: a floor
    losing its walls under somebody on the stairs (D-247) cannot be answered
    with "walk it to the end", because there is no end left to walk to. The
    door's rule holds against a **decision**, and a collapse is not one.
    """
    moment = now or datetime.now(UTC)
    going = await current(session, body)
    if going is None:
        raise NotGoing(key="travel-not-going")

    here = await session.get(Node, going.from_node_id)
    if (
        not forced
        and here is not None
        and not await access.may_enter(session, here, body.identity_id)
    ):
        raise access.Barred(key="travel-passage-not-turned", node=here.name)

    going.state = TravelState.CANCELLED
    going.plan = None
    going.arrived_at = moment
    await session.flush()

    #: The leg's job is dropped so that the arrival does not fire on schedule.
    legs = (
        (
            await session.execute(
                select(Job).where(
                    Job.kind == JobKind.TRAVEL_LEG.value,
                    Job.state == JobState.PENDING,
                    Job.payload["travel"].astext == str(going.id),
                )
            )
        )
        .scalars()
        .all()
    )
    for leg in legs:
        leg.state = JobState.CANCELLED
        leg.finished_at = moment
    await session.flush()

    await events.record(
        session,
        EventKind.TRAVEL_CANCELLED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        travel_id=str(going.id),
    )
    #: Never left after all: the frozen work goes on where the body still stands (D-209).

    await craft.wake(session, body, now=moment)
    return going


@handler(JobKind.TRAVEL_LEG)
async def arrive(session: AsyncSession, job: Job) -> None:
    """Arrived. The body moves to the new node together with everything it carries."""
    travel = await session.get(Travel, uuid.UUID(job.payload["travel"]))
    if travel is None:  # pragma: no cover
        raise TravelError(key="travel-job-no-leg", job=str(job.id))
    if travel.state is not TravelState.GOING:
        #: A job retry after a failure does not become a second arrival.
        return

    body = await session.get(Body, travel.body_id, with_for_update=True)
    target = await session.get(Node, travel.to_node_id)
    if body is None or target is None:  # pragma: no cover
        raise TravelError(key="travel-leg-nowhere", leg=str(travel.id))

    #: The road ends here, and the hours on it were the cold itself (D-231):
    #: the reserve is settled **before** the body takes the new node, or the
    #: stretch from the last tick to the arrival would be counted by the warmth
    #: of the place walked to rather than of the road walked.
    from src.engine import frost  # noqa: PLC0415 -- lazy: breaks the cycle with frost

    await frost.settle(session, constants_now(), current_catalog(), body, now=job.run_at)

    #: The inventory need not travel: it is bound to the body, not the place.
    #: Goods left in a terminal stay there -- things do not follow their owner.
    body.node_id = target.id
    #: Chat horizon: before arrival the body heard nothing here (D-043).
    body.node_since = job.run_at
    travel.state = TravelState.ARRIVED
    travel.arrived_at = job.run_at
    await session.flush()

    await events.record(
        session,
        EventKind.TRAVEL_ARRIVED,
        actor_identity_id=body.identity_id,
        node_id=target.id,
        travel_id=str(travel.id),
    )

    #: Back at a machine: a work frozen here goes on from where it stopped
    #: (D-209). Only when the road ends here -- a leg of a longer route sends
    #: the body straight on below, and it would freeze again at once.
    if not travel.plan:
        await craft.wake(session, body, now=job.run_at)

    #: The convoy arrived with the body and wore on this leg (D-157). One worn
    #: to zero stops here, and the cargo stays lying in the node.

    convoy = await transport.harnessed(session, body)
    broke = False
    if convoy is not None:
        await transport.follow(session, convoy, target)
        broke = await transport.wear_leg(
            session, constants_now(), current_catalog(), body, convoy, target
        )
        if broke:
            travel.plan = None
            await session.flush()

    #: Autopath: a leg's arrival itself sends the body into the next (D-045).
    #: The route is no shorter than by hand -- it only spares an alarm at every node.
    if travel.plan:
        next_node = await session.get(Node, uuid.UUID(travel.plan[0]))
        if next_node is None:  # pragma: no cover -- the route is over live nodes
            raise TravelError(key="travel-plan-node-gone", leg=str(travel.id))
        rest = [uuid.UUID(raw) for raw in travel.plan[1:]]

        #: Lazy for the same reason as in `depart`: `oxygen` reads the hull
        #: through `engine.ship`, which reaches back here.
        from src.engine import oxygen  # noqa: PLC0415 -- lazy: breaks the cycle with ship

        try:
            await depart(session, constants_now(), body, next_node, now=job.run_at, _plan=rest)
        except (
            NoStrength,
            customs.CustomsError,
            transport.Impassable,
            NoEdge,
            oxygen.NoAir,
        ) as stop:
            #: The route breaks off here -- not enough strength (D-147), the
            #: border did not let the cargo through (D-123), the road does not
            #: let the convoy through (D-107), the edge itself is gone (the ship
            #: undocked while the route was being walked, D-201), or the next
            #: node has no air and the body nothing to breathe it with (D-233).
            #: The body stays in the node rather than dropping mid-leg: got as
            #: far as allowed, the player decides the rest.
            await events.record(
                session,
                EventKind.TRAVEL_ARRIVED,
                actor_identity_id=body.identity_id,
                node_id=target.id,
                travel_id=str(travel.id),
                route_stopped=type(stop).__name__,
                why=str(stop),
            )
            #: The road ended here after all: whatever of theirs waited in
            #: this node goes on (D-209).

            await craft.wake(session, body, now=job.run_at)
