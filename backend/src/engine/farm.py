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

    yield = area * yield_per_m2 * (fertility / required) * care share
    care share = 1 - neglect_penalty * skipped days / 100  (not below zero)

`yield_per_m2` is not set by hand -- the vault derived it from `harvest.rates`
(D-136), and the engine takes it ready. Harvest quality is fertility taken by
the care share: tended land gives what is in it, neglected land gives worse.

**Depletion.** `farm.soil_depletion` for each cycle of **the same crop** in a
row: monoculture eats the land, rotation does not. A restoring crop returns
its `restores_fertility` from the data (beans), fallow recovers by
`farm.fallow_recovery` per idle day, credited by elapsed time on the next
action -- the land needs no tick, like sleep.

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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.constants.catalog import Plant
from src.engine import events, travel, world
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
WATER = "Вода"


class FarmError(Exception):
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
        raise WrongState("делянка не засеяна")
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
        raise TooSmall(
            f"меньше {constants[R.FARM_PLOT_MIN_AREA]} м² межевать бессмысленно"
        )

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise FarmError("тело вне узла")
    #: The plot's holder runs the estate: buy the land first (06-farming).
    #: Hiring is access plus a share by contract (D-116), not shared land.
    #:
    #: Land outside a city belongs to nobody and never will (D-198), and there
    #: the field is open: whoever ploughs it, farms it. The plot record still
    #: has an owner -- the crop is somebody's -- but the ground under it is not.
    nobody = node.owner_identity_id is None and node.owner_city_id is None
    if not nobody and node.owner_identity_id != body.identity_id:
        raise NotYours(
            "участок не ваш: городскую землю выкупают, а чужую — арендуют по договору"
        )

    taken = float(
        await session.scalar(
            select(func.coalesce(func.sum(Plot.area_m2), 0)).where(Plot.node_id == node.id)
        )
        or 0
    )
    if taken + area > float(node.area_m2):
        raise NoLand(
            f"в узле {node.key} свободно {float(node.area_m2) - taken:g} м², "
            f"просят {area:g}"
        )

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
        raise WrongState(f"делянка {plot.name!r} не под паром: {plot.state.value}")

    _accrue_fallow(constants, plot, moment)
    plot.state = PlotState.PLOWING
    plot.idle_since = None
    await session.flush()

    ready = moment + timedelta(
        minutes=constants[R.FARM_PLOW_TIME_PER_M2] * float(plot.area_m2)
    )
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
    plot = await session.get(Plot, uuid.UUID(job.payload["plot"]))
    if plot is None:  # pragma: no cover
        raise FarmError(f"задание {job.id}: делянки нет")
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
        raise WrongState(f"делянка {plot.name!r} не вспахана")

    from src.engine import breed

    variety = await breed._variety_of(session, seeds)  # noqa: SLF001
    plant = catalog.plants.by_id(variety.culture_id)
    if seeds.type_key != plant.seed:  # pragma: no cover -- cultivar and seed come from data
        raise NoSeeds(f"{seeds.type_key!r} — не семена культуры {plant.name!r}")

    pocket = await world.body_container(session, body)
    if seeds.container_id != pocket.id:
        raise NoSeeds("семена не в руках: сеют своим")

    need = amount(constants[R.FARM_SEED_RATE] * float(plot.area_m2))
    if seeds.amount < need:
        raise NoSeeds(
            f"нужно {amount_float(need):g} «{plant.seed}» на посев, "
            f"есть {amount_float(seeds.amount):g}"
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
        raise WrongState(f"на делянке {plot.name!r} ничего не растёт")

    day = timedelta(hours=day_hours(constants))
    if plot.cared_at is not None and moment - plot.cared_at < day:
        raise WrongState("сегодня уже ухожено: уход суточный, а не почасовой")

    node = await session.get(Node, plot.node_id)
    if node is None or node.properties.get("вода") != "река":
        need = amount(constants[R.FARM_WATER_PER_M2] * float(plot.area_m2))
        await _consume(session, body, WATER, need, why=NoWater(
            f"нужно {amount_float(need):g} воды: реки здесь нет, воду носят руками"
        ))

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

    Part of the harvest (`farm.harvest_seed_share`) stays for seeds. If the
    farmer did **selection** -- in-person work where mastery shows -- the fund
    keeps its strength; if not, the seeds degrade, and a hybrid additionally
    segregates (D-057, D-067).
    """
    moment = now or datetime.now(UTC)
    await _here(session, body)
    _owned(plot, body)
    if plot.state is not PlotState.SOWN or plot.culture_id is None:
        raise WrongState(f"на делянке {plot.name!r} нечего убирать")

    from src.engine import breed

    plant = catalog.plants.by_id(plot.culture_id)
    #: The cultivar decides the numbers: what was sown from one's own fund no
    #: longer has the crop's catalogue numbers. Old plots without a cultivar count as base.
    variety = (
        await session.get(Variety, plot.variety_id)
        if plot.variety_id is not None
        else None
    ) or await breed.landrace(session, catalog, plant.id)
    signs = variety.traits or breed.traits_of_plant(plant)
    cycle = float(signs.get("cycle_days", plant.cycle_days))
    strength = float(plot.seed_vigor) if plot.seed_vigor is not None else SCALE_MAX

    ready = (plot.sown_at or moment) + timedelta(hours=cycle * day_hours(constants))
    if moment < ready:
        raise WrongState(
            f"культура дозреет к {ready.isoformat()}: цикл {cycle:g} суток"
        )

    area = float(plot.area_m2)
    fertility = float(plot.fertility)
    #: Skipped care days cut the harvest but do not zero it.
    missed = max(0, int(cycle) - plot.care_credits)
    care_share = max(0.0, 1 - constants[R.FARM_NEGLECT_PENALTY] * missed / PERCENT)
    soil_share = fertility / float(signs.get("fertility", plant.requires.fertility))

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
        from src.engine import food

        session.add(
            Item(
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
        )

    #: Own seed: the harvest share kept for sowing, not for sale.
    seed_amount = got * constants[R.FARM_HARVEST_SEED_SHARE] / PERCENT
    if seed_amount > 0:
        seed_strength = breed.next_vigor(constants, variety, strength, selected=select_seed)
        if select_seed:
            await breed.select_generation(session, constants, variety)
        await breed.seed_lot(
            session, catalog, pocket.id, variety, seed_amount, seed_strength, now=moment
        )

    #: The land remembers what grew on it: monoculture eats it, rotation does not.
    depletion = (
        constants[R.FARM_SOIL_DEPLETION] if plot.last_culture == plant.id else 0.0
    )
    restored = plant.restores_fertility
    plot.fertility = Decimal(
        str(max(SCALE_MIN, min(SCALE_MAX, fertility - depletion + restored)))
    )
    plot.same_culture_cycles = (
        plot.same_culture_cycles + 1 if plot.last_culture == plant.id else 1
    )
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
        raise TooSmall("обе части обязаны быть не меньше farm.plot_min_area")

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
        raise FarmError("сливают соседние делянки, а не землю из разных узлов")

    _accrue_fallow(constants, one, moment)
    _accrue_fallow(constants, other, moment)

    a, b = float(one.area_m2), float(other.area_m2)
    one.area_m2 = Decimal(str(a + b))
    one.fertility = Decimal(
        str((float(one.fertility) * a + float(other.fertility) * b) / (a + b))
    )
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
            select(Plot, Node.name, Node.key)
            .join(Node, Node.id == Plot.node_id)
            .where(Plot.owner_identity_id == identity_id)
            .order_by(Plot.created_at)
        )
    ).all()

    out: list[dict] = []
    for plot, node_name, node_key in plots:
        row: dict = {
            "id": str(plot.id),
            "name": plot.name,
            "node": node_name,
            "node_key": node_key,
            "area": float(plot.area_m2),
            "state": plot.state.value,
            "fertility": float(plot.fertility),
            "culture": plot.culture_id,
        }
        if plot.state is PlotState.SOWN and plot.culture_id is not None and plot.sown_at:
            from src.engine import breed

            plant = catalog.plants.by_id(plot.culture_id)
            variety = (
                await session.get(Variety, plot.variety_id)
                if plot.variety_id is not None
                else None
            ) or await breed.landrace(session, catalog, plant.id)
            signs = variety.traits or breed.traits_of_plant(plant)
            cycle = float(signs.get("cycle_days", plant.cycle_days))
            fertility_needed = float(
                signs.get("fertility", plant.requires.fertility)
            )

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
                row["asks_care"] = needs_care
                row["missed_days"] = skipped
                row["cycle_days"] = cycle
                row["fertility_required"] = fertility_needed
                row["water_need"] = (
                    constants[R.FARM_WATER_PER_M2] * float(plot.area_m2)
                )
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


async def _here(session: AsyncSession, body: Body) -> None:
    if body.state is not BodyState.ALIVE:
        raise FarmError("мёртвое тело не работает")
    await travel.require_here(session, body)


def _owned(plot: Plot, body: Body) -> None:
    if plot.owner_identity_id != body.identity_id:
        raise NotYours("чужая делянка: аренда и наём — через договор")


def _recuttable(plot: Plot) -> None:
    if plot.state not in (PlotState.IDLE, PlotState.PLOWED):
        raise WrongState("перекроить можно только незасеянное")


def _ground_fertility(node: Node) -> float:
    """Starting fertility is a place property (D-126). No property -- it bears nothing."""
    raw = node.properties.get("плодородие", 0)
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
        (moment - plot.idle_since).total_seconds()
        / (day_hours(constants) * SECONDS_PER_HOUR),
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
    stacks = (
        (
            await session.execute(
                select(Item)
                .where(Item.container_id == pocket.id, Item.type_key == type_key)
                .order_by(Item.quality.asc().nulls_first())
            )
        )
        .scalars()
        .all()
    )
    have = sum(stack.amount for stack in stacks)
    if have < need:
        raise why
    left = need
    for stack in stacks:
        if left <= 0:
            break
        take = min(left, stack.amount)
        if take == stack.amount:
            await session.delete(stack)
        else:
            stack.amount -= take
        left -= take
    await session.flush()
