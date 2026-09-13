# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""craft: what a batch wears -- the machine per batch, the tools in the hands
by the hours swung (D-129, D-309).

Cut out of `_internal` (2026-09-13) when that module stood at the 800-line
bar: the wear of a run is asked by the end of a batch and by a freeze, and
by nothing that prepares one.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import wear
from src.engine.world import body_container
from src.models.craft import CraftBatch
from src.models.identity import Body
from src.models.inventory import Item
from src.units import SECONDS_PER_HOUR


def _hours_run(batch: CraftBatch, until: datetime) -> float:
    """How long the batch's current run has been going, in hours.

    A run is the stretch the master actually stood at the work: it opens in
    `_run` and closes at the end or at a freeze (D-209). What is charged for
    the tools is measured here and nowhere else, so the two closings cannot
    drift apart.
    """
    if batch.run_started_at is None:  # pragma: no cover -- a run always has its start
        return 0.0
    #: Never longer than the run itself. A job may fire late -- the worker was
    #: behind, the process restarted -- and a master who walks away after the
    #: hour was up would otherwise be billed for the waiting as if it were
    #: swinging. `queue.freeze` clamps the work left for the same reason.
    ends = min(until, batch.ready_at) if batch.ready_at is not None else until
    return max(0.0, (ends - batch.run_started_at).total_seconds()) / SECONDS_PER_HOUR


async def _wear_tools(
    session: AsyncSession, constants: Constants, batch: CraftBatch, *, hours: float
) -> None:
    """The tools wear by the hours actually swung (D-309).

    Not per batch, as the machine does: a batch of one log and a batch of fifty
    are five minutes and four hours of the same axe, and charging both the same
    would pay the worker for lumping orders together. Charged when a run of the
    batch closes -- at the end and at a freeze -- so that work never done is
    never billed for: a batch frozen with hours left in it has not spent them.

    A tool can leave the hands while the work runs -- handed over, sold across a
    counter, dropped in a chest -- and none of that freezes the batch. So the row
    is taken `FOR UPDATE` and the pocket is checked under that lock before a
    hundredth is written: without it this stream reached into a stranger's
    pocket, wore what it found there and, on the last of a tool's condition,
    deleted it. `wear.spend` has no lock of its own and says so; this is the one
    stream whose thing can walk away mid-work, so the lock is taken here.
    """
    if hours <= 0:
        return
    body = await session.get(Body, batch.body_id)
    pocket = None if body is None else await body_container(session, body)
    for held in batch.tool_item_ids or ():
        tool = await session.get(Item, uuid.UUID(held), with_for_update=True)
        if tool is None:
            #: Worn out by an earlier run of this same batch, or gone from the
            #: hands some other way. Nothing to charge.
            continue
        if pocket is None or tool.container_id != pocket.id:
            #: Gone from the hands while the work ran. The batch keeps the
            #: ceiling that tool set for it, but wear follows the thing, and the
            #: thing is somebody else's now: charging it would take the
            #: condition off whoever holds it -- and finish it off for them.
            continue
        await wear.spend(
            session,
            constants,
            tool,
            constants[R.WEAR_TOOL_PER_HOUR] * hours,
            cause="craft_batch",
        )


async def _wear_station(session: AsyncSession, constants: Constants, batch: CraftBatch) -> None:
    """The machine wears per batch: maintenance is mandatory (D-129)."""
    if batch.station_item_id is None:
        return
    station = await session.get(Item, batch.station_item_id)
    if station is None:  # pragma: no cover -- the machine may have been dismantled
        return
    await wear.spend(
        session,
        constants,
        station,
        constants[R.WEAR_STATION_PER_BATCH],
        cause="craft_batch",
    )
