# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What the field automaton is due to do (D-339): its plots read off their
clocks without a write, the cursor walked past the setpoints and the actions
done, and the actions due listed by urgency. Reads and decides; the hands do.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import farm, world
from src.engine.agro._base import (
    FALLOW,
    FEED,
    HARVEST,
    MOISTURE,
    NO_FERTILIZER,
    NO_SEEDS,
    NO_STORE,
    NO_WATER,
    NOT_PLOWED,
    PLOW,
    SETPOINTS,
    SOW,
    STORE_FULL,
    THIN,
    UNFIT,
    WEED,
    in_force,
)
from src.engine.agro.hands import Outcome, Shift
from src.models.agro import FieldAutomat, FieldAutomatPlot
from src.models.farm import Plot, PlotState
from src.models.world import Node

#: The work each trouble word holds back: a word stays on the machine while
#: such work is still due and was not tried again (`run.advance`). A store
#: that is not there holds back whichever work names a store.
TROUBLE_WORK: dict[str, frozenset[str]] = {
    NO_WATER: frozenset({"water"}),
    NO_SEEDS: frozenset({"sow"}),
    UNFIT: frozenset({"sow"}),
    NOT_PLOWED: frozenset({"sow"}),
    NO_FERTILIZER: frozenset({"feed"}),
    STORE_FULL: frozenset({"harvest"}),
    NO_STORE: frozenset({"sow", "feed", "harvest"}),
}

#: Troubles that are a matter of how much: a bed that asks less may still be
#: served where a bigger one was not. The rest hold back the work whole.
BY_AMOUNT: frozenset[str] = frozenset({NO_WATER, NO_SEEDS, NO_FERTILIZER, STORE_FULL})


@dataclass(frozen=True)
class Due:
    """An action due, not yet done.

    `work` names it for the trouble words; `key` is the resource it draws on
    -- the seeds of one culture, one fertilizer, the yard's water -- and
    `area` the size of what it asks for, so a shortage for a big bed does not
    starve a small one of the same resource.
    """

    work: str
    key: tuple[str, ...]
    area: float
    act: Callable[[], Awaitable[Outcome]]


@dataclass
class Bed:
    """A plot given to the machine, as its clock stands at the moment: read free."""

    plot: Plot
    life: farm.Life | None
    stage: str | None

    @property
    def growing(self) -> bool:
        return self.plot.state is PlotState.SOWN and self.life is not None and not self.life.dead

    @property
    def area(self) -> float:
        return float(self.plot.area_m2)


async def beds_of(
    session: AsyncSession, constants: Constants, catalog: Catalog, row: FieldAutomat, now: datetime
) -> list[Bed]:
    """The machine's plots in its owner's order, each bed walked to `now` and written nowhere.

    Only the owner's own: a plot that changed hands with the land (D-339 p. 6)
    is no longer the machine's to work, whatever the list still says.
    """
    plots = (
        (
            await session.execute(
                select(Plot)
                .join(FieldAutomatPlot, FieldAutomatPlot.plot_id == Plot.id)
                .where(
                    FieldAutomatPlot.automat_id == row.id,
                    Plot.owner_identity_id == row.owner_identity_id,
                )
                .order_by(FieldAutomatPlot.rank)
            )
        )
        .scalars()
        .all()
    )
    if not plots:
        return []
    node = await session.get(Node, row.node_id)
    epoch = await world.epoch(session)
    found: list[Bed] = []
    for plot in plots:
        if plot.state is not PlotState.SOWN or plot.culture_id is None:
            found.append(Bed(plot, None, None))
            continue
        plant, variety = await farm.sown_of(session, catalog, plot)
        seen = farm.peek(constants, plant, farm.signs_of(plant, variety), node, epoch, plot, now)
        found.append(Bed(plot, seen, farm.stage_of(constants, seen.growth)))
    return found


def _done(
    constants: Constants, step: dict[str, Any], beds: list[Bed], row: FieldAutomat, now: datetime
) -> bool:
    """Whether the action under the cursor is done on every plot (D-339 p. 2)."""
    kind = step["do"]
    if kind == PLOW:
        return all(bed.plot.state in (PlotState.PLOWED, PlotState.SOWN) for bed in beds)
    if kind == SOW:
        return all(bed.growing for bed in beds)
    if kind == HARVEST:
        return not any(bed.growing for bed in beds)
    if kind == FALLOW:
        days = timedelta(hours=float(step["days"]) * farm.day_hours(constants))
        return now >= row.step_since + days
    return True  # pragma: no cover -- a setpoint is passed before it is asked


