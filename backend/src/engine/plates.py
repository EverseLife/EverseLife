# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Eruptions: the planet redraws its own map (D-197, D-233).

Pyroxis was promised a moving map from the first day, and this is that promise
in code. An eruption is the same move the world already knows -- exploring
grows the graph, a road left unmaintained falls back to offroad -- only the
planet makes it instead of a person.

## What it does, and what it deliberately does not

* **Edges are redrawn, nodes are not.** Lava fills a pass, a cooled flow lies
  across a rift as a bridge. A node is where people stand and things lie
  (D-192), and it stays; a node is born of scouting and of nothing else (D-098).
* **Veins move.** In the shaken nodes a share of them goes out
  (`pyroxis.vein_relocate_share`) and as many light up next door. This is the
  measure against a staked claim: the vein leaves the monopolist by itself,
  with nobody's ill will and nobody's complaint to a court.
* **The Anvil Plateau is never shaken.** The one place on Pyroxis anything
  stands on is the one place the planet leaves alone (D-197).
* **What lies under the open sky burns.** Goods left in a field die with the
  ground they lie on -- the only loss of property here, and it is announced
  `pyroxis.eruption_warning` before it happens.
* **Nothing built is destroyed.** The world is eternal and there are no wipes
  (D-007): a base taken by lava is a wipe for one person, and the grudge would
  outlive any good the dynamics did.

## The two rules D-233 added

* **A node with people or property in it is never sealed.** There is always
  somewhere to walk. But **an edge may break under someone walking it**, and
  such a passage ends in death with the pocket lost for ever -- a sanctioned
  sink of matter, and the risk one takes by walking far from the ship.
* **Docking is untouchable.** The connector-to-node edge and the node a ship
  stands in are outside the draw: tearing a ship loose, or pulling the rock out
  from under it, would kill a crew by an event rather than by a mistake.

## What will not be here

The **forecast** D-197 once planned -- a seismologist's trade, sold days ahead
of the free signal -- is **cancelled** (D-235). The paid layer needed a
profession, an instrument and a market of information, and bought only "do not
lose a week of work"; the free signal buys a life, and that is the half the
world owes anybody. An eruption stays a thing one prepares for in general, not
a thing one buys the date of.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current
from src.constants import registry as R
from src.engine import death, events, net, ship, travel, world
from src.engine.jobs import enqueue, handler
from src.models.event import Event, EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Container, ContainerKind, Item
from src.models.job import Job, JobKind, JobState
from src.models.mining import MiningSession, SessionState
from src.models.ship import Ship
from src.models.travel import Travel, TravelState
from src.models.world import Edge, Layer, Node, Planet, Surface, Vein
from src.units import HOURS_PER_DAY, amount_float

#: The node property that keeps a place out of every draw (D-197): the Anvil
#: Plateau, and whatever else a planet's seed marks the same way.
ANVIL = "anvil"


async def schedule(session: AsyncSession, constants: Constants, *, after: datetime) -> None:
    """Put the planet's next eruption in the journal.

    A rhythm, not an event of the server (D-197): the world queues its own next
    one, the way the tick does, so it can neither be lost nor doubled. The roll
    is seeded by the **day** rather than by the second, so two processes
    starting a minute apart compute the same moment and the dedup key makes one
    job of the two.
    """
    period = constants[R.PYROXIS_ERUPTION_PERIOD]
    #: Counted from the **start of the day**, not from the moment somebody
    #: happened to call: two processes of one deploy start seconds apart, and
    #: an offset added to each of their clocks would put two independent chains
    #: of eruptions in the journal -- each queueing its own next one, and the
    #: planet shaking twice as often after every release.
    day = datetime.combine(after.date(), time.min, tzinfo=UTC)
    dice = random.Random(f"plates:{after.date().isoformat()}")
    days = dice.uniform(period.min, period.max)
    when = day + timedelta(hours=days * HOURS_PER_DAY)
    queued = await enqueue(
        session,
        JobKind.PLATES_WARN,
        when,
        dedup_key=f"plates.warn:{int(when.timestamp())}",
    )
    if queued is not None:
        return
    #: Refused by the key, and the two reasons for that are opposite. Usually
    #: it is the other process of the same deploy, a second ahead of us, and
    #: its job is the one we wanted -- there is nothing to do. But the key is
    #: unique across every state, so a **finished** warning of that same second
    #: refuses us too, and then swallowing the refusal would stop the planet's
    #: weather until somebody restarted the world. So: a pending warning means
    #: the chain runs; no pending warning means the second is taken by a
    #: corpse, and a minute later is a second that is not.
    running = await session.scalar(
        select(Job.id)
        .where(Job.kind == JobKind.PLATES_WARN.value, Job.state == JobState.PENDING)
        .limit(1)
    )
    if running is not None:
        return
    later = when + timedelta(minutes=1)
    await enqueue(
        session,
        JobKind.PLATES_WARN,
        later,
        dedup_key=f"plates.warn:{int(later.timestamp())}",
    )


