# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The bed's clock (D-296): the life read off a plot row, walked to a moment,
and written back -- by an action, or by the world tick.

`peek` computes and writes nothing: it is what the survey shows. `settle`
writes what `peek` computed and tells the death or the ripening it found.
The tick walks every growing bed but writes only where something was
decided or where the stamp grew old enough to keep the reads' walk short.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.constants.catalog import Plant
from src.engine import breed, climate, events, world
from src.engine.farm import life
from src.engine.farm._base import day_hours
from src.models.event import EventKind
from src.models.farm import Plot, PlotState
from src.models.plant import Variety
from src.models.world import Node
from src.units import ROUND_QUALITY, SCALE_MAX, SCALE_MIN, SECONDS_PER_HOUR, on_grid


def _signs(plant: Plant, variety: Variety | None) -> dict[str, Any]:
    """The cultivar's numbers over the crop's: what was sown from one's own
    fund no longer has the crop's catalogue numbers (D-057)."""
    if variety is not None and variety.traits:
        return variety.traits
    return breed.traits_of_plant(plant)


def _life_of(plot: Plot) -> life.Life:
    return life.Life(
        moisture=float(plot.moisture),
        health=float(plot.health),
        growth=float(plot.growth),
        boost=float(plot.growth_boost),
        boost_stage=plot.boost_stage,
        weeds=float(plot.weeds),
        thinned=plot.thinned,
        fed=dict(plot.fed or {}),
        pest={str(name): float(value) for name, value in (plot.pest or {}).items()},
        illness=float(plot.illness),
        illness_kind=plot.illness_kind,
    )


def _weather(
    constants: Constants, node: Node | None, epoch: datetime | None, since: datetime
) -> life.Weather:
    """The place as the bed feels it, with the temperature counted from `since`."""

    def temperature_at(hours: float) -> float | None:
        if node is None:
            return None
        return climate.temperature_now(constants, node, epoch, since + timedelta(hours=hours))

    return life.Weather(
        rain=0.0 if node is None else climate.precipitation(node),
        river=world.has_place(node, world.WATER),
        temperature_at=temperature_at,
    )


def guarded_hours(constants: Constants, plot: Plot, since: datetime) -> dict[str, float]:
    """How many hours of a treatment are left at `since`, pest by pest (D-299).

    The row keeps the guard by the class of the thing it was made with, and
    the vault couples the class to the trouble it puts out (`farm.pest_cure`)
    -- so a fifth preparation is a recipe and a row, never a branch here.
    """
    held: dict[str, float] = {}
    for pest, klass in constants[R.FARM_PEST_CURE].items():
        until = (plot.guard or {}).get(str(klass))
        if not until:
            continue
        left = (datetime.fromisoformat(str(until)) - since).total_seconds() / SECONDS_PER_HOUR
        if left > 0:
            held[str(pest)] = left
    return held


def showing(constants: Constants, state: life.Life) -> bool:
    """Whether the bed's trouble is far enough along to be seen (D-299).

    The threshold is the sign's own (`farm.pest_seen`): the journal must not
    say what the summary does not yet show.
    """
    return state.illness_kind is not None and state.illness >= constants[R.FARM_PEST_SEEN]


def peek(
    constants: Constants,
    plant: Plant,
    signs: dict[str, Any],
    node: Node | None,
    epoch: datetime | None,
    plot: Plot,
    now: datetime,
) -> life.Life:
    """The bed's life at `now`, computed and not written: what every read shows.

    The same steps from the same stamp the tick will take, so a survey and
    the settling behind it never disagree about the same bed.
    """
    was = _life_of(plot)
    if plot.settled_at is None:
        return was
    hours = (now - plot.settled_at).total_seconds() / SECONDS_PER_HOUR
    return life.advance(
        constants,
        life.norms(constants, plant, signs),
        _weather(constants, node, epoch, plot.settled_at),
        was,
        hours=hours,
        day_hours=day_hours(constants),
        fertility=float(plot.fertility),
        guarded=guarded_hours(constants, plot, plot.settled_at),
    )


