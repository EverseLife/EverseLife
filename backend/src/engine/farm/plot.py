# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The plot itself: marked out of open ground, plowed, split and merged --
the land as a thing one shapes before anything grows on it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import estate, events
from src.engine.farm._base import (
    FarmError,
    NoLand,
    NotYours,
    TooSmall,
    WrongState,
    _accrue_fallow,
    _consume,
    _ground_fertility,
    _here,
    _open_ground,
    _owned,
    _recuttable,
    plow_banked,
    plow_minutes,
    plow_paused,
)
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.farm import Plot, PlotState
from src.models.identity import Body
from src.models.job import Job, JobKind, JobState
from src.models.world import Node
from src.units import ROUND_MINUTES, ROUND_QUALITY, SCALE_MAX, amount, amount_float, on_grid


async def mark(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    *,
    name: str,
    area: float,
    now: datetime | None = None,
) -> Plot:
    """Survey a plot. In person: land is measured on foot."""
    moment = now or datetime.now(UTC)
    await _here(session, body)
    if area < constants[R.FARM_PLOT_MIN_AREA]:
        raise TooSmall(key="farm-too-small", min=constants[R.FARM_PLOT_MIN_AREA])

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise FarmError(key="farm-body-off-node")
    await _open_ground(session, node)
    #: A floor of a house is not ground (D-247). Left to the room check below it
    #: would refuse with "nothing free here" -- true of a third floor, and no
    #: explanation of why it will never be otherwise.
    if estate.storey_of(node) is not None:
        raise NotYours(key="farm-storey-not-ground")
    #: The plot's holder runs the estate: buy the land first (06-farming).
    #: Hiring is access plus a share by contract (D-116), not shared land.
    #:
    #: Land outside a city belongs to nobody and never will (D-198), and there
    #: the field is open: whoever ploughs it, farms it. The plot record still
    #: has an owner -- the crop is somebody's -- but the ground under it is not.
    nobody = node.owner_identity_id is None and node.owner_city_id is None
    if not nobody and node.owner_identity_id != body.identity_id:
        raise NotYours(key="farm-node-not-yours")

    #: The land is spent by three things and the check must know all three
    #: (D-246): the footprint of what stands here, the strips already marked,
    #: and the ground promised to a site under way. Asking about the strips
    #: alone let a hundred metres of beds be cut out from under a house, and
    #: the foraging then walked land that was not there.
    #:
    #: Under the plot's lock, and it is the same lock the building takes
    #: (`estate.hold_ground`): two commands now spend one remainder, and without
    #: it "mark out sixty" and "build sixty" both pass on a plot of a hundred.
    await estate.hold_ground(session, node)
    free = await estate.free_ground(session, node)
    if area > free:
        raise NoLand(key="farm-no-land", node=node.key, free=max(free, 0), area=area)

    plot = Plot(
        node_id=node.id,
        owner_identity_id=body.identity_id,
        name=name.strip() or "без имени",
        area_m2=Decimal(str(area)),
        fertility=Decimal(str(_ground_fertility(node))),
        idle_since=moment,
    )
    session.add(plot)
    await session.flush()

    await events.record(
        session,
        EventKind.PLOT_MARKED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        plot_id=str(plot.id),
        area=area,
    )
    return plot


