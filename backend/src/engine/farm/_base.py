# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The field's vocabulary and floor: every refusal the land can make, the
day's hours and the fallow's accrual, and the small guards -- whose plot,
what state, what ground. Asks nobody above itself.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_FLOOR, Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current_catalog
from src.constants import registry as R
from src.engine import liquid, occupation, stock, travel, world
from src.engine.errors import Refusal
from src.models.farm import Plot, PlotState
from src.models.identity import Body, BodyState
from src.models.world import Node
from src.units import (
    ROUND_QUALITY,
    SCALE_MAX,
    SCALE_MIN,
    SECONDS_PER_HOUR,
    SECONDS_PER_MINUTE,
    on_grid,
)

#: The name of water in `build/recipes.json` -- carried by hand where there is no river.
WATER = "water"

#: The fertilizer thing class (D-215, D-291). The engine knows a fertilizer
#: by its class and its strength by its row in `farm.fertilizer_recovery`: a
#: third fertilizer is a recipe with the class and a row, not a code change.
FERTILIZER = "fertilizer"


class FarmError(Refusal):
    pass


class NoLand(FarmError):
    """The node's land is finite: arable capacity is not elastic."""


class NotYours(FarmError):
    pass


class WrongState(FarmError):
    """The plot is in the wrong state: the unsown is not harvested, the ripe is not ploughed."""


class NoSeeds(FarmError):
    pass


class WrongClimate(FarmError):
    """The place refuses the culture (D-261): too cold, too hot or too dark."""


class NoWater(FarmError):
    """In a dry place water is carried by hand (D-126)."""


class TooSmall(FarmError):
    """Surveying less than `farm.plot_min_area` is pointless."""


def day_hours(constants: Constants) -> float:
    """Terra's day. All farming terms are given in it (D-008)."""
    return constants[R.TIME_DAY_TERRA]


def care_minutes(constants: Constants, area: float) -> float:
    """One action's minutes (D-293): a vault formula. Land scales, hands do not."""
    return constants[R.FARM_PLOT_OVERHEAD] + constants[R.FARM_CARE_TIME_PER_M2] * area


# --- internal ----------------------------------------------------------------


async def _open_ground(session: AsyncSession, node: Node) -> None:
    """Refuse a plot where nothing grows in the open ground (D-231).

    A climate is a property of the planet, and both of the ones the world has
    are lethal to a field: under the permafrost the soil never thaws, and where
    the ground bakes a sprout burns the day it comes up. Aurora's plots want
    greenhouses and Pyroxis wants nothing at all -- until that mechanic exists,
    the food of both arrives by ship (D-232, D-233).

    Refused at the marking out, not at the sowing: the plot is the estate, and
    a plot nobody can ever sow would be a thing sold to a player for nothing.
    """
    from src.engine import frost  # noqa: PLC0415 -- lazy: breaks the import cycle with frost
    from src.engine.ship import is_aboard  # noqa: PLC0415 -- lazy: ship imports farm through food

    #: A node aboard a ship is not open ground and never was: the hull holds
    #: its own air and its own warmth, and hydroponics lives there (D-234).
    weather = await frost.climate_of(session, node)
    if weather is None or is_aboard(node):
        return
    raise FarmError(key="farm-no-open-ground", node=node.name, weather=weather)


async def _here(session: AsyncSession, body: Body) -> None:
    """Present and free: plot work is an occupation like any other (D-211).

    All of it, not the plough alone. Marking out, sowing, the daily round and
    the harvest are short, but they are the body's hands too -- and a plot
    sown while the same body walks the neighbouring land searching would be
    two occupations on one pair of hands, which is exactly what D-211 forbids.
    """
    if body.state is not BodyState.ALIVE:
        raise FarmError(key="farm-dead-works")
    await travel.require_here(session, body)

    await occupation.require_free(session, body)


def _owned(plot: Plot, body: Body) -> None:
    if plot.owner_identity_id != body.identity_id:
        raise NotYours(key="farm-plot-not-yours")