def _store(plot: Plot, state: life.Life, moment: datetime) -> None:
    clamp = lambda value: max(SCALE_MIN, min(SCALE_MAX, value))  # noqa: E731
    plot.moisture = on_grid(clamp(state.moisture), ROUND_QUALITY)
    plot.health = on_grid(clamp(state.health), ROUND_QUALITY)
    plot.growth = on_grid(clamp(state.growth), ROUND_QUALITY)
    plot.growth_boost = on_grid(max(0.0, state.boost), ROUND_QUALITY)
    plot.boost_stage = state.boost_stage
    plot.weeds = on_grid(clamp(state.weeds), ROUND_QUALITY)
    #: The pressures go to the row as plain numbers: the column is JSONB, and
    #: a Decimal has no place in it (D-299).
    plot.pest = {name: round(clamp(value), ROUND_QUALITY) for name, value in state.pest.items()}
    plot.illness = on_grid(clamp(state.illness), ROUND_QUALITY)
    plot.illness_kind = state.illness_kind
    plot.settled_at = moment


def _clear(plot: Plot, moment: datetime) -> None:
    """The bed is bare again: harvested or dead, the crop and its life are gone."""
    plot.culture_id = None
    plot.variety_id = None
    plot.seed_vigor = None
    plot.sown_at = None
    plot.settled_at = None
    plot.growth = Decimal(0)
    plot.growth_boost = Decimal(0)
    plot.boost_stage = None
    plot.fed = {}
    plot.overfed = 0
    plot.weeds = Decimal(0)
    plot.thinned = False
    plot.pest = {}
    plot.illness = Decimal(0)
    plot.illness_kind = None
    plot.guard = {}
    plot.state = PlotState.IDLE
    plot.idle_since = moment


async def _sown(
    session: AsyncSession, catalog: Catalog, plot: Plot
) -> tuple[Plant, Variety | None]:
    plant = catalog.plants.by_id(plot.culture_id)
    variety = await session.get(Variety, plot.variety_id) if plot.variety_id is not None else None
    return plant, variety


async def _die(
    session: AsyncSession, constants: Constants, plot: Plot, plant: Plant, moment: datetime
) -> None:
    """The crop is gone (D-296): the bed goes back to fallow, the seed with
    it, and the land pays the cycle's depletion (D-256) -- it fed the plant
    all the same, and a dead cycle counts in the crop history like a reaped
    one, or a killed bed would launder a monoculture.
    """
    depletion = constants[R.FARM_SOIL_DEPLETION] + (
        constants[R.FARM_MONOCULTURE_PENALTY] if plot.last_culture == plant.id else 0.0
    )
    plot.fertility = on_grid(max(SCALE_MIN, float(plot.fertility) - depletion), ROUND_QUALITY)
    plot.same_culture_cycles = plot.same_culture_cycles + 1 if plot.last_culture == plant.id else 1
    plot.last_culture = plant.id
    _clear(plot, moment)
    await session.flush()
    await events.record(
        session,
        EventKind.PLOT_DIED,
        actor_identity_id=plot.owner_identity_id,
        node_id=plot.node_id,
        plot_id=str(plot.id),
        culture=plant.id,
        fertility=float(plot.fertility),
    )


