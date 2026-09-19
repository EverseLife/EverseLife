# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""craft: the queue: one body, one work, at the machine (D-209).

Split out of `engine/craft.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import String as SqlString
from sqlalchemy import case, cast, exists, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import current, current_catalog
from src.engine import events, goods, travel
from src.engine import world as world_engine
from src.engine.craft._base import Busy, CraftError, CutOff, NoStation
from src.engine.craft._internal import (
    _free_at,
    _machines,
    _num,
    _release,
)
from src.engine.craft.wearing import _hours_run, _wear_tools
from src.engine.jobs import enqueue, handler
from src.engine.world import body_container, node_container
from src.models.craft import BatchState, CraftBatch
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.job import Job, JobKind, JobState
from src.models.travel import Travel, TravelState
from src.models.world import Node


async def present(session: AsyncSession, body: Body, node_id: uuid.UUID) -> bool:
    """Whether the master stands at the machine: alive, in this node, awake, not
    on the road and not in the field.

    Sleep counts as absence (D-211): one body does one thing, and a sleeper is
    not working. Lying down is stepping away from the bench -- the batch
    freezes with its time left and the machine is freed, exactly as when the
    master leaves the node; waking resumes it. Until D-211 sleep was the one
    exception here, and it made the night a free accelerator of craft.
    """
    if body.state is not BodyState.ALIVE or body.node_id != node_id:
        return False
    if body.sleeping_since is not None:
        return False
    return await travel.current(session, body) is None


async def running(session: AsyncSession, body: Body) -> CraftBatch | None:
    """The one batch of this body under way, if any."""
    stmt = select(CraftBatch).where(
        CraftBatch.body_id == body.id, CraftBatch.state == BatchState.RUNNING
    )
    return (await session.execute(stmt)).scalars().first()


async def waiting(session: AsyncSession, body: Body) -> list[CraftBatch]:
    """This body's works that are not moving, in the order they were started."""
    stmt = (
        select(CraftBatch)
        .where(CraftBatch.body_id == body.id, CraftBatch.state == BatchState.WAITING)
        .order_by(CraftBatch.started_at.asc(), CraftBatch.id.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def _hold_station(session: AsyncSession, station: Item | None) -> None:
    """The machine's row, taken before a batch is written at it (D-351).

    The door that takes a machine down asks, under this very row, whether a
    batch here still needs it (`station._awaited`). A batch queued behind the
    master's running one holds no machine of its own (D-209), so without this
    row the two passed each other: the take-down saw no batch yet, the start
    saw the machine still standing, and both committed -- the batch waiting
    for ever on materials already written off (OQ-181). Taken here, one of
    them waits for the other, and whichever comes second sees what the first
    did. After the stacks and the pool, where the batch's run takes it too
    (`_take_station`).
    """
    if station is None:
        return
    await world_engine.lock_thing(session, station, gone=CraftError)
    #: Taken down while this waited: nothing to queue at. The master asks
    #: again and gets whatever machine of the name still stands.
    if not station.installed:
        raise NoStation(key="craft-no-station", station=station.type_key)


async def _take_station(session: AsyncSession, body: Body, name: str, until: datetime) -> Item:
    """The best free machine of this name in the node, taken for this master's
    run until `until` -- one machine, one worker (D-150).

    Chosen and taken in one statement, of the row as it stands. The choice
    alone (`_pick_station`) is a read: two masters taking up their waiting
    work at once -- a work ended beside them wakes the node, another comes
    back to the bench -- both read the one bench free and both wrote
    themselves onto it, the second over the first, and two batches ran at a
    machine recorded as one master's. A long transaction did the same on its
    own: the tick's orphan sweep wakes the node orphan after orphan (D-217)
    and went on trusting a bench it had read free after another master took
    it. Here the database answers "free" for the row it locks, and the object
    is read again with it.

    A machine another transaction holds right now is passed over, not waited
    for. Waiting closed circles: two batches ending at once, each finish
    holding its own machine and waking the node, and each wake reading the
    other's machine free -- its stamp past, as it always is at a finish -- and
    queueing on it; a master's command holding their body and queueing on a
    machine the fire held while it wanted that body; a machine held here while
    the fire, which takes machines by id, held the next one. The price is a
    wake that crosses the holder and misses the machine. Where the holder
    frees it -- a finish or the sweep while this master's own command holds
    their row (`wake_node` passes the held body by as this passes the held
    machine), a master walking away (`freeze`) -- the node gets a second
    chance after that commit, and that one waits for the rows (`_again`).
    Where the holder frees nothing -- a start whose batch queues behind its
    master's running one, a take-down refused -- the batch waits at a free
    machine for the next wake in the node or the master's hand.
    `NO KEY`, because a key-share lock -- another row's foreign key checked
    against this one -- is no hold on the machine and must not hide it.
    """
    moment = datetime.now(UTC)
    machine = (
        await session.execute(
            (await _machines(session, body, name))
            .where(_free_at(moment))
            .limit(1)
            .with_for_update(key_share=True, skip_locked=True)
            #: Read again, not only locked: the hold below is written through
            #: the object, and a memory of this very master on the machine --
            #: read before they walked away and it came free -- would make the
            #: `busy_body_id` below no change at all. The flush would leave the
            #: column out, and the machine would stand held by nobody.
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if machine is None:
        raise Busy(key="craft-station-busy", station=name, whose="other")
    machine.busy_body_id = body.id
    machine.busy_until = until
    await session.flush()
    return machine


async def _launch(
    session: AsyncSession,
    batch: CraftBatch,
    body: Body,
    *,
    station: Item | None,
    now: datetime,
    event: dict,
) -> CraftBatch:
    """Add a batch and put it to work -- or into the queue.

    Materials are already written off by the caller: a queued batch is paid for
    up front like a running one, otherwise the queue would be a way to reserve
    a machine with nothing. The one thing decided here is **whether it moves
    now**: one body works one batch, the rest wait their turn (D-209).

    `station` is the machine the caller chose, and it is **required** rather
    than optional: its row is taken here for every batch (`_hold_station`,
    D-351), and a door that could leave it out is a door that one day will --
    the coin press did, until a review found it.
    """
    await _hold_station(session, station)
    #: Born waiting; `_run` is the only door into "running", so that a batch
    #: cannot count as under way without a job scheduled for it.
    batch.state = BatchState.WAITING
    session.add(batch)
    await session.flush()
    await events.record(
        session,
        EventKind.CRAFT_STARTED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        batch_id=str(batch.id),
        **event,
    )
    if await running(session, body) is None:
        await _run(session, batch, body, now)
    return batch


async def _run(session: AsyncSession, batch: CraftBatch, body: Body, now: datetime) -> bool:
    """Set a waiting batch going from where it stopped.

    Takes the best free machine of the batch's name in the node -- not
    necessarily the one it ran at before: while the master was away somebody
    else may have stood there. No free machine, or the node cut off for debt --
    the batch stays waiting and says why through the client. Returns whether it
    started.
    """
    #: A frozen node stops a machine exactly as an unpaid bill does (D-231), and
    #: the resumption must treat it the same way: the batch waits. Without it
    #: the refusal would come out of `rest.wake` and out of the arrival job --
    #: a body on Aurora could neither wake up nor finish its road.
    from src.engine import frost  # noqa: PLC0415 -- lazy: breaks the import cycle with frost

    left = float(batch.remaining_seconds or 0)
    ready_at = now + timedelta(seconds=left)
    station: Item | None = None
    if batch.station is not None:
        try:
            station = await _take_station(session, body, batch.station, ready_at)
        except (NoStation, Busy, CutOff, frost.Frozen):
            return False

    batch.state = BatchState.RUNNING
    batch.runs += 1
    batch.run_started_at = now
    batch.ready_at = ready_at
    batch.remaining_seconds = None
    batch.station_item_id = None if station is None else station.id
    await session.flush()

    #: A batch is an ordinary journal job: it survives a process restart and
    #: runs exactly once (01-tech-notes, pattern 1). Each run has its own job:
    #: the one left over from a frozen run must not finish the resumed one.
    if batch.runs > 1:
        await events.record(
            session,
            EventKind.CRAFT_RESUMED,
            actor_identity_id=body.identity_id,
            node_id=batch.node_id,
            batch_id=str(batch.id),
            output=batch.output,
            left_seconds=left,
        )
    #: The first run keeps the plain key it always had; a resumed run gets
    #: its number, so that the two jobs are two rows and not one.
    key = f"craft.batch:{batch.id}" if batch.runs == 1 else f"craft.batch:{batch.id}:{batch.runs}"
    await enqueue(
        session,
        JobKind.CRAFT_BATCH,
        batch.ready_at,
        payload={"batch": str(batch.id), "run": batch.runs},
        dedup_key=key,
        body_id=body.id,
    )
    return True


async def freeze(
    session: AsyncSession, body: Body, *, now: datetime | None = None
) -> CraftBatch | None:
    """The master leaves: the running batch stops with the time left in it.

    The machine is freed -- half-done work does not hold a public bench hostage
    for whoever walked away and never came back; the batch takes a free one of
    the same name on return (D-209). Called wherever a body leaves its node:
    departure, going into the field, prison, death.

    Whoever waits here for a machine gets this one after the commit, not inside
    it (`_again`): freed "for others" (D-209, D-211), the queue behind it moves
    on (D-217). A freeze runs inside every door a body leaves by -- a death
    in the fire or of the frost tick, a sentence, a departure, lying down --
    and a wake here would take other masters' rows and a machine inside each
    of them, the fire among them, which takes machines before bodies. The
    second chance takes the same rows in a transaction of its own that holds
    nothing else.
    """
    moment = now or datetime.now(UTC)
    #: The body's row, before the batch is read. Most callers hold it already --
    #: every command does, through `_alive` -- but three do not: a sentence
    #: taking a convict off the bench (`justice`), and a hull lost or run out of
    #: air, which kill a crew read without a lock (`ship.fate`, `oxygen.breath`).
    #: Through those three this ran against the batch's own finishing job, and
    #: since D-309 that costs more than a confused row: both would close the same
    #: run and bill the tools for the same hours twice. Taken here rather than
    #: asked of every caller -- a re-lock costs nothing where the row is held.
    #: A bare id, not a `refresh`: refreshing would throw away whatever the
    #: caller has changed on the body and not yet flushed.
    await session.execute(select(Body.id).where(Body.id == body.id).with_for_update())
    batch = await running(session, body)
    if batch is None:
        return None
    left = max(0.0, (batch.ready_at - moment).total_seconds()) if batch.ready_at else 0.0
    #: The tools are paid off before the run is closed: they wear by the hours
    #: swung (D-309), and the hours of this run end here. Charged now rather
    #: than added up at the finish because a batch may be frozen and resumed
    #: any number of times, and hours nobody worked must not be billed.
    await _wear_tools(session, current(), batch, hours=_hours_run(batch, moment))
    batch.state = BatchState.WAITING
    batch.remaining_seconds = _num(left)
    batch.ready_at = None
    batch.run_started_at = None
    freed = batch.station_item_id
    await _release(session, freed)
    batch.station_item_id = None
    await session.flush()
    await events.record(
        session,
        EventKind.CRAFT_PAUSED,
        actor_identity_id=body.identity_id,
        node_id=batch.node_id,
        batch_id=str(batch.id),
        output=batch.output,
        left_seconds=left,
    )
    #: Whenever somebody else has a work waiting here -- awake, asleep,
    #: queued behind their own running work, on their way in: any of them
    #: may be taking it up right now, a waking or an arrival or the end of
    #: that work finding this machine still held, and the second chance is
    #: the wake that will not miss them. Not where nobody does: a job for
    #: nobody is a row in the journal for ever. Nobody who turns waiting in
    #: the same instant is missed by the read: a start queues only behind its
    #: master's own running work, whose end wakes them (`_launch`), and a
    #: frozen batch's master is away.
    if freed is not None and any(
        one != body.id for one in await _waiting_here(session, batch.node_id)
    ):
        await _again(session, batch.node_id)
    return batch


async def wake(
    session: AsyncSession,
    body: Body,
    *,
    now: datetime | None = None,
    besides: frozenset[str] = frozenset(),
) -> CraftBatch | None:
    """The master is free and on the spot: the first of their waiting works that
    can go here goes.

    In queue order, but not strictly: a work frozen in another node, or one
    whose machine is taken, does not hold up the ones behind it -- the player
    would otherwise be standing at a free bench unable to work because of a
    batch three towns away. Called on the end of a work, on waking up, on
    arrival and on the return from a run, by hand from the client, and for
    whoever waits in a node where a machine came free (`wake_node`, `woken`).

    Taking a work up is starting it again, and it asks what a start asks
    (D-211): a master at another occupation -- a search, a face, a plot, a
    house going up -- is given no bench, however free. A waiting batch is no
    occupation (`occupation._crafting`), so a master may well have walked
    from one into another; the wakes that come unasked -- another's work
    ending, a walk-away, the second chance -- set it going at their back, and
    a scout's, whom the engine keeps in the node they set out from until the
    run ends (D-327), at a bench kilometres away (D-209). `besides` is for the
    one caller inside an occupation of its own: the return from a run, whose
    survey is the job being run.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE or await running(session, body) is not None:
        return None
    from src.engine import occupation  # noqa: PLC0415 -- lazy: breaks the cycle occupation -> craft

    asked = False
    for batch in await waiting(session, body):
        if not await present(session, body, batch.node_id):
            continue
        #: Once, and only when a work could go here: most wakes find none.
        if not asked:
            busy = await occupation.current(
                session, body, besides=frozenset({occupation.CRAFT}) | besides
            )
            if busy is not None:
                return None
            asked = True
        if await _run(session, batch, body, moment):
            return batch
    return None


async def sweep_orphans(session: AsyncSession) -> int:
    """Cancel batches whose finishing job is gone, and give back what went in (D-217).

    A batch is the one work whose end lives entirely in a journal job. While
    the job is there everything holds: close the tab and the batch still
    arrives. When the job **disappears** -- retries exhausted on a defect, a
    hand in the database, a job that never got queued -- nothing happens at
    all. The batch stays "running" for ever, and that is not cosmetic: the body
    counts as busy (D-211) and can start nothing else, while the materials are
    already written off. It was found on the live world, where one master had
    been unable to take up anything for nine days.

    **State is what is checked, not time.** A job still waiting its hour means
    a healthy batch, however long the wait; only an absent, failed or cancelled
    job means nobody is coming. And a `waiting` batch is never an orphan: it has
    no job by design -- it is queued or frozen while the master is away (D-209).
    """
    alive = (
        select(Job.dedup_key)
        .where(
            Job.dedup_key == _batch_key(CraftBatch.id, CraftBatch.runs),
            Job.state.in_((JobState.PENDING, JobState.RUNNING)),
        )
        .exists()
    )
    orphans = (
        (
            await session.execute(
                select(CraftBatch).where(CraftBatch.state == BatchState.RUNNING, ~alive)
            )
        )
        .scalars()
        .all()
    )
    for batch in orphans:
        await _abandon(session, batch)
    return len(orphans)


def _batch_key(batch_id, runs):
    """The job key of a batch's current run, as SQL.

    The first run keeps the plain key it always had; a resumed one carries its
    number, so that the two runs are two job rows and not one (D-209).
    """
    plain = literal("craft.batch:") + cast(batch_id, SqlString)
    return case((runs == 1, plain), else_=plain + literal(":") + cast(runs, SqlString))


async def _abandon(session: AsyncSession, batch: CraftBatch) -> None:
    """Give the batch back to the master and close it as cancelled."""

    catalog = current_catalog()
    body = await session.get(Body, batch.body_id)
    node = await session.get(Node, batch.node_id)
    if body is None or node is None:  # pragma: no cover -- a batch into nowhere
        batch.state = BatchState.CANCELLED
        await session.flush()
        return

    #: Where the product would have gone (D-209): into the hands of a master
    #: standing at the machine, otherwise beside it. Matter does not travel
    #: after whoever walked away.
    at_bench = body.state is BodyState.ALIVE and body.node_id == batch.node_id
    where = await body_container(session, body) if at_bench else await node_container(session, node)

    returned: dict[str, float] = {}
    for name, value in (batch.spent or {}).items():
        #: A return is whole pieces, rounded down, like every return (D-212).
        back = goods.whole(name, float(value), catalog=catalog)
        if back <= 0:
            continue
        await world_engine.grant_item(
            session,
            where,
            name,
            amount=back,
            quality=float(batch.quality),
            origin=f"партия «{batch.output}» отменена: задания не стало",
        )
        returned[name] = back

    await _release(session, batch.station_item_id)
    batch.station_item_id = None
    batch.state = BatchState.CANCELLED
    batch.finished_at = datetime.now(UTC)
    await session.flush()
    await events.record(
        session,
        EventKind.CRAFT_ABANDONED,
        actor_identity_id=body.identity_id,
        node_id=batch.node_id,
        batch_id=str(batch.id),
        output=batch.output,
        returned=returned,
    )
    #: The machine came free -- whoever queued behind it moves up (D-217).
    await wake_node(session, node)


async def wake_node(session: AsyncSession, node: Node, *, now: datetime | None = None) -> None:
    """A machine came free in the node: whoever stands here waiting for one gets it.

    Called inside the transaction that freed it -- a finish, the orphan sweep,
    a machine put up -- which holds that machine and more besides. A waiting
    master whose row is held right now -- by their own command (`_alive`) or
    by anything else -- is passed over rather than waited for: that command
    may be reaching for the very machine held here, and waiting would close
    the circle. Passed over is not given up: the node gets a second chance
    after this commit, and that one waits for the row (`_again`, `woken`).
    Before it, a master who so much as moved a thing in their pocket in the
    second the bench came free went on waiting at a free bench, until the
    next one came free or they took the work up by hand.
    """
    standing = await _waiting_here(session, node.id)
    reached = await world_engine.lock_bodies(session, standing, skip_locked=True)
    if len(reached) < len(standing):
        await _again(session, node.id)
    for body in reached:
        await wake(session, body, now=now)


async def _waiting_here(session: AsyncSession, node_id: uuid.UUID) -> list[uuid.UUID]:
    """The live masters with a work waiting in the node who stand there or are
    on their way in -- read, not locked.

    On their way in, because a body on the road keeps the node it left until
    its arrival moves it (`travel.on_the_road`), and the arrival takes the
    work up under the body's row (`travel.walk.arrive`): a master whose
    arrival holds that row right now is exactly one a wake here must not
    lose sight of. One still on the road is woken for nothing -- not there
    yet (`present`) -- which is cheaper than asking the road here.
    """
    waiting = select(CraftBatch.body_id).where(
        CraftBatch.node_id == node_id, CraftBatch.state == BatchState.WAITING
    )
    coming = (
        exists()
        .where(
            Travel.body_id == Body.id,
            Travel.to_node_id == node_id,
            Travel.state == TravelState.GOING,
        )
        .correlate(Body)
    )
    stmt = select(Body.id).where(
        Body.id.in_(waiting),
        Body.state == BodyState.ALIVE,
        or_(Body.node_id == node_id, coming),
    )
    return list((await session.execute(stmt)).scalars())


async def _again(session: AsyncSession, node_id: uuid.UUID) -> None:
    """Wake the node's waiting masters once more, after this transaction
    commits (`woken`, D-209, D-217).

    Queued by a transaction that freed a machine and could not reach a master
    waiting for one (`wake_node`), or did not try (`freeze`). A hold that
    frees nothing -- a start queuing at the machine (`_hold_station`), a
    take-down refused -- queues none, and a take that passes by a machine
    another transaction holds (`_take_station`) leaves its batch to the next
    wake in the node or to its master's hand.

    Queued, not called: the job is a row of this transaction, so no worker
    sees it before the commit, and what this transaction freed is free by
    then. One per node and transaction -- the sweep abandons orphan after
    orphan, and the frost tick may empty a whole workshop -- keyed by the
    transaction for that rather than by the node alone. The key is unique
    across the whole journal, done jobs included, and one another transaction
    has written and not yet committed would make this insert wait for that
    transaction's end, holding whatever this one holds.
    """
    here = await session.scalar(select(cast(func.pg_current_xact_id(), SqlString)))
    await enqueue(
        session,
        JobKind.CRAFT_WAKE,
        datetime.now(UTC),
        payload={"node": str(node_id)},
        dedup_key=f"craft.wake:{node_id}:{here}",
    )


@handler(JobKind.CRAFT_WAKE)
async def woken(session: AsyncSession, job: Job) -> None:
    """The second chance (`_again`): the node's waiting masters, woken in a
    transaction of their own.

    This one **waits** for their rows. It holds no machine, so the circle
    `wake_node` steps around -- a command holding its master's row and
    reaching for the machine the waker holds -- cannot close here. The rows
    are taken all at once in id order (`world.lock_bodies`), the order every
    taker of many bodies keeps; a door that holds one body and then takes
    another out of that order can still cross it when both wait in this
    node -- a sentence taking the convict off the bench under the judge's own
    row (`justice`, `freeze`), a crew dying body by body (`ship.fate`,
    `oxygen.breath`) -- and the database breaks that knot: this job retries,
    the other side fails. Past the rows it takes what the masters' own resume
    takes, in the same order (`_run`).

    The work starts at the hour the job was queued, not at the hour the
    machine came free, as the wake inside a finish has it: the master was
    passed over because a command of theirs was running, and a waking or an
    arrival there may be later than the finish -- a run dated before it would
    be worked asleep or on the road. Queued inside the freeing transaction,
    the hour falls after whatever held the master's row began.
    """
    standing = await _waiting_here(session, uuid.UUID(job.payload["node"]))
    for body in await world_engine.lock_bodies(session, standing):
        await wake(session, body, now=job.run_at)
