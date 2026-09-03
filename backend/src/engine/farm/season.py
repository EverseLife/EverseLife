# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The season's work (D-293): sowing, the actions of care -- a watering up to
a target and a feeding in a stage -- the settling of a bed's life by the
clock, the harvest with its seed return, and the survey that tells the
field's whole state without writing a thing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current_catalog
from src.constants import registry as R
from src.constants.catalog import Plant
from src.engine import breed, climate, events, food, world
from src.engine.farm import life
from src.engine.farm._base import (
    FERTILIZER,
    WATER,
    FarmError,
    NoSeeds,
    NoWater,
    WrongClimate,
    WrongState,
    _consume,
    _here,
    _owned,
    care_minutes,
    day_hours,
    plow_minutes,
    plow_progress_minutes,
)
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.farm import Plot, PlotState
from src.models.identity import Body
from src.models.inventory import Item
from src.models.job import Job, JobKind
from src.models.plant import Variety
from src.models.world import Node
from src.units import (
    PERCENT,
    ROUND_QUALITY,
    SCALE_MAX,
    SCALE_MIN,
    SECONDS_PER_HOUR,
    amount,
    amount_float,
    on_grid,
)


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
    )


def _store(plot: Plot, state: life.Life, moment: datetime) -> None:
    clamp = lambda value: max(SCALE_MIN, min(SCALE_MAX, value))  # noqa: E731
    plot.moisture = on_grid(clamp(state.moisture), ROUND_QUALITY)
    plot.health = on_grid(clamp(state.health), ROUND_QUALITY)
    plot.growth = on_grid(clamp(state.growth), ROUND_QUALITY)
    plot.growth_boost = on_grid(max(0.0, state.boost), ROUND_QUALITY)
    plot.boost_stage = state.boost_stage
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
    """The crop is gone (D-293): the bed goes back to fallow, the seed with
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
    it before acting and the world tick calls it for every bed, so a death or
    a ripening is told where it is found, whoever found it. A bed that is not
    growing is returned as it stands. Under the caller's lock: the actions
    take the bed through `api.commands.farm._plot`, the tick takes its own.
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
    elif state.ripe and not was.ripe:
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
    """Advance every growing bed of the world (D-293).

    A death or a ripening is told the hour it happens, not when the owner
    next looks -- the survey reads the same life, but a read writes nothing.
    """
    moment = now or datetime.now(UTC)
    plots = (
        (
            await session.execute(
                select(Plot).where(Plot.state == PlotState.SOWN).order_by(Plot.id).with_for_update()
            )
        )
        .scalars()
        .all()
    )
    epoch = await world.epoch(session)
    died = ripened = 0
    for plot in plots:
        was_ripe = float(plot.growth) >= SCALE_MAX
        state = await settle(session, constants, catalog, plot, now=moment, epoch=epoch)
        if plot.state is not PlotState.SOWN:
            died += 1
        elif state.ripe and not was_ripe:
            ripened += 1
    return {"plots_died": died, "plots_ripened": ripened}


async def _hands_busy(
    session: AsyncSession, body: Body, plot: Plot, moment: datetime, minutes: float, cause: Any
) -> None:
    """The action holds the hands for its minutes (D-211, D-293): a job whose
    only work is to be pending -- the watering or the feeding wrote its effect
    when the button was pressed."""
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


async def sow(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    plot: Plot,
    seeds: Item,
    *,
    now: datetime | None = None,
) -> Plot:
    """Sow with seeds of a specific cultivar (D-057).

    One sows with seeds, not harvest: the batch has a cultivar and its own
    strength. Both move to the plot -- the harvest is computed from them, not
    from the crop's numbers. The bed's life starts here: half-wet ground
    (`farm.sown_moisture`), full health, nought grown (D-293).
    """
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(plot, body)
    if plot.state is not PlotState.PLOWED:
        raise WrongState(key="farm-not-plowed", plot=plot.name)

    variety = await breed._variety_of(session, seeds)  # noqa: SLF001
    plant = catalog.plants.by_id(variety.culture_id)
    if seeds.type_key != plant.seed:  # pragma: no cover -- cultivar and seed come from data
        raise NoSeeds(key="farm-wrong-seeds", goods=seeds.type_key, culture=plant.name)

    pocket = await world.body_container(session, body)
    if seeds.container_id != pocket.id:
        raise NoSeeds(key="farm-seeds-not-in-hands")

    #: The climate gate (D-261): the crop lives through every hour of its
    #: cycle, so the node's whole daily band must fit the culture's range,
    #: and the day must carry enough light. A node without a temperature
    #: record -- old ones, a ship's hydroponics bay -- carries no gate:
    #: absence of a record is not a climate.
    node = await session.get(Node, plot.node_id)
    mean = climate.mean_temperature(node)
    if node is not None and mean is not None:
        swing = climate.swing_of(constants, node.planet)
        wants = plant.requires.temp
        if mean - swing < wants["min"]:
            raise WrongClimate(key="farm-too-cold", culture=plant.name, night=round(mean - swing))
        if mean + swing > wants["max"]:
            raise WrongClimate(key="farm-too-hot", culture=plant.name, noon=round(mean + swing))
        shine = await climate.daylight(session, constants, node)
        if shine < plant.requires.light:
            raise WrongClimate(
                key="farm-too-dark",
                culture=plant.name,
                light=shine,
                need=int(plant.requires.light),
            )

    need = amount(constants[R.FARM_SEED_RATE] * float(plot.area_m2))
    if seeds.amount < need:
        raise NoSeeds(
            key="farm-not-enough-seeds",
            seeds=plant.seed,
            need=amount_float(need),
            have=amount_float(seeds.amount),
        )
    seeds.amount -= need
    strength = float(seeds.vigor) if seeds.vigor is not None else SCALE_MAX
    if seeds.amount <= 0:
        await session.delete(seeds)

    plot.state = PlotState.SOWN
    plot.culture_id = plant.id
    plot.variety_id = variety.id
    plot.seed_vigor = Decimal(str(strength))
    plot.sown_at = moment
    plot.moisture = on_grid(constants[R.FARM_SOWN_MOISTURE], ROUND_QUALITY)
    plot.health = Decimal(str(SCALE_MAX))
    plot.growth = Decimal(0)
    plot.growth_boost = Decimal(0)
    plot.boost_stage = None
    plot.fed = {}
    plot.overfed = 0
    plot.settled_at = moment
    await session.flush()

    await events.record(
        session,
        EventKind.PLOT_SOWN,
        actor_identity_id=body.identity_id,
        node_id=plot.node_id,
        plot_id=str(plot.id),
        culture=plant.id,
        variety=str(variety.id),
        vigor=strength,
        seeds=amount_float(need),
    )
    return plot


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
    anything else burns `farm.feed_wrong_burn` of health, and a second
    feeding in one stage runs the crop to leaf -- a share of the harvest per
    repeat (`farm.overfeed_yield_penalty`). The dose is the land's
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


async def harvest(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    plot: Plot,
    *,
    select_seed: bool = False,
    now: datetime | None = None,
) -> float:
    """Harvest. Returns the collected amount.

    The harvest is proportional to area, fertility, the crop's health **and
    cultivar strength** (D-293); land depletion and recovery are credited
    right here -- the harvest closes the cycle. Feedings repeated in a stage
    ran the crop to leaf and take their share off (`farm.overfeed_yield_penalty`).

    Seeds come back as a multiple of what was sown (`farm.seed_return`, D-257),
    scaled by the same soil, health and lot-strength shares as the goods: the
    fund reproduces by construction, and a sick bed, poor soil or a weak lot
    honestly sink the return below one. If the farmer did **selection** --
    in-person work where mastery shows -- the fund keeps its strength; if not,
    the seeds degrade, and a hybrid additionally segregates (D-057, D-067).
    """
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(plot, body)
    if plot.state is not PlotState.SOWN or plot.culture_id is None:
        raise WrongState(key="farm-nothing-to-harvest", plot=plot.name)

    node = await session.get(Node, plot.node_id)
    state = await settle(session, constants, catalog, plot, now=moment, node=node)
    if state.dead:
        raise WrongState(key="farm-nothing-to-harvest", plot=plot.name)
    if not state.ripe:
        raise WrongState(
            key="farm-not-ripe", plot=plot.name, stage=life.stage_of(constants, state.growth)
        )

    plant, found = await _sown(session, catalog, plot)
    #: The cultivar decides the numbers. Old plots without a cultivar count as base.
    variety = found or await breed.landrace(session, catalog, plant.id)
    signs = _signs(plant, variety)
    strength = float(plot.seed_vigor) if plot.seed_vigor is not None else SCALE_MAX

    area = float(plot.area_m2)
    fertility = float(plot.fertility)
    health_share = state.health / SCALE_MAX
    leaf_share = max(0.0, 1 - constants[R.FARM_OVERFEED_YIELD_PENALTY] / PERCENT * plot.overfed)
    #: Capped above: rich land is an edge, not a multiplier (D-256).
    soil_share = min(
        fertility / float(signs.get("fertility", plant.requires.fertility)),
        constants[R.FARM_SOIL_SHARE_CAP] / PERCENT,
    )

    got = (
        area
        * float(signs.get("yield_per_m2", plant.yield_per_m2))
        * soil_share
        * health_share
        * leaf_share
        * (strength / PERCENT)
    )
    quality = max(SCALE_MIN, min(SCALE_MAX, fertility * health_share))

    pocket = await world.body_container(session, body)
    if got > 0:
        reaped = Item(
            container_id=pocket.id,
            type_key=plant.gives,
            amount=amount(got),
            quality=Decimal(str(quality)),
            #: The harvest spoils at the cultivar's speed: turnip faster than flax.
            spoils_at=food.harvest_spoils_at(
                constants,
                float(signs.get("spoilage_k", plant.traits.spoilage_k)),
                now=moment,
            ),
        )
        session.add(reaped)
        #: Plots reaped in one round give one heap, if the harvest came out the
        #: same to the last number -- shelf life included (D-214).
        await world.stack_up(session, reaped)

    #: Own seed: a multiple of the sowing norm, not a share of the goods (D-257).
    seed_amount = (
        constants[R.FARM_SEED_RATE]
        * area
        * constants[R.FARM_SEED_RETURN]
        * soil_share
        * health_share
        * leaf_share
        * (strength / PERCENT)
    )
    if seed_amount > 0:
        seed_strength = breed.next_vigor(constants, variety, strength, selected=select_seed)
        if select_seed:
            await breed.select_generation(session, constants, variety)
        await breed.seed_lot(
            session, catalog, pocket.id, variety, seed_amount, seed_strength, now=moment
        )

    #: Every harvest takes from the land; the land remembers what grew on it,
    #: and a repeat of the same crop takes extra (D-256).
    depletion = constants[R.FARM_SOIL_DEPLETION] + (
        constants[R.FARM_MONOCULTURE_PENALTY] if plot.last_culture == plant.id else 0.0
    )
    restored = plant.restores_fertility
    settled = max(SCALE_MIN, min(SCALE_MAX, fertility - depletion + restored))
    plot.fertility = on_grid(settled, ROUND_QUALITY)
    plot.same_culture_cycles = plot.same_culture_cycles + 1 if plot.last_culture == plant.id else 1
    plot.last_culture = plant.id
    overfed = plot.overfed
    _clear(plot, moment)
    await session.flush()

    await events.record(
        session,
        EventKind.PLOT_HARVESTED,
        actor_identity_id=body.identity_id,
        node_id=plot.node_id,
        plot_id=str(plot.id),
        culture=plant.id,
        variety=str(variety.id),
        selected=select_seed,
        got=got,
        seeds=seed_amount,
        quality=quality,
        health=state.health,
        overfed=overfed,
        fertility=float(plot.fertility),
    )
    return got


async def survey(
    session: AsyncSession, constants: Constants, catalog: Catalog, identity_id: uuid.UUID
) -> list[dict]:
    """Farm summary. Remote: readable from anywhere, care -- on foot.

    A growing bed is shown as of this very moment -- its life computed from
    the last stamp and written nowhere (D-293). Two words and one curve: the
    stage, the word of health, and the moisture with the pace it leaves at,
    so the client draws the curve forward without a timer of its own (D-226).
    Nothing derivable rides along (D-225): the band, the feeding table and the
    days to ripeness are not in the row -- the first two are the Library's
    text, the last does not exist.
    """
    now = datetime.now(UTC)
    plots = (
        await session.execute(
            select(Plot, Node)
            .join(Node, Node.id == Plot.node_id)
            .where(Plot.owner_identity_id == identity_id)
            .order_by(Plot.created_at)
        )
    ).all()

    #: The whole summary in two queries, not one per bed: the cultivars are
    #: read for the list at once. A sown plot whose cultivar row is missing
    #: shows the base line's numbers from a transient object -- `landrace` is
    #: get-or-create, and a survey is a read.
    sown = [
        plot
        for plot, _ in plots
        if plot.state is PlotState.SOWN and plot.culture_id is not None and plot.settled_at
    ]
    ids = {plot.variety_id for plot in sown if plot.variety_id is not None}
    found: dict[uuid.UUID, Variety] = {}
    if ids:
        rows = await session.execute(select(Variety).where(Variety.id.in_(ids)))
        found = {cultivar.id: cultivar for cultivar in rows.scalars()}
    varieties = {
        plot.id: found.get(plot.variety_id) or breed.base_line(catalog, plot.culture_id)
        for plot in sown
    }

    epoch = await world.epoch(session)
    out: list[dict] = []
    for plot, node in plots:
        row: dict = {
            "id": str(plot.id),
            "name": plot.name,
            "node": node.name,
            "node_key": node.key,
            "area": float(plot.area_m2),
            "state": plot.state.value,
            "fertility": float(plot.fertility),
            "culture": plot.culture_id,
        }
        if plot.state is PlotState.PLOWING:
            #: The plough's progress (D-277): the share done and, while a run
            #: is under way, when it began and ends, for the deadline bar. The
            #: client cannot derive either (D-225): the norm is the engine's
            #: and the bank is the row's. "Paused" it derives itself -- under
            #: the plough with no run under way -- so it is not sent.
            whole = plow_minutes(constants, plot)
            done = plow_progress_minutes(plot, now)
            row["plow_share"] = min(1.0, done / whole) if whole > 0 else 1.0
            if plot.plow_since is not None:
                left = max(0.0, whole - done)
                row["plow_since"] = plot.plow_since.isoformat()
                row["plow_ready_at"] = (now + timedelta(minutes=left)).isoformat()
        if plot.id in varieties:
            plant = catalog.plants.by_id(plot.culture_id)
            variety = varieties[plot.id]
            signs = _signs(plant, variety)
            norm = life.norms(constants, plant, signs)
            state = peek(constants, plant, signs, node, epoch, plot, now)
            stage = life.stage_of(constants, state.growth)
            weather = _weather(constants, node, epoch, now)

            #: No `culture_name` beside `culture` (D-225): the client reads
            #: the word from `/public/renames`. The cultivar goes the same way
            #: -- key, mark or generation -- and the client says it (D-251).
            row["variety"] = breed.shown_as(catalog, variety)
            row["stage"] = stage
            row["ripe"] = state.ripe
            row["health"] = life.health_word(constants, state.health)
            #: The point and the pace: the curve is the client's to draw. The
            #: pace is the one of this hour -- with the heat, the rain and the
            #: river in it, none of which the client is told (D-225).
            row["moisture"] = round(state.moisture, 1)
            row["moisture_at"] = now.isoformat()
            row["dry_per_day"] = round(
                life.dry_rate(constants, norm, weather, weather.temperature_at(0.0)) * PERCENT,
                ROUND_QUALITY,
            )
            #: Only where it is actually carried (D-126): by a river the
            #: watering takes from the river, and the window must not ask the
            #: farmer to bring what the engine does not ask for.
            if not weather.river:
                row["carried"] = True
            given = (plot.fed or {}).get(stage, [])
            if given:
                row["fed"] = True
            #: The engine names the sign, the client picks the word (D-057).
            row["symptoms"] = life.symptoms(
                norm,
                state,
                fertility=float(plot.fertility),
                fertility_needed=float(signs.get("fertility", plant.requires.fertility)),
                fed=given,
            )
        out.append(row)
    return out
