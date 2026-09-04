# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The actions of care (D-293, D-295): a watering up to a target, a feeding in
a stage, a weeding and a thinning. Each is in person, each writes its effect
at once, and each holds the hands for its minutes through a job whose only
work is to be pending.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current_catalog
from src.constants import registry as R
from src.engine import events, world
from src.engine.farm import life
from src.engine.farm._base import (
    FERTILIZER,
    WATER,
    FarmError,
    NoWater,
    WrongState,
    _consume,
    _here,
    _owned,
    care_minutes,
)
from src.engine.farm.settle import _die, _sown, settle
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.farm import Plot, PlotState
from src.models.identity import Body
from src.models.job import Job, JobKind
from src.models.world import Node
from src.units import ROUND_QUALITY, SCALE_MAX, SCALE_MIN, amount, amount_float, on_grid


async def _hands_busy(
    session: AsyncSession, body: Body, plot: Plot, moment: datetime, minutes: float, cause: Any
) -> None:
    """The action holds the hands for its minutes (D-211, D-293): a job whose
    only work is to be pending -- the watering or the feeding wrote its effect
    when the button was pressed. The key is the plough's shape: one job per
    action, told apart by the moment it began."""
    await enqueue(
        session,
        JobKind.FARM_CARE,
        moment + timedelta(minutes=minutes),
        payload={"plot": str(plot.id)},
        dedup_key=f"farm.care:{plot.id}:{moment.timestamp()}",
        cause_event_id=cause.id,
        body_id=body.id,
    )


@handler(JobKind.FARM_CARE)
async def care_done(session: AsyncSession, job: Job) -> None:
    """The minutes are up. Nothing to write: the job existed to be pending."""
    return


async def water(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    plot: Plot,
    target: float,
    *,
    now: datetime | None = None,
) -> tuple[Plot, float]:
    """Water the bed up to `target` moisture (D-293). Returns the litres it took.

    The water is exactly the difference: `farm.water_per_m2` a metre takes
    the ground from dry to full, and a target takes its share of that. By a
    river it comes from the river; elsewhere from the hands, and short of it
    the action does not start -- half a watering is not offered (D-126). A
    target below what the ground holds is refused: the slider went the wrong
    way. A target above the culture's band is not: overwatering is the
    player's mistake, and the bed will show it.
    """
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(plot, body)
    if plot.state is not PlotState.SOWN or plot.culture_id is None:
        raise WrongState(key="farm-nothing-grows", plot=plot.name)
    goal = max(SCALE_MIN, min(SCALE_MAX, float(target)))

    node = await session.get(Node, plot.node_id)
    state = await settle(session, constants, catalog, plot, now=moment, node=node)
    if state.dead:
        raise WrongState(key="farm-nothing-grows", plot=plot.name)
    if goal <= state.moisture:
        raise WrongState(
            key="farm-already-wetter",
            plot=plot.name,
            moisture=round(state.moisture),
            target=round(goal),
        )

    area = float(plot.area_m2)
    litres = (goal - state.moisture) / SCALE_MAX * constants[R.FARM_WATER_PER_M2] * area
    if not world.has_place(node, world.WATER):
        need = amount(litres)
        await _consume(
            session,
            body,
            WATER,
            need,
            why=NoWater(key="farm-no-water", need=amount_float(need)),
        )
    plot.moisture = on_grid(goal, ROUND_QUALITY)
    await session.flush()

    minutes = care_minutes(constants, area)
    event = await events.record(
        session,
        EventKind.PLOT_WATERED,
        actor_identity_id=body.identity_id,
        node_id=plot.node_id,
        plot_id=str(plot.id),
        target=goal,
        litres=litres,
        minutes=minutes,
    )
    await _hands_busy(session, body, plot, moment, minutes, event)
    return plot, litres


