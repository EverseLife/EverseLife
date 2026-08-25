# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""craft: the queue: one body, one work, at the machine (D-209).

Split out of `engine/craft.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import String as SqlString
from sqlalchemy import case, cast, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import current_catalog
from src.engine import events, goods, travel
from src.engine import world as world_engine
from src.engine.craft._base import Busy, CutOff, NoStation
from src.engine.craft._internal import _num, _occupy, _pick_station, _release
from src.engine.jobs import enqueue
from src.engine.world import body_container, node_container
from src.models.craft import BatchState, CraftBatch
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.job import Job, JobKind, JobState
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
    if await travel.current(session, body) is not None:
        return False
    from src.engine import explore  # noqa: PLC0415 -- lazy: breaks the import cycle with explore

    return await explore.pending(session, body) is None


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


async def _launch(
    session: AsyncSession,
    batch: CraftBatch,
    body: Body,
    *,
    now: datetime,
    event: dict,
) -> CraftBatch:
    """Add a batch and put it to work -- or into the queue.

    Materials are already written off by the caller: a queued batch is paid for
    up front like a running one, otherwise the queue would be a way to reserve
    a machine with nothing. The one thing decided here is **whether it moves
    now**: one body works one batch, the rest wait their turn (D-209).
    """
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

    station: Item | None = None
    if batch.station is not None:
        try:
            station = await _pick_station(session, body, batch.station)
        except (NoStation, Busy, CutOff, frost.Frozen):
            return False

    left = float(batch.remaining_seconds or 0)
    batch.state = BatchState.RUNNING
    batch.runs += 1
    batch.run_started_at = now
    batch.ready_at = now + timedelta(seconds=left)
    batch.remaining_seconds = None
    batch.station_item_id = None if station is None else station.id
    await session.flush()
    await _occupy(session, station, body, batch.ready_at)

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
    """
    moment = now or datetime.now(UTC)
    batch = await running(session, body)
    if batch is None:
        return None
    left = max(0.0, (batch.ready_at - moment).total_seconds()) if batch.ready_at else 0.0
    batch.state = BatchState.WAITING
    batch.remaining_seconds = _num(left)
    batch.ready_at = None
    batch.run_started_at = None
    await _release(session, batch.station_item_id)
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
    return batch


async def wake(
    session: AsyncSession, body: Body, *, now: datetime | None = None
) -> CraftBatch | None:
    """The master is free and on the spot: the first of their waiting works that
    can go here goes.

    In queue order, but not strictly: a work frozen in another node, or one
    whose machine is taken, does not hold up the ones behind it -- the player
    would otherwise be standing at a free bench unable to work because of a
    batch three towns away. Called on arrival, on the end of a work, and by
    hand from the client.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE or await running(session, body) is not None:
        return None
    for batch in await waiting(session, body):
        if not await present(session, body, batch.node_id):
            continue
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
    #: The machine came free -- whoever queued behind it moves up (D-209).
    await wake_node(session, node)


async def wake_node(session: AsyncSession, node: Node, *, now: datetime | None = None) -> None:
    """A machine came free in the node: whoever stands here waiting for one gets it."""
    #: `FOR UPDATE` does not go with `DISTINCT`: the waiting bodies are
    #: named by a subquery instead of a join.
    waiting = select(CraftBatch.body_id).where(
        CraftBatch.node_id == node.id, CraftBatch.state == BatchState.WAITING
    )
    stmt = (
        select(Body)
        .where(
            Body.id.in_(waiting),
            Body.node_id == node.id,
            Body.state == BodyState.ALIVE,
        )
        .order_by(Body.id)
        #: Their own commands hold the body row (`_alive`); one locked right
        #: now is skipped and woken by the next machine that comes free.
        .with_for_update(skip_locked=True)
    )
    for body in (await session.execute(stmt)).scalars().all():
        await wake(session, body, now=now)
