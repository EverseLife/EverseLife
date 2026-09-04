# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The season's ends (D-296): sowing, the harvest with its seed return, and
the survey that tells the field's whole state without writing a thing. The
care between them lives in `care.py`, the clock that moves the bed in
`settle.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import breed, climate, events, food, world
from src.engine.farm import life
from src.engine.farm._base import (
    NoSeeds,
    WrongClimate,
    WrongState,
    _here,
    _owned,
    plow_minutes,
    plow_progress_minutes,
)
from src.engine.farm.settle import _clear, _signs, _sown, _weather, peek, settle
from src.models.event import EventKind
from src.models.farm import Plot, PlotState
from src.models.identity import Body
from src.models.inventory import Item
from src.models.plant import Variety
from src.models.world import Node
from src.units import (
    HARDINESS_SCALE,
    PERCENT,
    ROUND_QUALITY,
    SCALE_MAX,
    SCALE_MIN,
    amount,
    amount_float,
    on_grid,
)


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
    (`farm.sown_moisture`), full health, nought grown (D-296).
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
    plot.weeds = Decimal(0)
    plot.thinned = False
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
    cultivar strength** (D-296); land depletion and recovery are credited
    right here -- the harvest closes the cycle. Feedings repeated in a stage
    ran the crop to leaf and take their share off (`farm.overfeed_yield_penalty`);
    an unthinned stand pays its culture's crowd penalty, a thinned one the
    thinning's own cost (D-297).

    Seeds come back as a multiple of what was sown (`farm.seed_return`, D-257),
    scaled by the same soil, health, leaf, stand and lot-strength shares as
    the goods -- the health, the leaf and the stand are the "care share" of
    D-257, and a pulled seedling gives no seed: the fund
    reproduces by construction, and a sick or overfed bed, poor soil or a
    weak lot honestly sink the return below one. If the farmer did
    **selection** -- in-person work where mastery shows -- the fund keeps its
    strength; if not, the seeds degrade, and a hybrid additionally segregates
    (D-057, D-067).
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
    #: The stand (D-297): thinned, it paid its cost; crowded, it pays the
    #: culture's -- `density_risk` on the five-point scale of the traits.
    if plot.thinned:
        stand_share = 1 - constants[R.FARM_THIN_LOSS] / PERCENT
    else:
        risk = float(signs.get("density_risk", plant.traits.density_risk))
        stand_share = 1 - risk / HARDINESS_SCALE * constants[R.FARM_CROWD_PENALTY] / PERCENT
    stand_share = max(0.0, stand_share)
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
        * stand_share
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
        * stand_share
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
    thinned = plot.thinned
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
        thinned=thinned,
        fertility=float(plot.fertility),
    )
    return got


async def survey(
    session: AsyncSession, constants: Constants, catalog: Catalog, identity_id: uuid.UUID
) -> list[dict]:
    """Farm summary. Remote: readable from anywhere, care -- on foot.

    A growing bed is shown as of this very moment -- its life computed from
    the last stamp and written nowhere (D-296). Two words and one curve: the
    stage, the word of health, and the moisture with the pace it leaves at,
    so the client draws the curve forward without a timer of its own (D-226).
    Nothing derivable rides along (D-225): ripeness is the stage, and the
    band, the feeding table and the days to ripeness are not in the row --
    the first two are the Library's text, the last does not exist.
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
            row["health"] = life.health_word(constants, state.health)
            #: The point and the pace: the curve is the client's to draw. The
            #: pace is the one of this hour -- with the heat, the rain and the
            #: river in it, none of which the client is told (D-225).
            row["moisture"] = round(state.moisture, 1)
            row["moisture_at"] = now.isoformat()
            #: With the weeds' thirst in it (D-297): the engine dries the bed by
            #: it, and a curve drawn without it would show the ground wetter than
            #: it is, and the farmer would water later than the bed asks.
            row["dry_per_day"] = round(
                life.dry_rate(constants, norm, weather, weather.temperature_at(0.0))
                * life.weeds_thirst(constants, state.weeds)
                * PERCENT,
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
            #: Thinned is a fact of the sowing the client cannot derive (D-225):
            #: the button goes with it.
            if state.thinned:
                row["thinned"] = True
            #: The engine names the sign, the client picks the word (D-057).
            row["symptoms"] = life.symptoms(
                constants,
                norm,
                state,
                fertility=float(plot.fertility),
                fertility_needed=float(signs.get("fertility", plant.requires.fertility)),
                fed=given,
            )
        out.append(row)
    return out