async def settle(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    plot: Plot,
    *,
    now: datetime,
    node: Node | None = None,
    epoch: datetime | None = None,
) -> life.Life:
    """Bring a sown bed's life up to `now` and write it down.

    The one place the scales are written from the clock: every action calls
    it before acting and the world tick calls it for the beds it decided on,
    so a death or a ripening is told where it is found, whoever found it. A
    bed that is not growing is returned as it stands. Under the caller's
    lock: the actions take the bed through `api.commands.farm._plot`, the
    tick takes each row's own.
    """
    if plot.state is not PlotState.SOWN or plot.culture_id is None or plot.settled_at is None:
        return _life_of(plot)
    if node is None:
        node = await session.get(Node, plot.node_id)
    if epoch is None:
        epoch = await world.epoch(session)
    plant, variety = await _sown(session, catalog, plot)
    was = _life_of(plot)
    state = peek(constants, plant, _signs(plant, variety), node, epoch, plot, now)
    _store(plot, state, now)
    if state.dead:
        await _die(session, constants, plot, plant, now)
    else:
        #: Two crossings, not one choice: a bed can be struck and reach its
        #: ripeness inside the same walk -- the stamp lives up to a Terran day
        #: -- and an `elif` here would swallow the ripening for good.
        if showing(constants, state) and not showing(constants, was):
            #: Told when it shows, not when it starts (D-299): the journal
            #: names the sign the eye can see, and the sign begins at
            #: `farm.pest_seen`. Which bottle answers it is the text's to say.
            await events.record(
                session,
                EventKind.PLOT_STRUCK,
                actor_identity_id=plot.owner_identity_id,
                node_id=plot.node_id,
                plot_id=str(plot.id),
                culture=plant.id,
                sign=life.PEST_SIGNS[str(state.illness_kind)],
            )
        if state.ripe and not was.ripe:
            await events.record(
                session,
                EventKind.PLOT_RIPENED,
                actor_identity_id=plot.owner_identity_id,
                node_id=plot.node_id,
                plot_id=str(plot.id),
                culture=plant.id,
            )
    await session.flush()
    return state


async def tick_plots(
    session: AsyncSession, constants: Constants, catalog: Catalog, *, now: datetime | None = None
) -> dict[str, int]:
    """Walk every growing bed of the world (D-296) and write where it matters.

    A death or a ripening is told the hour it happens, not when the owner
    next looks. Everything else is a read: the bed's life is a function of
    its stamp, and rewriting every row every hour would only put the
    farmers' actions in the queue behind the tick. A stamp older than a
    Terran day is refreshed all the same, so the walk a read takes from it
    stays short. Each row written is locked on its own and judged again under
    the lock: an action may have committed while the read was free.
    """
    moment = now or datetime.now(UTC)
    ids = (
        (
            await session.execute(
                select(Plot.id).where(Plot.state == PlotState.SOWN).order_by(Plot.id)
            )
        )
        .scalars()
        .all()
    )
    epoch = await world.epoch(session)
    stale = timedelta(hours=day_hours(constants))
    died = ripened = stricken = 0
    for plot_id in ids:
        plot = await session.get(Plot, plot_id)
        if (
            plot is None
            or plot.state is not PlotState.SOWN
            or plot.culture_id is None
            or plot.settled_at is None
        ):
            continue
        node = await session.get(Node, plot.node_id)
        plant, variety = await _sown(session, catalog, plot)
        seen = peek(constants, plant, _signs(plant, variety), node, epoch, plot, moment)
        was_ripe = float(plot.growth) >= SCALE_MAX
        struck = showing(constants, seen) and not showing(constants, _life_of(plot))
        crossing = seen.dead or struck or (seen.ripe and not was_ripe)
        if not crossing and moment - plot.settled_at < stale:
            continue
        locked = await session.get(Plot, plot_id, with_for_update=True, populate_existing=True)
        if locked is None or locked.state is not PlotState.SOWN:
            continue
        before = float(locked.growth) >= SCALE_MAX
        was_struck = showing(constants, _life_of(locked))
        state = await settle(
            session, constants, catalog, locked, now=moment, node=node, epoch=epoch
        )
        if locked.state is not PlotState.SOWN:
            died += 1
        else:
            if showing(constants, state) and not was_struck:
                stricken += 1
            if state.ripe and not before:
                ripened += 1
    return {"plots_died": died, "plots_ripened": ripened, "plots_struck": stricken}
