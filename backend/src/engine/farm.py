# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Farming by plots (D-118, D-105, D-057).

Cultivars, seeds and crossing live next door in `engine/breed.py`: here is the
land and the cycle, there is what grows on it.

The economy's second pedal: mining is limited by the player's attention,
farming by land and time. The plot cycle: ploughing -> sowing -> care ->
growth -> harvest -> fallow or the next crop. Growth runs offline, care only
on foot: fully offline farming does not go, otherwise it is a printing press
(D-118).

## Where each formula came from

Numbers come from `farm.*` and `build/plants.json`, the order of steps is the
engine's business.

**A day.** All farming terms are given "in days", and a day here is planetary:
`time.day_terra` hours (D-008). Terra has no other day length.

**Care.** Once a day, for the whole plot. The round time is a vault formula:
`farm.plot_overhead + farm.care_time_per_m2 * area`; water is
`farm.water_per_m2 * area`, and by a river it is taken from the river, while
in a dry place it is carried as an item. Skipped days do not zero the harvest
but cut it by `farm.neglect_penalty` each: a holiday is not punished, but
neglect shows.

**Harvest.** "Proportional to area, fertility and care quality":

    yield = area * yield_per_m2 * soil share * care share
    soil share = min(fertility / required, farm.soil_share_cap)  (D-256)
    care share = 1 - neglect_penalty * skipped days / 100  (not below zero)

The soil share is capped: rich land is an edge, not a multiplier, otherwise
the degenerate optimum is the least demanding crop on the best land (OQ-107).

`yield_per_m2` is not set by hand -- the vault derived it from `harvest.rates`
(D-136), and the engine takes it ready. Harvest quality is fertility taken by
the care share: tended land gives what is in it, neglected land gives worse.

**Depletion.** `farm.soil_depletion` for **every** harvest, whatever the crop;
a repeat of the same crop in a row adds `farm.monoculture_penalty` on top
(D-256): monoculture eats the land twice as fast, but rotation is not free
either -- otherwise alternating two crops was a perpetual motion machine.
A restoring crop returns its `restores_fertility` from the data (beans),
fallow recovers by `farm.fallow_recovery` per idle day, credited by elapsed
time on the next action -- the land needs no tick, like sleep.

## Honest simplifications of this version

* **By-product** (straw for spelt) is not given: the share is not set by data,
  and inventing it here is not allowed (D-065);
* **Diseases and the five care parameters** (OQ-098) are reduced to one daily
  round: what the player answers with during care is an open question of the
  pilot screen, and until it closes the round is binary.
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
from src.engine import breed, estate, events, food, liquid, occupation, stock, travel, world
from src.engine.errors import Refusal, left_to_say
from src.engine.jobs import enqueue, handler
from src.models.event import EventKind
from src.models.farm import Plot, PlotState
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.job import Job, JobKind
from src.models.plant import Variety
from src.models.world import Node
from src.units import PERCENT, SCALE_MAX, SCALE_MIN, SECONDS_PER_HOUR, amount, amount_float

#: The name of water in `build/recipes.json` -- carried by hand where there is no river.
WATER = "water"


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


class NoWater(FarmError):
    """In a dry place water is carried by hand (D-126)."""


class TooSmall(FarmError):
    """Surveying less than `farm.plot_min_area` is pointless."""


def day_hours(constants: Constants) -> float:
    """Terra's day. All farming terms are given in it (D-008)."""
    return constants[R.TIME_DAY_TERRA]


def ripe_at(constants: Constants, plot: Plot, plant: Plant) -> datetime:
    if plot.sown_at is None:  # pragma: no cover
        raise WrongState(key="farm-plot-not-sown")
    return plot.sown_at + timedelta(hours=plant.cycle_days * day_hours(constants))


def care_minutes(constants: Constants, area: float) -> float:
    """Round time: a vault formula. Land scales, hands do not."""
    return constants[R.FARM_PLOT_OVERHEAD] + constants[R.FARM_CARE_TIME_PER_M2] * area


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
    """Plough. Long-running: started in person, goes by itself."""
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(plot, body)
    if plot.state is not PlotState.IDLE:
        raise WrongState(key="farm-not-fallow", plot=plot.name, state=plot.state.value)

    _accrue_fallow(constants, plot, moment)
    plot.state = PlotState.PLOWING
    plot.idle_since = None
    await session.flush()

    ready = moment + timedelta(minutes=constants[R.FARM_PLOW_TIME_PER_M2] * float(plot.area_m2))
    event = await events.record(
        session,
        EventKind.PLOT_PLOWED,
        actor_identity_id=body.identity_id,
        node_id=plot.node_id,
        plot_id=str(plot.id),
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


@handler(JobKind.FARM_PLOW)
async def plow_done(session: AsyncSession, job: Job) -> None:
    #: Under the same lock the commands take (`api.commands.farm._plot`):
    #: the state write below must not race a split or a merge of the strip.
    plot = await session.get(
        Plot, uuid.UUID(job.payload["plot"]), with_for_update=True, populate_existing=True
    )
    if plot is None:  # pragma: no cover
        raise FarmError(key="farm-job-no-plot", job=str(job.id))
    if plot.state is not PlotState.PLOWING:
        #: A job retry after a failure does not double the ploughing.
        return
    plot.state = PlotState.PLOWED
    await session.flush()


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
    """Do the plot round: once a day, on foot, with water.

    By a river water is taken from the river; in a dry place from the
    inventory, and that makes water a commodity where there is none (D-126).
    """
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(plot, body)
    if plot.state is not PlotState.SOWN or plot.sown_at is None:
        raise WrongState(key="farm-nothing-grows", plot=plot.name)

    day = timedelta(hours=day_hours(constants))
    if plot.cared_at is not None and moment - plot.cared_at < day:
        raise WrongState(key="farm-cared-today")

    node = await session.get(Node, plot.node_id)
    if not world.has_place(node, world.WATER):
        need = amount(constants[R.FARM_WATER_PER_M2] * float(plot.area_m2))
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
    #: Skipped care days cut the harvest but do not zero it.
    missed = max(0, int(cycle) - plot.care_credits)
    care_share = max(0.0, 1 - constants[R.FARM_NEGLECT_PENALTY] * missed / PERCENT)
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
    one.fertility = Decimal(str((float(one.fertility) * a + float(other.fertility) * b) / (a + b)))
    heavier = max((one, other), key=lambda p: p.same_culture_cycles)
    one.last_culture = heavier.last_culture
    one.same_culture_cycles = heavier.same_culture_cycles
    #: Resurveyed land is ploughed anew.
    one.state = PlotState.IDLE
    one.idle_since = moment

    await session.delete(other)
    await session.flush()
    return one


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
            day = timedelta(hours=day_hours(constants))
            needs_care = plot.cared_at is None or now - plot.cared_at >= day
            #: Losses accrue on the day they accrue, not as a surprise at
            #: harvest (D-118).
            elapsed = (now - plot.sown_at).total_seconds() / (
                day_hours(constants) * SECONDS_PER_HOUR
            )
            skipped = max(0, min(int(cycle), int(elapsed)) - plot.care_credits)
            ripe = now >= ready

            row["culture_name"] = plant.name
            row["variety"] = variety.name or f"гибрид, поколение {variety.generation}"
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
                    row["water_need"] = constants[R.FARM_WATER_PER_M2] * float(plot.area_m2)
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
        raise WrongState(key="farm-recut-sown")


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
    healed = constants[R.FARM_FALLOW_RECOVERY] * days
    plot.fertility = Decimal(str(min(SCALE_MAX, float(plot.fertility) + healed)))
    plot.idle_since = moment


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