@handler(JobKind.PLATES_WARN)
async def warned(session: AsyncSession, job: Job) -> None:
    """The signal: these nodes will be shaken, in `pyroxis.eruption_warning` hours.

    Free and to everybody in them -- that is P6, the window to walk out of. The
    nodes are chosen **now** and travel in the job: an eruption that announced
    one place and shook another would be worse than no warning at all.
    """
    constants = current()
    moment = job.run_at
    dice = random.Random(str(job.id))
    shaken = await _choose(session, constants, dice)
    when = moment + timedelta(hours=constants[R.PYROXIS_ERUPTION_WARNING])
    for node in shaken:
        #: One event per node, and with `node_id`: that is how a thing said in
        #: a place reaches everybody standing in it (`api.push`). A summary
        #: with no place in it would reach nobody at all -- and the window to
        #: walk out is the whole licence for what follows (P6).
        await events.record(
            session,
            EventKind.PLATES_WARNED,
            node_id=node.id,
            at=when.isoformat(),
        )
    if shaken:
        await enqueue(
            session,
            JobKind.PLATES_ERUPT,
            when,
            payload={"nodes": [str(node.id) for node in shaken]},
            dedup_key=f"plates.erupt:{int(when.timestamp())}",
        )
    await schedule(session, constants, after=moment)


@handler(JobKind.PLATES_ERUPT)
async def erupted(session: AsyncSession, job: Job) -> None:
    """The eruption itself: the ground moves and the map with it."""
    constants = current()
    dice = random.Random(str(job.id))
    moment = job.run_at
    shaken = []
    for one in job.payload.get("nodes") or []:
        node = await session.get(Node, uuid.UUID(one))
        if node is not None:
            shaken.append(node)
    #: The exemptions are asked **again**, six hours after they were first
    #: asked: a ship that came down in an announced node inside the window --
    #: a rescue run, the likeliest use of the window there is -- must not find
    #: the ground moving under it (D-233).
    spared = await _exempt(session)
    shaken = [node for node in shaken if node.id not in spared]
    if not shaken:
        return

    #: **The lock order of the whole file, and it is written down once here.**
    #:
    #:     the things lying in a node  ->  bodies  ->  the sessions at a face
    #:
    #: A miner can die in another transaction while the planet shakes -- of the
    #: heat, of their own roof -- and `death.die` takes the same rows in the
    #: same order: it lays the salvaged pocket into the node first (`stack_up`
    #: locks what is already lying there), and only then closes the face
    #: (`mining.abandon`). Two transactions that took them in opposite orders
    #: would deadlock on the first miner who died in a shaking node (ABBA), so
    #: the fire goes first even though it reads like the end of the story.
    #:
    #: The redraw before the veins move is a separate decision: a vein moves
    #: along the ways as they are **after** the eruption -- it may cross a
    #: bridge laid this same second and may not cross an edge that has just
    #: gone.
    burnt = await _burn(session, shaken)
    torn, laid, dead = await _redraw(session, constants, dice, shaken, now=moment)
    moved = await _move_veins(session, constants, dice, shaken, now=moment)
    for node in shaken:
        #: Again one per node: whoever stands here learns that the ground under
        #: them moved, and rereads the place. **Without the planet's totals** --
        #: somebody standing in one field has no business reading how much
        #: burned in another; the tally of the whole eruption goes to the
        #: journal below, where the metrics read it.
        await events.record(session, EventKind.PLATES_ERUPTED, node_id=node.id)
    await events.record(
        session,
        EventKind.PLATES_ERUPTED,
        places=[node.key for node in shaken],
        burnt=burnt,
        veins_moved=moved,
        ways_torn=torn,
        ways_laid=laid,
        died=dead,
    )


