# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The season's work: sowing, care that costs water and minutes, the harvest
with its seed return, and the survey that tells the field's whole state.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current_catalog
from src.constants import registry as R
from src.constants.catalog import Plant
from src.engine import breed, climate, events, food, world
from src.engine.errors import left_to_say
from src.engine.farm._base import (
    WATER,
    NoSeeds,
    NoWater,
    WrongClimate,
    WrongState,
    _consume,
    _here,
    _owned,
    care_minutes,
    day_hours,
)
from src.models.event import EventKind
from src.models.farm import Plot, PlotState
from src.models.identity import Body
from src.models.inventory import Item
from src.models.plant import Variety
from src.models.world import Node, Planet
from src.units import PERCENT, SCALE_MAX, SCALE_MIN, SECONDS_PER_HOUR, amount, amount_float

#: The catalog's hardiness scale (plants.yaml: 1-5). 5/5 takes the whole
#: relief `farm.hardiness_relief` off the neglect penalty (D-261).
HARDINESS_SCALE = 5.0


def water_need(constants: Constants, plant: Plant, node: Node | None, area: float) -> float:
    """One round's water: the norm by area, the culture's thirst, minus rain (D-261).

    Thirst is `farm.water_by_need` over `requires.water`; rain covers up to
    `site.rain_water_offset` of the round at the top of the scale. A node
    without a rainfall record reads as dry, like the scale's floor.
    """
    thirst = float(constants[R.FARM_WATER_BY_NEED].get(str(int(plant.requires.water)), 1.0))
    rain = min(climate.precipitation(node), PERCENT) / PERCENT if node is not None else 0.0
    covered = constants[R.SITE_RAIN_WATER_OFFSET] / PERCENT * rain
    return constants[R.FARM_WATER_PER_M2] * area * thirst * max(0.0, 1.0 - covered)


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
    from the crop's numbers.
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
    plot.care_credits = 0
    plot.cared_at = None
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