async def plow(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    plot: Plot,
    *,
    now: datetime | None = None,
) -> Plot:
    """Plough. Long-running: started in person, goes by itself.

    Taking up a paused plough is the same act (D-277): the strip keeps what
    was ploughed, and the run is queued for the remainder alone.
    """
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(plot, body)
    resumed = plow_paused(plot)
    if plot.state is PlotState.IDLE:
        _accrue_fallow(constants, plot, moment)
        plot.plow_done_minutes = Decimal(0)
    elif not resumed:
        raise WrongState(key="farm-not-fallow", plot=plot.name, state=plot.state.value)

    plot.state = PlotState.PLOWING
    plot.idle_since = None
    plot.plow_since = moment
    await session.flush()

    left = max(0.0, plow_minutes(constants, plot) - float(plot.plow_done_minutes))
    ready = moment + timedelta(minutes=left)
    event = await events.record(
        session,
        EventKind.PLOT_PLOWED,
        actor_identity_id=body.identity_id,
        node_id=plot.node_id,
        plot_id=str(plot.id),
        resumed=resumed,
    )
    await enqueue(
        session,
        JobKind.FARM_PLOW,
        ready,
        payload={"plot": str(plot.id)},
        dedup_key=f"farm.plow:{plot.id}:{moment.timestamp()}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    return plot


async def _running_plough(session: AsyncSession, **where: object) -> Job | None:
    """The pending plough job, locked -- by the body at it or by the strip.

    Lock order: the job first, the plot second -- the worker's own order
    (`jobs.run_one` claims the job, `plow_done` then locks the plot). Taken
    the other way round, a pause holding the plot while it waits for the job
    and a worker holding the job while it waits for the plot would deadlock.
    The wait on the lock may end with the worker's commit: the row is then
    DONE, the condition fails on reread, and there is nothing to pause.
    """
    stmt = (
        select(Job)
        .where(Job.kind == JobKind.FARM_PLOW.value, Job.state == JobState.PENDING)
        #: One row, and the worker's own choice of it -- `jobs.run_one` orders
        #: the same way. Without the limit `.first()` trims in Python while the
        #: lock is taken in the database: every matching row would be held, and
        #: which one came back would be nobody's decision.
        .order_by(Job.run_at)
        .limit(1)
        .with_for_update()
    )
    if "body" in where:
        stmt = stmt.where(Job.body_id == where["body"])
    if "plot" in where:
        stmt = stmt.where(Job.payload["plot"].astext == str(where["plot"]))
    return (await session.execute(stmt)).scalars().first()


async def _locked_plot(session: AsyncSession, plot_id: uuid.UUID) -> Plot:
    plot = await session.get(Plot, plot_id, with_for_update=True, populate_existing=True)
    if plot is None:  # pragma: no cover
        raise FarmError(key="farm-job-no-plot", job=str(plot_id))
    return plot


async def plow_pause(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    *,
    plot: Plot | None = None,
    now: datetime | None = None,
) -> Plot:
    """Take the hands off the plough: the work stops, what is done stays (D-277).

    The minutes ploughed are banked on the strip, never more than the whole
    -- a pause after the job's hour and before the worker's visit is a
    finished plough waiting, not a surplus. `plow` takes the bank up again
    for the remainder. The body is free at once, wherever it has wandered to
    meanwhile: the plough is a work of these hands, not a place
    (`occupation._ploughing`), so pausing asks for no presence.

    Named, it is the strip's plough that is paused, whoever's hands began it
    -- a body printed anew after a death still owns its strips and their
    work. Unnamed, it is this body's one plough: the "activities" column
    knows the occupation, not the plot.
    """
    moment = now or datetime.now(UTC)
    if plot is not None:
        _owned(plot, body)
        job = await _running_plough(session, plot=plot.id)
        if job is None:
            raise WrongState(key="farm-plot-not-plowing", plot=plot.name)
    else:
        job = await _running_plough(session, body=body.id)
        if job is None:
            raise WrongState(key="farm-not-plowing")
    strip = await _locked_plot(session, uuid.UUID(job.payload["plot"]))

    job.state = JobState.CANCELLED
    job.finished_at = moment
    whole = Decimal(str(plow_minutes(constants, strip)))
    done = min(whole, plow_banked(strip, moment))
    #: Down, never to the nearest. The bank already holds whole hundredths of
    #: a minute, so rounding to the nearest handed back a whole hundredth for
    #: every pause: a pause-and-resume loop cycling between 0.3 and 0.6 s
    #: ploughed at up to twice real time -- quicker than that banked nothing,
    #: slower banked honestly. Downwards the rounding drops at most a
    #: hundredth, and drops it against the plougher.
    strip.plow_done_minutes = done.quantize(Decimal(1).scaleb(-ROUND_MINUTES), rounding=ROUND_DOWN)
    strip.plow_since = None
    await session.flush()

    await events.record(
        session,
        EventKind.PLOT_PLOW_PAUSED,
        actor_identity_id=body.identity_id,
        node_id=strip.node_id,
        plot_id=str(strip.id),
        done_minutes=float(strip.plow_done_minutes),
    )
    return strip


async def plow_reset(
    session: AsyncSession,
    body: Body,
    plot: Plot,
    *,
    now: datetime | None = None,
) -> Plot:
    """Drop the plough's progress: the strip is fallow again, nothing kept (D-277).

    A decision of its own, never a side effect: pausing keeps the work, and
    only this throws it away -- and only from a pause, so that dropping is
    always two presses apart from working. A running plough is refused and
    told to pause first; so there is no job to take here, and the strip's own
    lock is the whole of the ordering.
    """
    moment = now or datetime.now(UTC)
    _owned(plot, body)
    strip = await _locked_plot(session, plot.id)
    #: Judged under the lock: a resume may have committed while we waited.
    if strip.state is not PlotState.PLOWING:
        raise WrongState(key="farm-plot-not-plowing", plot=strip.name)
    if strip.plow_since is not None:
        raise WrongState(key="farm-plow-running", plot=strip.name)

    strip.state = PlotState.IDLE
    strip.plow_done_minutes = Decimal(0)
    strip.idle_since = moment
    await session.flush()

    await events.record(
        session,
        EventKind.PLOT_PLOW_RESET,
        actor_identity_id=body.identity_id,
        node_id=strip.node_id,
        plot_id=str(strip.id),
    )
    return strip


@handler(JobKind.FARM_PLOW)
async def plow_done(session: AsyncSession, job: Job) -> None:
    #: Under the same lock the commands take (`api.commands.farm._plot`):
    #: the state write below must not race a split or a merge of the strip.
    plot = await session.get(
        Plot, uuid.UUID(job.payload["plot"]), with_for_update=True, populate_existing=True
    )
    if plot is None:  # pragma: no cover
        raise FarmError(key="farm-job-no-plot", job=str(job.id))
    if plot.state is not PlotState.PLOWING or plot.plow_since is None:
        #: A job retry after a failure does not double the ploughing, and a
        #: job of a run that was paused meanwhile finishes nothing.
        return
    plot.state = PlotState.PLOWED
    plot.plow_done_minutes = Decimal(0)
    plot.plow_since = None
    await session.flush()


#: The two fertilizers of the vault (D-264), by their D-251 ids. The dose is
#: one for both -- the difference is strength, and it is these constants.
FERTILIZERS = {
    "compost": R.FARM_COMPOST_RECOVERY,
    "mineral_fertilizer": R.FARM_MINERAL_RECOVERY,
}


async def fertilize(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    plot: Plot,
    goods: str,
    *,
    now: datetime | None = None,
) -> Plot:
    """Work fertilizer into the land (D-264, closes OQ-108).

    Into the **land**, not into what grows: a fallow or plowed strip. Feeding
    a growing bed is the "feeding" of the five care decisions and waits for
    OQ-098. The dose is one norm for either kind (`farm.fertilizer_per_m2`);
    the kinds differ in what they give back -- compost returns
    `farm.compost_recovery`, the mineral one `farm.mineral_recovery`, most of
    all, as the vault's table promises. No limit per cycle: the price is the
    limit -- compost costs waste and water, saltpeter comes from the Salt
    Wastes and is wanted by the rocket too.
    """
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(plot, body)
    if plot.state not in (PlotState.IDLE, PlotState.PLOWED):
        raise WrongState(key="farm-fertilize-sown", plot=plot.name, state=plot.state.value)
    spec = FERTILIZERS.get(goods)
    if spec is None:
        raise FarmError(key="farm-not-a-fertilizer", goods=goods)

    #: Fallow is credited first: the fertilizer tops up the healed land, and
    #: the ceiling refusal below judges the honest, current number.
    _accrue_fallow(constants, plot, moment)
    fertility = float(plot.fertility)
    if fertility >= SCALE_MAX:
        raise WrongState(key="farm-land-sated", plot=plot.name)

    need = amount(constants[R.FARM_FERTILIZER_PER_M2] * float(plot.area_m2))
    await _consume(
        session,
        body,
        goods,
        need,
        why=FarmError(key="farm-no-fertilizer", goods=goods, need=amount_float(need)),
    )
    plot.fertility = on_grid(min(SCALE_MAX, fertility + constants[spec]), ROUND_QUALITY)
    await session.flush()

    await events.record(
        session,
        EventKind.PLOT_FERTILIZED,
        actor_identity_id=body.identity_id,
        node_id=plot.node_id,
        plot_id=str(plot.id),
        goods=goods,
        spent=amount_float(need),
        fertility=float(plot.fertility),
    )
    return plot


async def split(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    plot: Plot,
    cut_area: float,
    *,
    name: str,
    now: datetime | None = None,
) -> Plot:
    """Split a plot. Both parts inherit fertility and history as is."""
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(plot, body)
    _recuttable(plot)

    rest = float(plot.area_m2) - cut_area
    if cut_area < constants[R.FARM_PLOT_MIN_AREA] or rest < constants[R.FARM_PLOT_MIN_AREA]:
        raise TooSmall(key="farm-halves-too-small")

    _accrue_fallow(constants, plot, moment)
    plot.area_m2 = Decimal(str(rest))
    #: Resurveyed land is ploughed anew.
    plot.state = PlotState.IDLE
    plot.idle_since = moment

    piece = Plot(
        node_id=plot.node_id,
        owner_identity_id=plot.owner_identity_id,
        name=name.strip() or "отрез",
        area_m2=Decimal(str(cut_area)),
        fertility=plot.fertility,
        last_culture=plot.last_culture,
        same_culture_cycles=plot.same_culture_cycles,
        idle_since=moment,
    )
    session.add(piece)
    await session.flush()
    return piece


async def merge(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    one: Plot,
    other: Plot,
    *,
    now: datetime | None = None,
) -> Plot:
    """Merge two plots: fertility weighted, history -- the heaviest.

    Anti-exploit (D-118): otherwise redrawing borders would reset depletion.
    """
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(one, body)
    _owned(other, body)
    _recuttable(one)
    _recuttable(other)
    if one.node_id != other.node_id:
        raise FarmError(key="farm-merge-other-node")

    _accrue_fallow(constants, one, moment)
    _accrue_fallow(constants, other, moment)

    a, b = float(one.area_m2), float(other.area_m2)
    one.area_m2 = Decimal(str(a + b))
    #: A weighted mean lands off the grid as a rule -- ten square metres of
    #: thirty with seven of forty is 35.294... -- and the journal below would
    #: otherwise report a number the row never took.
    blended = (float(one.fertility) * a + float(other.fertility) * b) / (a + b)
    one.fertility = on_grid(blended, ROUND_QUALITY)
    heavier = max((one, other), key=lambda p: p.same_culture_cycles)
    one.last_culture = heavier.last_culture
    one.same_culture_cycles = heavier.same_culture_cycles
    #: Resurveyed land is ploughed anew.
    one.state = PlotState.IDLE
    one.idle_since = moment

    await session.delete(other)
    await session.flush()
    return one