async def _exempt(session: AsyncSession) -> set[uuid.UUID]:
    """The ground no eruption touches: the plateau and whatever a ship stands on.

    Asked twice -- when the nodes are chosen and again when they are shaken --
    because six hours pass between the two, and a ship may land inside them.
    """
    anvils = (
        (
            await session.execute(
                select(Node.id).where(
                    Node.planet == Planet.PYROXIS.value,
                    Node.properties[ANVIL].as_boolean(),
                )
            )
        )
        .scalars()
        .all()
    )
    moored = (
        (await session.execute(select(Ship.docked_node_id).where(Ship.docked_node_id.is_not(None))))
        .scalars()
        .all()
    )
    return {one for one in [*anvils, *moored] if one is not None}


async def _choose(session: AsyncSession, constants: Constants, dice: random.Random) -> list[Node]:
    """Which nodes the next eruption takes.

    Never the plateau (D-197), and never the ground a ship is standing on
    (D-233): pulling the rock out from under a docked hull would kill a crew by
    an event rather than by a mistake.
    """
    ground = await _surface(session)
    spared = await _exempt(session)
    open_ground = [node for node in ground if node.id not in spared]
    if not open_ground:
        return []
    how_many = constants[R.PYROXIS_NODES_SHIFTED]
    count = min(len(open_ground), dice.randint(int(how_many.min), int(how_many.max)))
    return dice.sample(open_ground, max(1, count))


async def _surface(session: AsyncSession) -> list[Node]:
    """Every node of the planet's ground. Not the sphere, not a ship's rooms."""
    found = (
        (
            await session.execute(
                select(Node).where(Node.planet == Planet.PYROXIS.value, Node.layer != Layer.SPACE)
            )
        )
        .scalars()
        .all()
    )
    return [node for node in found if not ship.is_aboard(node)]