def _recuttable(plot: Plot) -> None:
    if plot.state not in (PlotState.IDLE, PlotState.PLOWED):
        raise WrongState(key="farm-recut-sown", state=plot.state.value)


def _ground_fertility(node: Node) -> float:
    """Starting fertility is a place property (D-126). No property -- it bears nothing."""
    raw = node.properties.get("fertility", 0)
    try:
        return max(SCALE_MIN, min(SCALE_MAX, float(raw)))
    except (TypeError, ValueError):
        return SCALE_MIN


def _accrue_fallow(constants: Constants, plot: Plot, moment: datetime) -> None:
    """Fallow: recovery by elapsed idle time. The land needs no tick, like sleep."""
    if plot.idle_since is None:
        return
    days = max(
        0.0,
        (moment - plot.idle_since).total_seconds() / (day_hours(constants) * SECONDS_PER_HOUR),
    )
    if days <= 0:
        return
    rate = constants[R.FARM_FALLOW_RECOVERY]
    was = float(plot.fertility)
    #: Down: never more fertility than the days earned. Fertility is kept to a
    #: hundredth, and at two a day over a thirty-eight hour day that is a
    #: little under six minutes of lying fallow before the row moves at all.
    #: The stamp used to go to `moment` whatever the column could show for it,
    #: and every touch of a plot accrues -- so a plot worked oftener than that
    #: recovered nothing, ever, and the loss fell on whoever farmed hardest.
    grown = min(SCALE_MAX, float(on_grid(was + rate * days, ROUND_QUALITY, ROUND_FLOOR)))
    plot.fertility = Decimal(str(grown))
    #: The stamp moves only as far as the growth accounts for, and the rest of
    #: the idleness waits in it for the next accrual.
    earned = days if grown >= SCALE_MAX or rate <= 0 else (grown - was) / rate
    plot.idle_since = min(
        moment,
        plot.idle_since
        + timedelta(seconds=max(0.0, earned) * day_hours(constants) * SECONDS_PER_HOUR),
    )


async def _consume(
    session: AsyncSession, body: Body, type_key: str, need: int, *, why: FarmError
) -> None:
    """Write off from the pocket, worst first. Not enough -- the action did not start."""
    pocket = await world.body_container(session, body)
    #: Water comes out of the canister in the hands (D-230): the pocket and
    #: the vessels in it are one stock.
    stacks = await liquid.locked_stacks(
        session, current_catalog(), pocket, (type_key,), worst_first=True
    )
    if sum(stack.amount for stack in stacks) < need:
        raise why
    await stock.consume(session, stacks, need)


def plow_minutes(constants: Constants, plot: Plot) -> float:
    """The whole of the plough on this strip, in minutes: the norm by the area."""
    return constants[R.FARM_PLOW_TIME_PER_M2] * float(plot.area_m2)


def plow_banked(plot: Plot, moment: datetime) -> Decimal:
    """What is ploughed by now, to the last hundredth: the bank and the run at it.

    Exact because the bank is written from it, and a float minute is not: the
    eighth honest pause of six seconds adds `0.1` to a bank of `0.7` and gets
    `0.7999999999999999`, which stored downwards is `0.79`. The hundredth is
    worked for and not kept, and the bank stays behind for good.
    """
    done = Decimal(str(plot.plow_done_minutes))
    if plot.plow_since is not None:
        ran = max(0.0, (moment - plot.plow_since).total_seconds())
        done += Decimal(str(ran)) / Decimal(str(SECONDS_PER_MINUTE))
    return done


def plow_progress_minutes(plot: Plot, moment: datetime) -> float:
    """What is ploughed by now, for shares and remainders: a float will do.

    Deliberately not named after the column. Writing `plot.plow_done_minutes`
    from a float is what cost the bank a hundredth a pause -- the bank is
    written from `plow_banked`, and nothing here rounds back into it.
    """
    return float(plow_banked(plot, moment))


def plow_paused(plot: Plot) -> bool:
    """Under the plough, but nobody at it: paused with its progress kept."""
    return plot.state is PlotState.PLOWING and plot.plow_since is None