def walk(constants: Constants, row: FieldAutomat, beds: list[Bed], now: datetime) -> None:
    """Move the cursor past the setpoints and the actions already done.

    At most one turn of the circle: a programme whose every action is done at
    once -- a plough and a harvest over ploughed strips -- stands where the
    turn ended rather than spinning the tick.
    """
    program = row.program
    size = len(program)
    for _ in range(size):
        step = program[row.cursor % size]
        if step["do"] not in SETPOINTS and not _done(constants, step, beds, row, now):
            return
        row.cursor = (row.cursor + 1) % size
        row.step_since = now


def unploughed(plot: Plot) -> bool:
    """Idle, or under a hand's plough that was paused and left (D-277): the
    machine takes such a strip up where the hand left it. A plough still
    running is the hand's and is waited for."""
    return plot.state is PlotState.IDLE or farm.plow_paused(plot)


def plan(shift: Shift, beds: list[Bed]) -> tuple[list[Due], str | None]:
    """What is due now, most urgent first, and a trouble no action can answer.

    The ripe bed under a harvest first -- it drinks while it waits -- then
    the setpoints in force (the moisture, the feedings, the thinning, the
    weeding), then the plough or the sowing under the cursor. A strip the
    sowing finds unploughed is a trouble: the programme forgot its plough.
    """
    constants, row, now = shift.constants, shift.row, shift.now
    program = row.program
    step = program[row.cursor % len(program)]
    held = in_force(program, row.cursor)
    growing = [(bed, bed.life) for bed in beds if bed.growing and bed.life is not None]
    due: list[Due] = []

    if step["do"] == HARVEST:
        due += [
            Due("harvest", ("harvest",), bed.area, partial(shift.harvest, bed.plot.id))
            for bed, life in growing
            if life.ripe
        ]

    moisture = [line for line in held if line["do"] == MOISTURE]
    if moisture:
        target = float(moisture[-1]["target"])
        floor = target - constants[R.AGRO_MOISTURE_BAND]
        due += [
            Due("water", ("water",), bed.area, partial(shift.water_to, bed.plot.id, target))
            for bed, life in growing
            if life.moisture <= floor
        ]

    #: A stage fed is a stage closed, by a hand or by another line (D-339
    #: p. 3, 4): the machine never gives a stage its second dose.
    for line in (line for line in held if line["do"] == FEED):
        for bed, _life in growing:
            fed = bool((bed.plot.fed or {}).get(line["stage"]))
            if bed.stage == line["stage"] and not fed:
                act = partial(shift.feed, bed.plot.id, line["goods"], line["stage"])
                due.append(Due("feed", ("feed", line["goods"]), bed.area, act))

    if any(line["do"] == THIN for line in held):
        due += [
            Due("thin", ("thin",), bed.area, partial(shift.thin, bed.plot.id))
            for bed, _life in growing
            if not bed.plot.thinned and farm.thinning_open(constants, str(bed.stage))
        ]

    weeding = [line for line in held if line["do"] == WEED]
    if weeding:
        every = timedelta(hours=float(weeding[-1]["days"]) * farm.day_hours(constants))
        for bed, _life in growing:
            since = bed.plot.weeded_at or bed.plot.sown_at
            if since is not None and now - since >= every:
                act = partial(shift.weed, bed.plot.id, every)
                due.append(Due("weed", ("weed",), bed.area, act))

    trouble: str | None = None
    if step["do"] == PLOW:
        due += [
            Due("plow", ("plow",), bed.area, partial(shift.plow, bed.plot.id))
            for bed in beds
            if unploughed(bed.plot)
        ]
    elif step["do"] == SOW:
        for bed in beds:
            if bed.plot.state is PlotState.PLOWED:
                act = partial(shift.sow, bed.plot.id, step["culture"])
                due.append(Due("sow", ("sow", step["culture"]), bed.area, act))
            elif unploughed(bed.plot):
                trouble = NOT_PLOWED
    return due, trouble
