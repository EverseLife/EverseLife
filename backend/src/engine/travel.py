# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Transit between nodes (D-045, D-097, D-107).

The map is a **weighted graph**, not a grid: hence chokepoints, bridges and
passes worth fighting over. One can move only along an existing edge and only
on foot: this world has no teleport, neither for people nor for things.

## Where transit time comes from

**Surface decides everything.** `road.*_multiplier` are given as time
multipliers relative to the reference road: offroad is two to three times
longer, a paved highway faster. So transit time is the edge's own time times
the surface:

    time = base_seconds * road.<surface>_multiplier

An edge's own time is rolled when the map appears: inside a city from
`travel.city_step`, beyond the walls from the node's **distance** (D-180).
Stored in seconds so that a step across the quarter and a crossing of the
steppe do not live in different units.

## Distance: the farther from a city, the pricier the step (D-180)

Distance is a node property: how many transits it is from civic land. Built-up
area is 0, the first ring beyond the walls is 1, a find from a node of distance
`d` is `d + 1`. The transit length to it:

    base_seconds = travel.frontier_step * travel.frontier_growth ^ (d - 1)

The settled surroundings are thereby closer than the unexplored: the near mine
is walked to in twenty seconds, the far frontier requires an expedition.
Distance is stored in the node rather than computed over the graph: the map
grows in branches, and "how many steps to the nearest city" would have to be
recomputed on every find.

## The road costs stamina (D-147)

Time is a poor price: close the tab and you have arrived. So a transit has a
second price, and the body pays it:

    spend = travel.stamina_per_hour * road hours * satiety

The spend goes **by time**, not by number of transits: otherwise a step across
the quarter would cost as much as a crossing of the steppe, and geography would
turn inside out. The number is small -- an hour of walking is several times
cheaper than an hour at the face: the road tires but does not replace work.

With a convoy the spend is multiplied by `transport.stamina_k` = 0: the
vehicle carries, not the legs.

## A convoy changes both speed and the map itself (D-107, D-157)

A harnessed vehicle (`engine.transport`) does three things at once: carries
cargo in the hold, goes `transport.speed_k` times faster than on foot -- and
**narrows the graph**. Offroad lets no vehicle through at all, a heavy one
needs a paved highway, so autopath with a convoy is built over passable edges,
and a route that runs into the impassable stops at the last node -- the same
place it stops for lack of strength and at customs.

Hence the consequence all this was made for: **the road is a precondition of
trade, not a convenience.**

Written off **up front**, like batch materials: one cannot set out on a road
there is not enough strength for. On autopath this means the route breaks off
where strength sufficed -- the body stays in a node rather than dropping in the
middle of a leg.

## The graph changes in both directions (D-201)

Until the spaceship the map only grew: exploration added nodes with edges
(D-152), a road changed an edge's surface and overgrew without maintenance
(D-158), but no edge ever disappeared.

A ship is a **group of nodes of this same graph** with exactly one connector
node facing outwards, and docking is one edge between that connector and the
spaceport. So undocking is the removal of that one edge, and a flight is the
absence of it -- not a state of the body. `connect` and `disconnect` are the
whole of it; nothing else in the graph moves.

Two rules follow, and they hold for the whole map rather than for space alone:

* **an edge is removed, not flagged.** "The edge is there but you may not walk
  it" would be a second state to account for in routing, in exploration, in
  chat and in the search itself. An undocked ship is unreachable for exactly
  the reason any disconnected piece of the map is: there is no path;
* **an edge nobody walks on** -- otherwise a transit hangs between a node that
  is no longer adjacent and a body with nowhere to arrive. Undocking waits for
  the gangway to clear.

An autopath tail is a different matter: a route laid before the edge went away
is cut off at the node the body reached, the same way it is cut off by a
customs refusal or by lack of strength. A route is a plan, not a promise.

## A city touches the outside world only through its exits (D-206)

The built-up area is a group of nodes joined by short edges, and until now
nothing said **where** that group meets everything beyond it. So an edge out of
the steppe could be welded to any node of it: a trail from the trading yard
straight into the wild made a second gate out of a market, and the route out of
the city stopped passing the gate at all.