async def feed(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    plot: Plot,
    goods: str,
    *,
    now: datetime | None = None,
) -> tuple[Plot, str, str]:
    """Feed the growing bed (D-293). Returns the stage it was fed in and what
    the feeding did -- for the journal and the tests, never for the answer:
    the bed shows its state the next day, the button confirms the action.

    What a fertilizer does in a stage is the culture's table (`feeding` in
    the vault): the right one quickens the growth to the end of the stage,
    anything else burns `farm.feed_wrong_burn` of health -- whole, hardiness
    softens the weather's stress and not a mistake of the hands -- and a
    second feeding in one stage runs the crop to leaf, a share of the harvest
    per repeat (`farm.overfeed_yield_penalty`). The dose is the land's
    (`farm.fertilizer_per_m2`, D-264).
    """
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(plot, body)
    if plot.state is not PlotState.SOWN or plot.culture_id is None:
        raise WrongState(key="farm-nothing-grows", plot=plot.name)
    #: The canon key first: the class is asked by it and the table is read by
    #: it, and a synonym would otherwise pass the one and miss the other.
    book = current_catalog().recipes
    goods = book.resolve(goods)
    if book.class_of(goods) != FERTILIZER:
        raise FarmError(key="farm-not-a-fertilizer", goods=goods)

    node = await session.get(Node, plot.node_id)
    state = await settle(session, constants, catalog, plot, now=moment, node=node)
    if state.dead:
        raise WrongState(key="farm-nothing-grows", plot=plot.name)
    if state.ripe:
        raise WrongState(key="farm-feed-ripe", plot=plot.name)
    plant, _ = await _sown(session, catalog, plot)
    stage = life.stage_of(constants, state.growth)

    area = float(plot.area_m2)
    dose = amount(constants[R.FARM_FERTILIZER_PER_M2] * area)
    await _consume(
        session,
        body,
        goods,
        dose,
        why=FarmError(key="farm-no-fertilizer", goods=goods, need=amount_float(dose)),
    )

    fed = dict(plot.fed or {})
    given = list(fed.get(stage, []))
    suits = next(
        (row for row in plant.feeding if row.stage == stage and row.fertilizer == goods), None
    )
    if given:
        effect = life.OVERFED
        plot.overfed += 1
    elif suits is not None:
        effect = life.BOOST
        plot.growth_boost = on_grid(suits.growth, ROUND_QUALITY)
        plot.boost_stage = stage
    else:
        effect = life.BURN
        burned = state.health - constants[R.FARM_FEED_WRONG_BURN]
        plot.health = on_grid(max(SCALE_MIN, burned), ROUND_QUALITY)
    given.append({"goods": goods, "effect": effect})
    fed[stage] = given
    plot.fed = fed
    await session.flush()

    minutes = care_minutes(constants, area)
    event = await events.record(
        session,
        EventKind.PLOT_FED,
        actor_identity_id=body.identity_id,
        node_id=plot.node_id,
        plot_id=str(plot.id),
        goods=goods,
        stage=stage,
        effect=effect,
        spent=amount_float(dose),
        minutes=minutes,
    )
    if float(plot.health) <= SCALE_MIN:
        await _die(session, constants, plot, plant, moment)
    await _hands_busy(session, body, plot, moment, minutes, event)
    return plot, stage, effect


async def weed(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    plot: Plot,
    *,
    now: datetime | None = None,
) -> Plot:
    """Pull the weeds (D-295): the cover goes to nought, the hands are busy.

    Allowed whenever something grows, seen or not: a farmer who weeds early
    and often pays in minutes, not in a refusal.
    """
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(plot, body)
    if plot.state is not PlotState.SOWN or plot.culture_id is None:
        raise WrongState(key="farm-nothing-grows", plot=plot.name)
    node = await session.get(Node, plot.node_id)
    state = await settle(session, constants, catalog, plot, now=moment, node=node)
    if state.dead:
        raise WrongState(key="farm-nothing-grows", plot=plot.name)
    pulled = state.weeds
    plot.weeds = Decimal(0)
    await session.flush()

    minutes = care_minutes(constants, float(plot.area_m2))
    event = await events.record(
        session,
        EventKind.PLOT_WEEDED,
        actor_identity_id=body.identity_id,
        node_id=plot.node_id,
        plot_id=str(plot.id),
        pulled=pulled,
        minutes=minutes,
    )
    await _hands_busy(session, body, plot, moment, minutes, event)
    return plot


async def thin(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    plot: Plot,
    *,
    now: datetime | None = None,
) -> Plot:
    """Thin the stand (D-295): once, and only up to `farm.thin_until`.

    What is pulled is not put back -- the thinning costs `farm.thin_loss` of
    the harvest -- and what it buys is the culture's own: the crowd penalty
    by its `density_risk`. Whether it pays is the text's to say; the window
    offers it to every stand alike (D-057).
    """
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(plot, body)
    if plot.state is not PlotState.SOWN or plot.culture_id is None:
        raise WrongState(key="farm-nothing-grows", plot=plot.name)
    node = await session.get(Node, plot.node_id)
    state = await settle(session, constants, catalog, plot, now=moment, node=node)
    if state.dead:
        raise WrongState(key="farm-nothing-grows", plot=plot.name)
    if state.thinned:
        raise WrongState(key="farm-thinned-already", plot=plot.name)
    stage = life.stage_of(constants, state.growth)
    if not life.thinning_open(constants, stage):
        raise WrongState(
            key="farm-thin-late", plot=plot.name, until=str(constants[R.FARM_THIN_UNTIL])
        )
    plot.thinned = True
    await session.flush()

    minutes = care_minutes(constants, float(plot.area_m2))
    event = await events.record(
        session,
        EventKind.PLOT_THINNED,
        actor_identity_id=body.identity_id,
        node_id=plot.node_id,
        plot_id=str(plot.id),
        stage=stage,
        minutes=minutes,
    )
    await _hands_busy(session, body, plot, moment, minutes, event)
    return plot