async def _burn(session: AsyncSession, shaken: list[Node]) -> float:
    """What lies under the open sky burns with the ground (D-197).

    There is no warehouse in the fields, and that is the point: hauling is
    always part of the work here, and the logistics of Pyroxis are dear by the
    world's build rather than by anybody's tariff.

    Taken under a lock, and re-read after it: somebody carrying a sack out of
    the node in the last minute of the window is doing exactly what the window
    is for, and their sack must not be burned out of their hands.
    """
    yards = [(await world.node_container(session, node)).id for node in shaken]
    lying = (
        (
            await session.execute(
                select(Item)
                .where(Item.container_id.in_(yards))
                .order_by(Item.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    #: Only what is still here: somebody carrying a sack out in the last minute
    #: of the window is doing exactly what the window is for.
    here = [thing for thing in lying if thing.container_id in yards]
    return await _consume(session, here)


async def _consume(session: AsyncSession, things: list[Item]) -> float:
    """Take these things out of the world, and whatever is inside them with them.

    A chest is a thing with a container of its own, and deleting the chest
    alone would leave its goods alive in a place that no longer exists -- the
    same orphan `estate.upkeep` clears when a house falls. Used by the fire in
    the fields and by the rift under a walker alike: matter leaves the world by
    one door, or it leaves it half-way.

    **All the way down.** A chest goes inside a chest (`storage.admits` allows
    it), and one level of unpacking would delete the inner chest while its own
    container went on holding goods with no owner -- the same orphan, one floor
    lower. The loop runs until a layer brings back no new box.
    """
    gone = 0.0
    opened: set[uuid.UUID] = set()
    emptied: list[Container] = []
    inner: list[Item] = []
    layer = list(things)
    while layer:
        boxes = (
            (
                await session.execute(
                    select(Container).where(
                        Container.kind == ContainerKind.STORAGE,
                        Container.owner_id.in_([thing.id for thing in layer]),
                    )
                )
            )
            .scalars()
            .all()
        )
        layer = []
        for box in boxes:
            if box.id in opened:  # pragma: no cover -- a box cannot own itself
                continue
            opened.add(box.id)
            #: Under the lock and reread after it, exactly like the things lying
            #: on the ground above. A chest in a field is open to anybody
            #: (`station.may_build` gives the wild to everybody), so somebody may
            #: be taking a sack out of it in the last minute of the window --
            #: doing precisely what the window is for. Without the lock the
            #: delete would queue behind their update and take the sack **out of
            #: their hands** the moment it landed there.
            held = (
                (
                    await session.execute(
                        select(Item)
                        .where(Item.container_id == box.id)
                        .order_by(Item.id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .all()
            )
            for thing in held:
                if thing.container_id != box.id:  # pragma: no cover -- carried out in time
                    continue
                #: A chest among them is opened on the next lap. Nothing is
                #: deleted while the walk is on: a delete flushed between two
                #: laps would take its own container's owner out from under the
                #: query looking for it.
                layer.append(thing)
                inner.append(thing)
            emptied.append(box)
    for thing in [*things, *inner]:
        gone += amount_float(thing.amount)
        await session.delete(thing)
    #: In two flushes, and not for tidiness: with one, the delete of a box went
    #: to the database ahead of the delete of what lay in it, and the database
    #: refused it (`fk_item_container_id_container`). The order is not left to
    #: be inferred -- what is inside goes, then the box that held it.
    await session.flush()
    for box in emptied:
        await session.delete(box)
    await session.flush()
    return gone


async def _move_veins(
    session: AsyncSession,
    constants: Constants,
    dice: random.Random,
    shaken: list[Node],
    *,
    now: datetime,
) -> int:
    """A share of the shaken veins goes out, and as many light up next door.

    The measure against a staked claim (D-197): the vein leaves by itself. It
    lights up in a **neighbour** rather than anywhere, so the map keeps meaning
    something -- what moves is the claim, not the geography.

    Whoever was working it stops working it: matter is worked in person (D-044),
    and a face two passes away is not the face under this pick.
    """
    ids = [node.id for node in shaken]
    veins = (
        (
            await session.execute(
                select(Vein).where(Vein.node_id.in_(ids)).order_by(Vein.id).with_for_update()
            )
        )
        .scalars()
        .all()
    )
    share = constants[R.PYROXIS_VEIN_RELOCATE_SHARE]
    ways = await _adjacency(session)
    #: The exempt ground is not a destination either. The plateau is never
    #: shaken (`_choose`), so a vein that moved onto it would stay there for
    #: ever -- the one claim on the planet nothing can ever take away, which is
    #: the whole thing this machinery exists against (D-197). The ground under
    #: a docked ship is out for the same reason it is out of the draw: what
    #: stands there is not touched by the event.
    spared = await _exempt(session)
    places = {node.id: node for node in await _surface(session) if node.id not in spared}
    moved = 0
    for vein in veins:
        if dice.random() > share:
            continue
        neighbours = [places[one] for one in ways.get(vein.node_id, set()) if one in places]
        if not neighbours:
            continue
        await _close_faces(session, constants, vein, now=now)
        vein.node_id = dice.choice(neighbours).id
        moved += 1
    await session.flush()
    return moved


async def _close_faces(
    session: AsyncSession, constants: Constants, vein: Vein, *, now: datetime
) -> None:
    """End the sessions at a vein about to move out from under them.

    Through `mining.leave`, not by writing the state by hand: leaving a face is
    what carries the ore out of it into the pocket, wears the tool for the
    session and tells the journal. Set by hand, the session would close with the
    haul still lying in a container nobody will ever open again -- the ground
    moved, and that is not the miner's mistake (D-143).

    Called **before** the vein moves, so the ore is carried out of the face
    where it was actually mined.

    **Takes the node's things before the sessions**, which is the file's lock
    order (`erupted`) held locally rather than borrowed. In the eruption the
    rows are already this transaction's -- `_burn` took the same yards two
    steps earlier -- so the statement below costs nothing and changes nothing.
    It is here for the caller that has not taken them: `mining.abandon` warns
    that whoever takes a session before the node's heaps meets `death.die`
    coming the other way and one of the two is killed as a deadlock, and a
    private helper whose safety rests on what its callers did first is a trap
    laid for the next one. The invariant belongs to the function that needs it.
    """
    from src.engine import mining  # noqa: PLC0415 -- lazy: breaks the cycle with mining

    where = await session.get(Node, vein.node_id)
    if where is not None:
        yard = await world.node_container(session, where)
        await session.execute(
            select(Item)
            .where(Item.container_id == yard.id)
            .order_by(Item.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    working = (
        (
            await session.execute(
                select(MiningSession)
                .where(
                    MiningSession.vein_id == vein.id,
                    MiningSession.state == SessionState.ACTIVE,
                )
                .order_by(MiningSession.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    for face in working:
        #: Reread after the lock, like everything else taken under one. The
        #: other end of this race is `mining.abandon`, which closes the same
        #: session when the miner dies; whichever gets the row first finishes,
        #: and the second must see a closed session rather than a stale copy.
        if face.state is not SessionState.ACTIVE:  # pragma: no cover -- closed under the lock
            continue
        body = await session.get(Body, face.body_id)
        if body is None or body.state is not BodyState.ALIVE:
            #: A session open at a dead body: worlds that ran before
            #: `mining.abandon` existed have them, and `leave` would carry the
            #: ore into a pocket nobody will ever open. Closed the way a death
            #: closes it -- the haul stays lying in the node.
            if body is not None:
                await mining.abandon(session, body, now=now)
            continue
        await mining.leave(session, constants, face, now=now)


async def _adjacency(session: AsyncSession) -> dict[uuid.UUID, set[uuid.UUID]]:
    """The planet's whole graph in one reading: node -> its neighbours.

    One query, because everything below walks this graph -- what may break, what
    is still reachable, where a vein may move -- and asking the database per
    edge would grow with the planet.
    """
    ground = {node.id for node in await _surface(session)}
    edges = (
        (
            await session.execute(
                select(Edge).where(or_(Edge.node_a_id.in_(ground), Edge.node_b_id.in_(ground)))
            )
        )
        .scalars()
        .all()
    )
    ways: dict[uuid.UUID, set[uuid.UUID]] = {}
    for edge in edges:
        ways.setdefault(edge.node_a_id, set()).add(edge.node_b_id)
        ways.setdefault(edge.node_b_id, set()).add(edge.node_a_id)
    return ways


def _connected(ways: dict[uuid.UUID, set[uuid.UUID]], start: uuid.UUID) -> set[uuid.UUID]:
    """Everything reachable from here by the ways given."""
    seen = {start}
    queue = [start]
    while queue:
        where = queue.pop()
        for other in ways.get(where, set()):
            if other not in seen:
                seen.add(other)
                queue.append(other)
    return seen


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


async def shaking(session: AsyncSession, node: Node) -> datetime | None:
    """When the ground under this node is due to move, if it is due at all.

    The free signal is an event, and an event reaches whoever is connected in
    the second it is written (`api.push`). The window is six hours wide, and
    somebody logging in ten minutes into it must not walk into a field that is
    about to burn knowing nothing -- so the place itself carries the warning
    while it stands, and `look` shows it (D-197, P6).
    """
    if node.planet is not Planet.PYROXIS:
        return None
    said = await session.scalar(
        select(Event)
        .where(
            Event.kind == EventKind.PLATES_WARNED.value,
            Event.node_id == node.id,
            Event.at > datetime.now(UTC) - timedelta(hours=current()[R.PYROXIS_ERUPTION_WARNING]),
        )
        .order_by(Event.at.desc())
        .limit(1)
    )
    if said is None:
        return None
    when = said.payload.get("at")
    return None if when is None else datetime.fromisoformat(str(when))


async def ensure_scheduled(session: AsyncSession, *, now: datetime | None = None) -> None:
    """Make sure the planet's clock runs. Called at process start, like the tick.

    Two guards, and both are needed. A **pending** warning means the chain is
    running and nothing is queued -- so a deploy does not add a second chain to
    the first. Only a pending one counts: a warning that failed all its attempts
    must not stop the planet's weather for ever. And the moment itself is
    counted from the start of the day, so two processes of one deploy compute
    the same one and the dedup key makes a single job of the two.
    """
    running = await session.scalar(
        select(Job.id)
        .where(Job.kind == JobKind.PLATES_WARN.value, Job.state == JobState.PENDING)
        .limit(1)
    )
    if running is not None:
        return
    await schedule(session, current(), after=now or datetime.now(UTC))