A city therefore has exactly two doors, and both are nodes:

* **the gate** -- the node property `выход`. The one place one leaves the walls
  on foot, and hence the one place one arrives at from outside;
* **the spaceport** -- the node the `Космическая верфь` machine stands in. Ship groups
  couple to it by one edge (D-201), and a ship is the only thing that arrives
  anywhere but the gate.

The rule lives in `connect`, because an edge is created nowhere else: an edge
between a city node and a node outside that city is allowed only at an exit.
Inside one city nothing is checked -- a street is not a border; outside every
city nothing is checked either -- wild land has no walls to have doors in.

The spaceport is a machine rather than a second property on purpose: what a
place is, is set by what stands in it (D-176). A city builds itself a port, and
loses it with the machine -- without a property to keep in step.

## The border is settled at departure (D-123)

Duty, ban and duty-free norm live in `engine.customs`; here stands the single
point where a body changes city. Settled **before** leaving: not enough for the
duty -- the transit does not start at all, and no debt arises.

## While walking -- you are absent

In-person actions are closed, every one: mining, craft, loading, buying,
copying a recipe. Remote (orders, account, correspondence) works --
information travels over the Net, matter requires presence (D-044, D-047).

That is the price of the road: while you are on the way, the lot gets bought
and the price beaten down. Knowing the price is not getting the goods.
"""

from __future__ import annotations

import heapq
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current_catalog
from src.constants import current as constants_now
from src.constants import registry as R
from src.engine import (
    access,
    bank,
    chat,
    craft,
    customs,
    estate,
    events,
    explore,
    food,
    justice,
    net,
    ship,
    transport,
    world,
)
from src.engine import city as town
from src.engine import ship as vessels
from src.engine.errors import Refusal
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.job import Job, JobKind, JobState
from src.models.travel import Travel, TravelState
from src.models.world import Edge, Node, Surface
from src.units import SECONDS_PER_HOUR


class TravelError(Refusal):
    pass


class NoEdge(TravelError):
    """No edge. Nobody walks in a straight line in this world."""


class NoRoute(NoEdge):
    """No path at all: the nodes are not connected by edges even through other nodes."""


class InTransit(TravelError):
    """The body is in transit. Matter requires presence, and there is no presence now."""


class EdgeInUse(TravelError):
    """People are walking the edge right now: it is not removed from under them (D-201).

    The gangway is not pulled from under a walker. Undocking waits, and that is
    the only precondition the removal of an edge has.
    """


class NotAnExit(TravelError):
    """An edge across a city's boundary at a node that is not a door (D-206).

    A city meets everything beyond it at the gate and at the spaceport, and
    nowhere else. A road laid into the middle of the built-up area would be a
    second gate made out of whatever node it happened to touch.
    """


class AlreadyGoing(TravelError):
    pass


class Imprisoned(TravelError):
    """Imprisonment: the body is held to the node until the term (D-095, D-166)."""


class NoStrength(TravelError):
    """Not enough strength for the road. Eat or sleep first (D-147)."""


@dataclass(frozen=True, slots=True)
class Exit:
    """Where one can go from here and how much time it costs."""

    edge_id: uuid.UUID
    node_id: uuid.UUID
    key: str
    name: str
    surface: Surface
    seconds: float
    #: Surface condition, 0..100 (D-158): a road without maintenance overgrows,
    #: and the player must see that in advance -- the convoy will stop where it overgrew.
    condition: float


def surface_multiplier(constants: Constants, surface: Surface) -> float:
    """Time multiplier by surface. The road is the reference (D-107)."""
    if surface is Surface.TRAIL:
        return constants[R.ROAD_TRAIL_MULTIPLIER]
    if surface is Surface.PAVED:
        return constants[R.ROAD_PAVED_MULTIPLIER]
    return constants[R.ROAD_ROAD_MULTIPLIER]


def edge_seconds(constants: Constants, edge: Edge) -> float:
    return edge.base_seconds * surface_multiplier(constants, edge.surface)


#: The node property "distance" (D-180): how many transits it is from civic
#: land. Built-up area has none at all, and that is the same as zero.
REACH = "даль"


def reach_of(node: Node) -> int:
    """The node's distance. Civic land and everything created before D-180 -- zero."""
    return int((node.properties or {}).get(REACH, 0) or 0)


