# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The planet's clock: the warn/erupt job chain, and the eruption itself.

The rhythm queues its own next beat, the way the tick does, so it can neither
be lost nor doubled. `erupted` is the orchestrator: it rings the fire, the
ways and the veins in the one lock order the whole package agrees on -- the
order is written down once, on the handler.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current
from src.constants import registry as R
from src.engine import events
from src.engine.jobs import enqueue, handler
from src.engine.plates._base import _exempt, _surface
from src.engine.plates.fire import _burn
from src.engine.plates.veins import _move_veins
from src.engine.plates.ways import _redraw
from src.models.event import Event, EventKind
from src.models.job import Job, JobKind, JobState
from src.models.mining import MiningSession, SessionState
from src.models.world import Node, Planet, Vein
from src.units import HOURS_PER_DAY


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

    #: **The lock order of the whole package, and it is written down once here.**
    #:
    #:     veins  ->  the sessions at a face  ->  the things lying in a node
    #:     ->  bodies  ->  (inside a closer) the face's things  ->  the node's
    #:     heaps  ->  the pocket
    #:
    #: A miner can die in another transaction while the planet shakes -- of
    #: the heat, of their own roof -- and the session row is the **gate** the
    #: two closers of a face agree on: `death.die` opens with `mining.abandon`
    #: (the session rows are its first lock), and this job takes the same rows
    #: right here, before the fire touches the first heap. Whoever wins the
    #: gate plays the whole story out; the loser waits at it holding nothing
    #: the winner could want. Locked any later -- as they were, in
    #: `_close_faces` after `_burn` had the yards -- a death holding its gate
    #: and laying salvage into a burning yard crossed this job holding the
    #: yards and closing his face: ABBA, and the database killed one of the
    #: two. The veins go before the sessions for the same reason: a swing
    #: holds its vein and then writes its session row, and taking them here
    #: the other way round would cross it.
    #:
    #: What this order still does not cover, all of it a job crossing a tick
    #: once in a planet's rhythm, and all of it replayed by the worker's retry
    #: (`jobs._mark_failure`) if the database kills one side: a frost or
    #: oxygen death of a **walker** in a shaken node takes his body first
    #: while this job takes the yards first, and `_kill_on` wants bodies late
    #: (B <-> N); a session **started** between this pre-lock and
    #: `_close_faces` is seen there but its gate is taken with the yards
    #: already burnt; and a tick that killed two bodies in one pass holds the
    #: first death's heaps in a shaken yard while the second death waits at a
    #: gate this job pre-locked (N <-> S).
    ids = [node.id for node in shaken]
    await session.execute(
        select(Vein).where(Vein.node_id.in_(ids)).order_by(Vein.id).with_for_update()
    )
    await session.execute(
        select(MiningSession)
        .join(Vein, Vein.id == MiningSession.vein_id)
        .where(Vein.node_id.in_(ids), MiningSession.state == SessionState.ACTIVE)
        .order_by(MiningSession.id)
        .with_for_update(of=MiningSession)
    )

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