async def care(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    plot: Plot,
    *,
    now: datetime | None = None,
) -> Plot:
    """Do the plot round: once a calendar day of the planet, on foot, with water.

    One day -- one round, **at any hour of it** (D-263): the day is counted
    from the world's epoch, the same scale the client's clock draws, so the
    window never drifts away from the player's own rhythm. It used to be a
    38-hour interval, and the care hour ran away by fourteen every day --
    farming demanded an alarm clock.

    By a river water is taken from the river; in a dry place from the
    inventory, and that makes water a commodity where there is none (D-126).
    """
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(plot, body)
    if plot.state is not PlotState.SOWN or plot.sown_at is None:
        raise WrongState(key="farm-nothing-grows", plot=plot.name)

    node = await session.get(Node, plot.node_id)
    epoch = await world.epoch(session)
    #: The farm's day is Terran everywhere (D-008): the cycle, the ripeness
    #: and this round all count the same day, whatever ground the bed is on
    #: -- a hull's hydroponics must not tend by the planet the ship visits.
    #: `<=` and not equality: a moment handed from the past must not mint a
    #: second credit for a day already tended.
    if plot.cared_at is not None and climate.day_index(
        constants, Planet.TERRA, epoch, moment
    ) <= climate.day_index(constants, Planet.TERRA, epoch, plot.cared_at):
        raise WrongState(key="farm-cared-today")
    if not world.has_place(node, world.WATER):
        #: Thirst and rain are in the norm (D-261). The culture is looked up
        #: by the plot -- a SOWN plot always has one, the state check above
        #: guarantees it -- and the round is for what actually grows here.
        plant = current_catalog().plants.by_id(plot.culture_id)
        need = amount(water_need(constants, plant, node, float(plot.area_m2)))
        await _consume(
            session,
            body,
            WATER,
            need,
            why=NoWater(key="farm-no-water", need=amount_float(need)),
        )

    plot.care_credits += 1
    plot.cared_at = moment
    await session.flush()

    await events.record(
        session,
        EventKind.PLOT_CARED,
        actor_identity_id=body.identity_id,
        node_id=plot.node_id,
        plot_id=str(plot.id),
        credits=plot.care_credits,
        minutes=care_minutes(constants, float(plot.area_m2)),
    )
    return plot


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

    The harvest is proportional to area, fertility, care quality **and cultivar
    strength**; land depletion and recovery are credited right here -- the
    harvest closes the cycle.

    Seeds come back as a multiple of what was sown (`farm.seed_return`, D-257),
    scaled by the same soil, care and lot-strength shares as the goods: the
    fund reproduces by construction, and neglect, poor soil or a weak lot
    honestly sink the return below one. If the farmer did **selection** --
    in-person work where mastery shows -- the fund keeps its strength; if not,
    the seeds degrade, and a hybrid additionally segregates (D-057, D-067).
    """
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(plot, body)
    if plot.state is not PlotState.SOWN or plot.culture_id is None:
        raise WrongState(key="farm-nothing-to-harvest", plot=plot.name)

    plant = catalog.plants.by_id(plot.culture_id)
    #: The cultivar decides the numbers: what was sown from one's own fund no
    #: longer has the crop's catalogue numbers. Old plots without a cultivar count as base.
    variety = (
        await session.get(Variety, plot.variety_id) if plot.variety_id is not None else None
    ) or await breed.landrace(session, catalog, plant.id)
    signs = variety.traits or breed.traits_of_plant(plant)
    cycle = float(signs.get("cycle_days", plant.cycle_days))
    strength = float(plot.seed_vigor) if plot.seed_vigor is not None else SCALE_MAX

    ready = (plot.sown_at or moment) + timedelta(hours=cycle * day_hours(constants))
    if moment < ready:
        raise WrongState(key="farm-not-ripe", cycle=cycle, inner={"left": [left_to_say(ready)]})

    area = float(plot.area_m2)
    fertility = float(plot.fertility)
    #: Skipped care days cut the harvest but can never zero it (D-263): a
    #: miss costs its share of the cycle, so a long crop forgives a single
    #: skip more, not less, and a full walk-out still leaves a quarter.
    #: Hardiness softens the cut on top (D-261) -- the trait the breeder
    #: selects for keeps mattering.
    missed = max(0, int(cycle) - plot.care_credits)
    hardiness = float(signs.get("hardiness", plant.traits.hardiness))
    forgiven = 1 - constants[R.FARM_HARDINESS_RELIEF] / PERCENT * hardiness / HARDINESS_SCALE
    care_share = max(
        0.0,
        1 - constants[R.FARM_NEGLECT_TOTAL] * forgiven * missed / max(cycle, 1.0) / PERCENT,
    )
    #: Capped above: rich land is an edge, not a multiplier (D-256).
    soil_share = min(
        fertility / float(signs.get("fertility", plant.requires.fertility)),
        constants[R.FARM_SOIL_SHARE_CAP] / PERCENT,
    )

    got = (
        area
        * float(signs.get("yield_per_m2", plant.yield_per_m2))
        * soil_share
        * care_share
        * (strength / PERCENT)
    )
    quality = max(SCALE_MIN, min(SCALE_MAX, fertility * max(care_share, 0.0)))

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
        * care_share
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
    plot.fertility = Decimal(str(max(SCALE_MIN, min(SCALE_MAX, fertility - depletion + restored))))
    plot.same_culture_cycles = plot.same_culture_cycles + 1 if plot.last_culture == plant.id else 1
    plot.last_culture = plant.id
    plot.culture_id = None
    plot.variety_id = None
    plot.seed_vigor = None
    plot.sown_at = None
    plot.care_credits = 0
    plot.cared_at = None
    plot.state = PlotState.IDLE
    plot.idle_since = moment
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
        missed_days=missed,
        fertility=float(plot.fertility),
    )
    return got


async def survey(
    session: AsyncSession, constants: Constants, catalog: Catalog, identity_id: uuid.UUID
) -> list[dict]:
    """Farm summary. Remote: readable from anywhere, care -- on foot."""
    now = datetime.now(UTC)
    plots = (
        await session.execute(
            select(Plot, Node)
            .join(Node, Node.id == Plot.node_id)
            .where(Plot.owner_identity_id == identity_id)
            .order_by(Plot.created_at)
        )
    ).all()

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
        if plot.state is PlotState.SOWN and plot.culture_id is not None and plot.sown_at:
            plant = catalog.plants.by_id(plot.culture_id)
            variety = (
                await session.get(Variety, plot.variety_id) if plot.variety_id is not None else None
            ) or await breed.landrace(session, catalog, plant.id)
            signs = variety.traits or breed.traits_of_plant(plant)
            cycle = float(signs.get("cycle_days", plant.cycle_days))
            fertility_needed = float(signs.get("fertility", plant.requires.fertility))

            ready = plot.sown_at + timedelta(hours=cycle * day_hours(constants))
            #: The round goes by the calendar day (D-263), Terran like every
            #: other farm term (D-008): "asks care" means this day has not
            #: seen one, whatever its hour.
            needs_care = plot.cared_at is None or climate.day_index(
                constants, Planet.TERRA, epoch, plot.cared_at
            ) < climate.day_index(constants, Planet.TERRA, epoch, now)
            #: Losses accrue on the day they accrue, not as a surprise at
            #: harvest (D-118).
            elapsed = (now - plot.sown_at).total_seconds() / (
                day_hours(constants) * SECONDS_PER_HOUR
            )
            skipped = max(0, min(int(cycle), int(elapsed)) - plot.care_credits)
            ripe = now >= ready

            #: No `culture_name` beside `culture` (D-225): the client reads
            #: the word from `/public/renames`. The cultivar goes the same way
            #: -- key, mark or generation -- and the client says it (D-251).
            row["variety"] = breed.shown_as(catalog, variety)
            row["ripe"] = ripe

            #: Knowledge turns guesswork into a solved problem (D-057). With
            #: agrotech norms and the remainder to them are visible; without it
            #: only symptoms, common to all crops, and what to do about them the
            #: farmer finds out by experience, by buying knowledge, or by stubbornness.
            knows = await breed.knows_agrotech(session, identity_id, variety)
            row["agrotech"] = knows
            if knows:
                row["ripe_at"] = ready.isoformat()
                #: The start of the term, so the client can draw the deadline
                #: bar's share and not only the countdown (D-225: the client
                #: cannot derive when the bed was sown).
                row["sown_at"] = plot.sown_at.isoformat()
                row["asks_care"] = needs_care
                row["missed_days"] = skipped
                row["cycle_days"] = cycle
                row["fertility_required"] = fertility_needed
                #: Only where it is actually carried (D-126). By a river the
                #: round takes water from the river, and telling the farmer to
                #: bring seventy-five of it was the window asking for work the
                #: engine does not ask for -- the same number `care` refuses by
                #: when there is no river, said where there is one.
                if not world.has_place(node, world.WATER):
                    row["water_need"] = water_need(constants, plant, node, float(plot.area_m2))
            else:
                #: The engine names the sign, the client picks the word: a
                #: symptom is what is seen, not what is computed.
                symptoms: list[str] = []
                if needs_care:
                    symptoms.append("thirst")
                if float(plot.fertility) < fertility_needed:
                    symptoms.append("pale")
                if skipped > 0:
                    symptoms.append("stunted")
                if ripe:
                    symptoms.append("ripe")
                row["symptoms"] = symptoms
        out.append(row)
    return out