#: The node property marking the city's gate (D-097, D-206): the one node of
#: the built-up area a road from beyond the walls may be tied to.
EXIT = "выход"


async def is_exit(session: AsyncSession, node: Node) -> bool:
    """Whether the node is one of the city's two doors (D-206).

    The gate is a property of the node, the spaceport is a machine standing in
    it: what a place is, is set by what stands in it (D-176), so a city gets a
    port by building one and loses it with the machine.
    """
    if (node.properties or {}).get(EXIT):
        return True

    return await world.has_station(session, node, ship.SPACEPORT)


async def gate_of(session: AsyncSession, node: Node) -> Node | None:
    """The gate of the city this node stands in. Outside a city -- nothing.

    This is where a road from beyond the walls is tied: exploration lays its
    trail from here rather than from the node the scout set out from (D-206).
    """

    city = await town.of_node(session, node)
    if city is None:
        return None
    return await town.gate(session, city)


def frontier_seconds(constants: Constants, reach: int) -> float:
    """Transit length to a node of this distance (D-180).

    The first ring beyond the walls costs `travel.frontier_step`, each next one
    `travel.frontier_growth` times more than the previous. The settled
    surroundings are thereby closer than the unexplored, and that is the only
    reason a near resource is hauled daily and a far one by expedition.
    """
    step = constants[R.TRAVEL_FRONTIER_STEP]
    growth = constants[R.TRAVEL_FRONTIER_GROWTH]
    return step * growth ** max(0, reach - 1)


async def exits(session: AsyncSession, constants: Constants, node: Node) -> tuple[Exit, ...]:
    """Where the edges from the node lead. An edge is undirected, so we look at both ends."""
    rows = (
        (
            await session.execute(
                select(Edge).where(or_(Edge.node_a_id == node.id, Edge.node_b_id == node.id))
            )
        )
        .scalars()
        .all()
    )
    #: All the far ends at once. One by one this was a query per road, and the
    #: node scene asks for exits on every look -- the commonest command there is.
    far = {edge.node_b_id if edge.node_a_id == node.id else edge.node_a_id for edge in rows}
    beyond = (
        {
            other.id: other
            for other in (await session.execute(select(Node).where(Node.id.in_(far)))).scalars()
        }
        if far
        else {}
    )

    found: list[Exit] = []
    for edge in rows:
        other_id = edge.node_b_id if edge.node_a_id == node.id else edge.node_a_id
        other = beyond.get(other_id)
        if other is None:  # pragma: no cover -- an edge to nowhere is a bug
            continue
        found.append(
            Exit(
                edge_id=edge.id,
                node_id=other.id,
                key=other.key,
                name=other.name,
                surface=edge.surface,
                seconds=edge_seconds(constants, edge),
                condition=float(edge.condition),
            )
        )
    return tuple(sorted(found, key=lambda exit: exit.seconds))


async def has_transport(session: AsyncSession, body: Body) -> bool:
    """Whether the body drives a convoy. The vehicle is **harnessed**, not in the pocket (D-157).

    Previously a wagon was looked for in the hands, and that was nonsense: a
    wagon is heavier than the carry limit and is not taken in hand at all. It
    can be pulled only when harnessed, and the harness is the only sign by
    which the road tells a carter from a walker.
    """

    return await transport.harnessed(session, body) is not None


def stamina_cost(constants: Constants, seconds: float, *, transport: bool) -> float:
    """What a road of this length costs the body.

    The spend is computed by time, not by number of transits (D-147): otherwise
    a step across the quarter would cost as much as a crossing of the steppe.
    """
    spend = constants[R.TRAVEL_STAMINA_PER_HOUR] * seconds / SECONDS_PER_HOUR
    if transport:
        spend *= constants[R.TRANSPORT_STAMINA_K]
    return spend


async def current(session: AsyncSession, body: Body) -> Travel | None:
    """This body's ongoing transit, if any."""
    stmt = select(Travel).where(Travel.body_id == body.id, Travel.state == TravelState.GOING)
    return (await session.execute(stmt)).scalars().first()


class Asleep(TravelError):
    """The body sleeps. The same unavailability as the road, only voluntary."""


class InField(TravelError):
    """The body is exploring: it left on its own and returns on schedule or by cancel."""


async def require_here(session: AsyncSession, body: Body) -> None:
    """The presence check -- one for all in-person actions.

    The road must really cost time: otherwise leaving a node becomes free, and
    the geography all this was made for disappears. Sleep stands at the same
    door: a sleeper is unavailable for everything in-person (D-091) -- that is
    how hibernation pays for recovery. Exploration stands at it too (D-152):
    the scout leaves in person, and while in the field is not in the node.
    """
    if body.sleeping_since is not None:
        raise Asleep("тело спит: сначала проснуться")
    going = await current(session, body)
    if going is not None:
        raise InTransit(
            f"тело в пути и придёт в {going.arrives_at.isoformat()}: материя требует присутствия"
        )

    run = await explore.pending(session, body)
    if run is not None:
        raise InField(
            f"тело в разведке и вернётся в {run.run_at.isoformat()}: "
            "отменить заход — «вернуться» на карте"
        )


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
        raise NoRoute(
            "пути нет вовсе: узлы не связаны рёбрами"
            if vehicle is None
            else "обозу туда дороги нет: бездорожье транспорт не пускает"
        )
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
        raise TravelError("мёртвое тело никуда не идёт")
    if await current(session, body) is not None:
        raise AlreadyGoing("тело уже в пути")
    #: Setting out is an in-person start, and its door is **the same** as for
    #: all in-person actions: a sleeper does not go (D-091), a scout does not go
    #: (D-152) -- they are not in the node, they are in the field. Keeping this
    #: list as a separate copy would mean forgetting a line in it sooner or
    #: later: the scout did exactly that, walking away while staying "in the field".
    await require_here(session, body)
    if target.id == body.node_id:
        raise NoEdge("это тот же узел")

    #: Imprisonment is a forced restriction of movement to the node (D-095,
    #: D-166). The engine enforces it, not guards: the verdict does not depend
    #: on whether anyone is online.

    sits = await justice.imprisoned(session, body.identity_id)
    if sits is not None:
        raise Imprisoned(
            "заключение: выходить из узла запрещено до "
            + (sits.until.isoformat() if sits.until else "решения суда")
        )

    #: Insolvency holds in the node the same way, but it is imposed not by the
    #: authority but by the banking system: world physics, not a verdict (D-063, D-168).

    holds = await bank.restrained(session, constants, body.identity_id, now=moment)
    if holds is not None:
        raise Imprisoned(
            "долг не обслуживается: выходить из узла нельзя, пока не "
            "рассчитаетесь. Заплатить за вас вправе кто угодно"
        )

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
            raise TravelError("маршрут ведёт в исчезнувший узел")
        target = next_node
        plan = legs[1:] + plan

    #: Left the workshop -- left the conversation: the circle does not follow (D-043).

    await chat.leave_groups(session, body.identity_id)

    seconds = edge_seconds(constants, edge)
    if convoy is not None:
        #: Surface decides not only time but the very possibility to drive through.
        if not transport.passable(constants, edge.surface, convoy.type_key):
            raise transport.Impassable(
                f"«{convoy.type_key}» здесь не пройдёт: "
                f"{edge.surface.value} транспорт не пускает. "
                "Распрягитесь либо ищите дорогу"
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

    #: The road costs stamina, and it is paid up front (D-147). Satiety slows
    #: the spend exactly as at work: lunch is lunch, and the cold makes every
    #: step dearer the same way (D-231).

    spend = (
        stamina_cost(constants, seconds, transport=await has_transport(session, body))
        * food.drain_multiplier(constants, body, moment)
        * await frost.drain_multiplier(session, constants, body)
    )
    if spend > float(body.stamina):
        raise NoStrength(
            f"на дорогу нужно {spend:.1f} выносливости, а есть "
            f"{float(body.stamina):.1f}: сначала поесть или поспать"
        )
    body.stamina = Decimal(str(float(body.stamina) - spend))

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


class NotGoing(TravelError):
    """The body is not on the road: there is nothing to turn back from."""


async def turn_back(session: AsyncSession, body: Body, *, now: datetime | None = None) -> Travel:
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
    """
    moment = now or datetime.now(UTC)
    going = await current(session, body)
    if going is None:
        raise NotGoing("тело не в пути: возвращаться неоткуда")

    here = await session.get(Node, going.from_node_id)
    if here is not None and not await access.may_enter(session, here, body.identity_id):
        raise access.Barred(
            f"«{here.name}» — чужая закрытая локация, и вы идёте через неё "
            "проходом: с полпути тут не поворачивают, проход идётся до конца"
        )

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
        raise TravelError(f"задание {job.id}: перехода нет")
    if travel.state is not TravelState.GOING:
        #: A job retry after a failure does not become a second arrival.
        return

    body = await session.get(Body, travel.body_id, with_for_update=True)
    target = await session.get(Node, travel.to_node_id)
    if body is None or target is None:  # pragma: no cover
        raise TravelError(f"переход {travel.id} ссылается в никуда")

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
            raise TravelError(f"переход {travel.id}: план ведёт в исчезнувший узел")
        rest = [uuid.UUID(raw) for raw in travel.plan[1:]]

        try:
            await depart(session, constants_now(), body, next_node, now=job.run_at, _plan=rest)
        except (
            NoStrength,
            customs.CustomsError,
            transport.Impassable,
            NoEdge,
        ) as stop:
            #: The route breaks off here -- not enough strength (D-147), the
            #: border did not let the cargo through (D-123), the road does not
            #: let the convoy through (D-107), or the edge itself is gone: the
            #: ship undocked while the route was being walked (D-201). The body
            #: stays in the node rather than dropping mid-leg: got as far as
            #: allowed, the player decides the rest.
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


async def connect(
    session: AsyncSession,
    a: Node,
    b: Node,
    *,
    base_seconds: float,
    surface: Surface = Surface.ROAD,
) -> Edge:
    """Connect two nodes with an edge. Undirected -- the road is the same both ways.

    The docking half of the pair (D-201): a ship couples to a spaceport by one
    edge between its connector and the port node, and nothing else in the graph
    changes. Idempotent -- an existing edge is returned rather than doubled, so
    a repeated docking does not give a second way in.

    An edge is created nowhere else in the engine, so this is also the one place
    the city's boundary can be held: across it only the gate and the spaceport
    are connected (D-206).
    """
    existing = await _edge_between(session, a.id, b.id)
    if existing is not None:
        return existing
    await require_exit(session, a, b)
    #: Asked before the edge exists: whether either end is a place nothing led
    #: to yet. See below -- that decides whether measured distances survive.

    dead_end = await _unconnected(session, a) or await _unconnected(session, b)
    afloat = vessels.is_aboard(a) or vessels.is_aboard(b)
    edge = Edge(
        node_a_id=a.id,
        node_b_id=b.id,
        base_seconds=int(base_seconds),
        surface=surface,
    )
    session.add(edge)
    await session.flush()
    #: Land is priced by the distance to the city's printer (D-220), and this
    #: is the one place an edge appears -- so it is the one place a measured
    #: distance can go stale. It does not always go stale, and the difference
    #: is worth keeping:
    #:
    #: * **a way to a new place changes nothing.** A scout's trail hangs a
    #:   node nothing led to yet, and a road through it would have to come back
    #:   along the same edge -- so it lies on nobody's shortest way. This is the
    #:   common case: the map grows at its edges;
    #: * **a gangway changes nothing either.** A ship hangs on the map by that
    #:   one edge (D-201), so a road through it comes back the way it went; and
    #:   no ship node belongs to a city, so no land tax is measured for one at
    #:   all. Docking used to drop the whole world's measurements for nothing;
    #: * **a way between two places already on the map may be a short cut**, and
    #:   then a whole quarter is nearer the centre than it was measured to be.
    #:   Which quarter is not asked: the measurements are dropped and taken
    #:   again by whoever needs one. Nobody lays such an edge in play -- roads
    #:   only re-surface edges that exist -- so in practice this is the seed
    #:   catching an already-living world up to a changed map, once per deploy.

    if dead_end and not afloat:
        #: The map grew at its edge: the new place stands one step further from
        #: the printer than what it was hung on, and that is the whole of
        #: measuring in play (D-220). Nothing else moved, so nothing else is
        #: touched.
        await estate.note_new_place(session, a, b)
    elif not afloat:
        await estate.forget_distances(session)
    #: The Net's map of the roads changed either way, gangway or not (D-222).

    net.forget_graph()
    return edge


async def _unconnected(session: AsyncSession, node: Node) -> bool:
    """Whether no edge leads to this node at all."""
    found = await session.scalar(
        select(Edge.id).where(or_(Edge.node_a_id == node.id, Edge.node_b_id == node.id)).limit(1)
    )
    return found is None


async def require_exit(session: AsyncSession, a: Node, b: Node) -> None:
    """An edge across a city's boundary is allowed only at its doors (D-206).

    Two ends, and each is checked on its own: a road between two cities leaves
    one gate and arrives at another. What is not checked is an edge inside a
    single city -- a street is not a border -- and an edge between nodes outside
    every city: wild land has no walls, so it has no doors either.
    """

    here = await town.of_node(session, a)
    there = await town.of_node(session, b)
    if here is not None and there is not None and here.id == there.id:
        return
    for node, city in ((a, here), (b, there)):
        if city is None or await is_exit(session, node):
            continue
        raise NotAnExit(
            f"«{node.name}» — не выход из города: за стену ведут только ворота "
            "и космодром, дорогу тянут от них"
        )


async def disconnect(session: AsyncSession, a: Node, b: Node) -> bool:
    """Remove the edge between two nodes -- undocking (D-201).

    The edge is **removed**, not flagged as closed: a second state would have
    to be accounted for in routing, in exploration and in the node scene, and a
    ship in flight is unreachable for exactly the reason any disconnected piece
    of the map is -- there is no path to it.

    The single precondition: **nobody is walking this edge**. A transit under
    way would hang between a node that is no longer adjacent and a body with
    nowhere to arrive, so undocking waits for the gangway to clear. Routes laid
    through the edge are another matter: they break off at the node the body
    reached, like any other route that ran into the impassable.

    Returns whether there was anything to remove: undocking an undocked ship is
    not an error, it is a no-op.
    """
    edge = await _edge_between(session, a.id, b.id)
    if edge is None:
        return False

    walking = (
        (
            await session.execute(
                select(Travel).where(Travel.edge_id == edge.id, Travel.state == TravelState.GOING)
            )
        )
        .scalars()
        .first()
    )
    if walking is not None:
        raise EdgeInUse(
            "по переходу сейчас идут: трап из-под идущего не убирают. "
            "Дождитесь, пока дорога освободится"
        )

    await session.delete(edge)
    await session.flush()
    #: A way that is gone may make the road to the centre longer than it was
    #: measured to be, and land is priced by that distance (D-220) -- the same
    #: reason as in `connect`, and the same exception: a ship hung on the map
    #: by this one gangway (D-201), and nothing on land counted its way through
    #: a hull. Today that is every removal there is; the check is here for the
    #: day something on land is taken apart.

    if not (vessels.is_aboard(a) or vessels.is_aboard(b)):
        await estate.forget_distances(session)

    net.forget_graph()
    return True


async def _edge_between(session: AsyncSession, one: uuid.UUID, other: uuid.UUID) -> Edge | None:
    stmt = select(Edge).where(
        or_(
            (Edge.node_a_id == one) & (Edge.node_b_id == other),
            (Edge.node_a_id == other) & (Edge.node_b_id == one),
        )
    )
    return (await session.execute(stmt)).scalars().first()


async def neighbours(session: AsyncSession, node: Node) -> Sequence[Node]:  # pragma: no cover
    """The node's neighbours -- for the map and the future autopath (D-045)."""
    edges = (
        (
            await session.execute(
                select(Edge).where(or_(Edge.node_a_id == node.id, Edge.node_b_id == node.id))
            )
        )
        .scalars()
        .all()
    )
    out: list[Node] = []
    for edge in edges:
        other_id = edge.node_b_id if edge.node_a_id == node.id else edge.node_a_id
        other = await session.get(Node, other_id)
        if other is not None:
            out.append(other)
    return out
