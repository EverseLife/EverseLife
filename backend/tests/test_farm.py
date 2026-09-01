# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Farming by plots (D-118, D-105).

Checked is what the system is built this way for:

* land is finite: the sum of plots is no more than the node's area;
* the cycle is honest: not ploughed -- cannot sow, not ripe -- cannot harvest;
* neglect cuts the harvest by its share of the cycle and can never zero it (D-263);
* rich land is an edge, not a multiplier: the soil share is capped (D-256);
* every harvest depletes, monoculture doubly; beans restore, fallow heals over time;
* redrawing borders does not heal the land: inheritance on split and merge;
* by a river one waters from the river, in a dry place water is carried by hand.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import farm, jobs, world
from src.models.estate import Building
from src.models.farm import Plot, PlotState
from src.models.inventory import Item
from src.models.world import Node
from src.units import amount_float

SPELT = "spelt"
BEANS = "beans"
#: The least demanding crop of the catalog: its fertility norm is 10, which is
#: exactly what made the uncapped soil share a tenfold multiplier (OQ-107).
BROME = "brome"


async def _farmstead(
    session: AsyncSession, *, water: str = "river", fertility: float = 55, area: float = 200
):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session,
        f"terra.farm.{stamp}",
        "Хутор",
        area_m2=area,
        properties={"water": water, "fertility": fertility},
    )
    identity = await world.create_identity(session, f"Фермер-{stamp}")
    body = await world.print_body(session, identity, node)
    #: The holder runs the estate: the fixture's farmer has already taken their plot.
    node.owner_identity_id = identity.id
    await session.flush()
    return node, identity, body


async def _grain(session: AsyncSession, body, cat: Catalog, culture: str, qty=200):
    """The base cultivar's seed fund: one sows with seeds, not harvest (D-057)."""
    from src.engine import breed
    from src.units import PERCENT

    cultivar = await breed.landrace(session, cat, culture)
    pocket = await world.body_container(session, body)
    return await breed.seed_lot(session, cat, pocket.id, cultivar, qty, PERCENT)


async def _ready(session, constants, catalog, body, *, area=10.0, culture=SPELT):
    """A plot brought to sowing, skipping the wait for ploughing."""
    plot = await farm.mark(session, constants, body, name="грядка", area=area)
    plot.state = PlotState.PLOWED
    await session.flush()
    seeds = await _grain(session, body, catalog, culture)
    return await farm.sow(session, constants, catalog, body, plot, seeds)


def _day(constants: Constants) -> timedelta:
    return timedelta(hours=constants[R.TIME_DAY_TERRA])


# --- land --------------------------------------------------------------------


async def test_node_land_is_finite(session: AsyncSession, constants: Constants) -> None:
    _, _, body = await _farmstead(session, area=20)
    await farm.mark(session, constants, body, name="первая", area=15)
    with pytest.raises(farm.NoLand):
        await farm.mark(session, constants, body, name="вторая", area=10)


async def test_the_house_takes_its_ground_from_the_strips(
    session: AsyncSession, constants: Constants
) -> None:
    """A bed is cut out of the yard, not out of the house (D-246).

    The check used to ask about the strips alone, so a plot with a house on
    half of it could still be cut into strips edge to edge -- and the empty
    land the foraging walks came out negative.
    """
    node, _, body = await _farmstead(session, area=100)
    session.add(Building(node_id=node.id, area_m2=160, footprint_m2=40, floors=4))
    await session.flush()

    await farm.mark(session, constants, body, name="первая", area=50)
    with pytest.raises(farm.NoLand):
        await farm.mark(session, constants, body, name="вторая", area=20)
    #: The storeys take nothing more: the ground is spent by the footprint (D-125).
    await farm.mark(session, constants, body, name="вторая", area=10)


async def test_no_survey_below_minimum(session: AsyncSession, constants: Constants) -> None:
    _, _, body = await _farmstead(session)
    with pytest.raises(farm.TooSmall):
        await farm.mark(
            session, constants, body, name="лоскут", area=constants[R.FARM_PLOT_MIN_AREA] - 1
        )


async def test_land_bears_nothing_without_fertility(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Fertility is a place property (D-126): no property -- no harvest."""
    _, _, body = await _farmstead(session, fertility=0)
    plot = await _ready(session, constants, catalog, body)
    plant = catalog.plants.by_id(SPELT)
    ripeness = farm.ripe_at(constants, plot, plant)
    collected = await farm.harvest(session, constants, catalog, body, plot, now=ripeness)
    assert collected == 0


# --- cycle -------------------------------------------------------------------


async def test_cycle_is_honest(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _farmstead(session)
    plot = await farm.mark(session, constants, body, name="грядка", area=10)
    seeds = await _grain(session, body, catalog, SPELT)

    with pytest.raises(farm.WrongState):
        await farm.sow(session, constants, catalog, body, plot, seeds)

    plot.state = PlotState.PLOWED
    await session.flush()
    await farm.sow(session, constants, catalog, body, plot, seeds)

    with pytest.raises(farm.WrongState):
        #: Not ripe -- cannot harvest.
        await farm.harvest(session, constants, catalog, body, plot)


async def test_ploughing_goes_by_job(
    factory: async_sessionmaker[AsyncSession], constants: Constants
) -> None:
    async with factory() as session, session.begin():
        _, _, body = await _farmstead(session)
        plot = await farm.plow(
            session,
            constants,
            body,
            await farm.mark(session, constants, body, name="грядка", area=10),
        )
        assert plot.state is PlotState.PLOWING
        plot_id = plot.id

    job = await jobs.run_one(factory, now=datetime.now(UTC) + timedelta(hours=1))
    assert job is not None and job.kind == "farm.plow"

    async with factory() as session:
        plot = await session.get(Plot, plot_id)
        assert plot.state is PlotState.PLOWED


async def test_sowing_spends_seeds(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Seeds are an item with their own sowing norm per metre (D-057)."""
    _, _, body = await _farmstead(session)
    plant = catalog.plants.by_id(SPELT)
    plot = await farm.mark(session, constants, body, name="грядка", area=10)
    plot.state = PlotState.PLOWED
    await session.flush()

    little = await _grain(session, body, catalog, SPELT, qty=1)
    with pytest.raises(farm.NoSeeds):
        await farm.sow(session, constants, catalog, body, plot, little)

    seeds = await _grain(session, body, catalog, SPELT, qty=100)
    await farm.sow(session, constants, catalog, body, plot, seeds)

    pocket = await world.body_container(session, body)
    left = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == pocket.id, Item.type_key == plant.seed
        )
    )
    #: The sack from the first attempt stayed untouched: the batch does not
    #: start if seeds are short.
    assert amount_float(int(left)) == pytest.approx(101 - constants[R.FARM_SEED_RATE] * 10)


async def test_harvest_from_vault_formula(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Area x derived yield x fertility x care -- and nothing beyond.

    Fertility sits below the crop's norm on purpose: this test pins the
    proportional branch of the soil share, the cap has its own test below.
    """
    _, _, body = await _farmstead(session, fertility=40)
    plant = catalog.plants.by_id(SPELT)
    assert 40 / plant.requires.fertility < constants[R.FARM_SOIL_SHARE_CAP] / 100, (
        "тесту нужна доля ниже потолка, иначе пропорциональность не проверена"
    )
    plot = await _ready(session, constants, catalog, body, area=10)

    #: Full care: we do the round every day of the cycle.
    sown = plot.sown_at
    for day_ in range(int(plant.cycle_days)):
        await farm.care(session, constants, body, plot, now=sown + _day(constants) * day_)

    ripeness = farm.ripe_at(constants, plot, plant)
    collected = await farm.harvest(session, constants, catalog, body, plot, now=ripeness)
    await session.commit()

    expected = 10 * plant.yield_per_m2 * (40 / plant.requires.fertility)
    assert collected == pytest.approx(expected, rel=0.01)

    #: The collected stack is not a seed sack: we search by harvest quality,
    #: and it equals fertility taken by full care.
    pocket = await world.body_container(session, body)
    stacks = (
        (
            await session.execute(
                select(Item).where(Item.container_id == pocket.id, Item.type_key == plant.gives)
            )
        )
        .scalars()
        .all()
    )
    qualities = {None if s.quality is None else float(s.quality) for s in stacks}
    assert 40.0 in qualities, f"среди стопок нет урожая: {qualities}"


async def test_neglect_cuts_but_does_not_zero(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A holiday is not punished: a share, not zero (D-118)."""
    _, _, body = await _farmstead(session)
    plant = catalog.plants.by_id(SPELT)
    plot = await _ready(session, constants, catalog, body, area=10)
    ripeness = farm.ripe_at(constants, plot, plant)

    #: Not a single round for the whole cycle.
    abandoned = await farm.harvest(session, constants, catalog, body, plot, now=ripeness)

    #: A miss costs its share of the cycle (D-263) softened by hardiness
    #: (D-261): a full walk-out leaves a share for any crop, never zero.
    forgiven = 1 - constants[R.FARM_HARDINESS_RELIEF] / 100 * plant.traits.hardiness / 5
    share = (
        1 - constants[R.FARM_NEGLECT_TOTAL] * forgiven * plant.cycle_days / plant.cycle_days / 100
    )
    soil = min(55 / plant.requires.fertility, constants[R.FARM_SOIL_SHARE_CAP] / 100)
    full = 10 * plant.yield_per_m2 * soil
    assert share > 0, "полный прогул оставляет долю, а не ноль (D-263)"
    assert abandoned == pytest.approx(max(0.0, full * share), rel=0.01)


async def test_care_goes_by_the_planets_calendar_day(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One day -- one round, at any hour of it (D-263).

    The old rule was a 38-hour interval, and the care hour drifted by
    fourteen every day: two rounds two hours apart across a day boundary
    were forbidden, which is exactly what a player with an Earth rhythm did.
    """
    _, _, body = await _farmstead(session)
    plot = await _ready(session, constants, catalog, body)
    #: The farmstead node is the world's first, so its planetary day starts
    #: at the epoch and the boundaries sit at whole day lengths from sowing.
    late = plot.sown_at + timedelta(hours=constants[R.TIME_DAY_TERRA] - 1)
    await farm.care(session, constants, body, plot, now=late)
    #: Two hours later -- but a new planetary day: allowed, no drift.
    await farm.care(session, constants, body, plot, now=late + timedelta(hours=2))
    with pytest.raises(farm.WrongState):
        #: The same day's second round is what stays forbidden.
        await farm.care(session, constants, body, plot, now=late + timedelta(hours=3))
    with pytest.raises(farm.WrongState):
        #: A moment handed from the past does not mint a credit either: the
        #: guard compares day numbers with <=, not equality.
        await farm.care(session, constants, body, plot, now=late)


async def test_water_carried_by_hand_in_dry_place(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """By a river -- from the river; otherwise water is a commodity (D-126)."""
    _, _, body = await _farmstead(session, water="нет")
    plot = await _ready(session, constants, catalog, body, area=10)

    with pytest.raises(farm.NoWater):
        await farm.care(session, constants, body, plot, now=plot.sown_at)

    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, farm.WATER, amount=100, origin="тест")
    await farm.care(session, constants, body, plot, now=plot.sown_at)

    left = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == pocket.id, Item.type_key == farm.WATER
        )
    )
    assert amount_float(int(left)) == pytest.approx(100 - constants[R.FARM_WATER_PER_M2] * 10)


# --- the land remembers ------------------------------------------------------


async def test_monoculture_depletes_and_beans_restore(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _farmstead(session, fertility=55)
    plant = catalog.plants.by_id(SPELT)
    plot = await _ready(session, constants, catalog, body, area=10)
    moment = farm.ripe_at(constants, plot, plant)

    #: First cycle takes too: every harvest costs the land (D-256), otherwise
    #: alternating two crops was a perpetual motion machine.
    await farm.harvest(session, constants, catalog, body, plot, now=moment)
    assert float(plot.fertility) == pytest.approx(55 - constants[R.FARM_SOIL_DEPLETION])

    #: Second cycle of the same crop in a row -- the monoculture extra on top.
    plot.state = PlotState.PLOWED
    plot.idle_since = None
    await session.flush()
    more = await _grain(session, body, catalog, SPELT)
    await farm.sow(session, constants, catalog, body, plot, more, now=moment)
    moment = farm.ripe_at(constants, plot, plant)
    before = float(plot.fertility)
    await farm.harvest(session, constants, catalog, body, plot, now=moment)
    assert float(plot.fertility) == pytest.approx(
        before - constants[R.FARM_SOIL_DEPLETION] - constants[R.FARM_MONOCULTURE_PENALTY]
    )
    assert plot.same_culture_cycles == 2

    #: Beans return their `restores_fertility` from the data -- net of the
    #: depletion every harvest pays, so rotation costs something too.
    beans = catalog.plants.by_id(BEANS)
    assert beans.restores_fertility > constants[R.FARM_SOIL_DEPLETION], (
        "иначе севообороту не на чем держаться"
    )
    plot.state = PlotState.PLOWED
    plot.idle_since = None
    await session.flush()
    bean_seeds = await _grain(session, body, catalog, BEANS)
    await farm.sow(session, constants, catalog, body, plot, bean_seeds, now=moment)
    moment = farm.ripe_at(constants, plot, beans)
    before = float(plot.fertility)
    await farm.harvest(session, constants, catalog, body, plot, now=moment)
    assert float(plot.fertility) == pytest.approx(
        before + beans.restores_fertility - constants[R.FARM_SOIL_DEPLETION]
    )
    assert plot.same_culture_cycles == 1


async def test_rich_land_is_an_edge_not_a_multiplier(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The soil share is capped (D-256).

    Before the cap the least demanding crop on the best land beat everything
    tenfold: the playtest reaped 189.5 of hay against the nominal 18.95 (OQ-107).
    """
    _, _, body = await _farmstead(session, fertility=100)
    plant = catalog.plants.by_id(BROME)
    assert 100 / plant.requires.fertility > constants[R.FARM_SOIL_SHARE_CAP] / 100, (
        "тесту нужен запас плодородия над нормой, иначе потолок не виден"
    )
    plot = await _ready(session, constants, catalog, body, culture=BROME)

    sown = plot.sown_at
    for day_ in range(int(plant.cycle_days)):
        await farm.care(session, constants, body, plot, now=sown + _day(constants) * day_)

    ripeness = farm.ripe_at(constants, plot, plant)
    collected = await farm.harvest(session, constants, catalog, body, plot, now=ripeness)
    expected = 10 * plant.yield_per_m2 * constants[R.FARM_SOIL_SHARE_CAP] / 100
    assert collected == pytest.approx(expected, rel=0.01)


async def test_climate_gates_the_sowing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The place refuses the culture (D-261): too cold, too hot, too dark.

    The whole daily band must fit -- the crop lives through the night too --
    and a node without a temperature record carries no gate at all (the
    fixtures above farm exactly such nodes).
    """
    plant = catalog.plants.by_id(SPELT)
    swing = constants[R.PLANET_TEMP_SWING]["terra"]

    _, _, cold = await _farmstead(session, fertility=55)
    plot = await farm.mark(session, constants, cold, name="мерзлота", area=10)
    plot.state = PlotState.PLOWED
    node = await session.get(Node, plot.node_id)
    node.properties = {**node.properties, "temperature": plant.requires.temp["min"] - 1}
    await session.flush()
    seeds = await _grain(session, cold, catalog, SPELT)
    with pytest.raises(farm.WrongClimate):
        await farm.sow(session, constants, catalog, cold, plot, seeds)

    #: The same mean would pass a gate on the mean alone: the band does not fit.
    node.properties = {**node.properties, "temperature": plant.requires.temp["max"] - swing + 1}
    await session.flush()
    with pytest.raises(farm.WrongClimate):
        await farm.sow(session, constants, catalog, cold, plot, seeds)

    #: A light-hungry culture refuses the woods; spelt puts up with them.
    sunny = catalog.plants.by_id("camelina")
    assert sunny.requires.light > plant.requires.light, "тесту нужна светолюбивая культура"
    _, _, shaded = await _farmstead(session, fertility=55)
    strip = await farm.mark(session, constants, shaded, name="под пологом", area=10)
    strip.state = PlotState.PLOWED
    grove = await session.get(Node, strip.node_id)
    grove.properties = {**grove.properties, "temperature": 18, "woods": True}
    await session.flush()
    sunny_seeds = await _grain(session, shaded, catalog, "camelina")
    with pytest.raises(farm.WrongClimate):
        await farm.sow(session, constants, catalog, shaded, strip, sunny_seeds)
    spelt_seeds = await _grain(session, shaded, catalog, SPELT)
    await farm.sow(session, constants, catalog, shaded, strip, spelt_seeds)
    assert strip.state is PlotState.SOWN


async def test_thirst_and_rain_shape_the_watering(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Water is the norm by area, the culture's thirst, minus rain (D-261)."""
    _, _, body = await _farmstead(session, water="нет")
    plot = await _ready(session, constants, catalog, body, area=10)
    node = await session.get(Node, plot.node_id)
    node.properties = {**node.properties, "precipitation": 100}
    await session.flush()

    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, farm.WATER, amount=100, origin="тест")
    await farm.care(session, constants, body, plot, now=plot.sown_at)

    plant = catalog.plants.by_id(SPELT)
    thirst = constants[R.FARM_WATER_BY_NEED][str(int(plant.requires.water))]
    covered = constants[R.SITE_RAIN_WATER_OFFSET] / 100
    need = constants[R.FARM_WATER_PER_M2] * 10 * thirst * (1 - covered)
    left = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == pocket.id, Item.type_key == farm.WATER
        )
    )
    assert amount_float(int(left)) == pytest.approx(100 - need)


async def test_fertilizer_feeds_the_land_not_the_bed(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Fertilizer goes into fallow or plowed ground (D-264, closes OQ-108).

    One dose for either kind, two strengths -- and the mineral one gives
    most of all, as the vault's table promises. A growing bed refuses:
    feeding it is one of the five care decisions and waits for OQ-098.
    """
    moment = datetime.now(UTC)
    _, _, body = await _farmstead(session, fertility=40)
    plot = await farm.mark(session, constants, body, name="тощая", area=10, now=moment)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "compost", amount=20, origin="тест")
    await world.grant_item(session, pocket, "mineral_fertilizer", amount=20, origin="тест")

    with pytest.raises(farm.FarmError):
        await farm.fertilize(session, constants, body, plot, "grain", now=moment)

    await farm.fertilize(session, constants, body, plot, "compost", now=moment)
    assert float(plot.fertility) == pytest.approx(40 + constants[R.FARM_COMPOST_RECOVERY])
    await farm.fertilize(session, constants, body, plot, "mineral_fertilizer", now=moment)
    assert float(plot.fertility) == pytest.approx(
        40 + constants[R.FARM_COMPOST_RECOVERY] + constants[R.FARM_MINERAL_RECOVERY]
    )
    assert constants[R.FARM_MINERAL_RECOVERY] > constants[R.FARM_COMPOST_RECOVERY], (
        "минеральное обязано давать больше всех"
    )

    #: The dose went by area, once per kind.
    left = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == pocket.id, Item.type_key == "compost"
        )
    )
    assert amount_float(int(left)) == pytest.approx(20 - constants[R.FARM_FERTILIZER_PER_M2] * 10)

    #: Sated land refuses: the ceiling is the scale's, not the purse's.
    plot.fertility = Decimal("100")
    await session.flush()
    with pytest.raises(farm.WrongState):
        await farm.fertilize(session, constants, body, plot, "compost", now=moment)

    #: A growing bed is not the land: the strip refuses whole.
    sown = await _ready(session, constants, catalog, body, area=10)
    with pytest.raises(farm.WrongState):
        await farm.fertilize(session, constants, body, sown, "compost", now=moment)

    #: An empty pocket refuses before anything changes.
    bare = await farm.mark(session, constants, body, name="без запаса", area=10, now=moment)
    stacks = (
        (
            await session.execute(
                select(Item).where(
                    Item.container_id == pocket.id, Item.type_key == "mineral_fertilizer"
                )
            )
        )
        .scalars()
        .all()
    )
    for stack in stacks:
        await session.delete(stack)
    await session.flush()
    before = float(bare.fertility)
    with pytest.raises(farm.FarmError):
        await farm.fertilize(session, constants, body, bare, "mineral_fertilizer", now=moment)
    assert float(bare.fertility) == pytest.approx(before)


async def test_fallow_heals_over_time(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Credited by elapsed idle time: the land needs no tick, like sleep."""
    _, _, body = await _farmstead(session, fertility=30)
    plot = await farm.mark(session, constants, body, name="пар", area=10)
    plot.fertility = 30
    await session.flush()

    two_days = datetime.now(UTC) + _day(constants) * 2
    await farm.plow(session, constants, body, plot, now=two_days)
    assert float(plot.fertility) == pytest.approx(
        30 + constants[R.FARM_FALLOW_RECOVERY] * 2, rel=0.01
    )


async def test_resurvey_does_not_heal_land(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A split inherits as is, a merge -- weighted and with the heavy history."""
    _, _, body = await _farmstead(session, area=200, fertility=50)
    plot = await farm.mark(session, constants, body, name="целое", area=100)
    plot.fertility = 20
    plot.last_culture = SPELT
    plot.same_culture_cycles = 3
    plot.idle_since = None
    await session.flush()

    piece = await farm.split(session, constants, body, plot, 40, name="отрез")
    assert float(piece.fertility) == pytest.approx(20), "деление не сбрасывает истощение"
    assert piece.same_culture_cycles == 3
    assert float(plot.area_m2) == pytest.approx(60)

    #: A fresh plot + a depleted one: the merge weighs, the history is the heavy one.
    piece.fertility = 80
    piece.last_culture = None
    piece.same_culture_cycles = 0
    piece.idle_since = None
    await session.flush()
    whole = await farm.merge(session, constants, body, plot, piece)
    assert float(whole.area_m2) == pytest.approx(100)
    assert float(whole.fertility) == pytest.approx((20 * 60 + 80 * 40) / 100)
    assert whole.last_culture == SPELT and whole.same_culture_cycles == 3


async def test_sown_land_not_resurveyed(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _farmstead(session)
    plot = await _ready(session, constants, catalog, body, area=20)
    with pytest.raises(farm.WrongState):
        await farm.split(session, constants, body, plot, 10, name="кусок")


async def test_foreign_patch_left_alone(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Hiring is access plus a share, by contract (D-116), not by a button."""
    node, _, owner = await _farmstead(session)
    plot = await farm.mark(session, constants, owner, name="своя", area=10)

    guest = await world.create_identity(session, f"Гость-{uuid.uuid4().hex[:6]}")
    guest_body = await world.print_body(session, guest, node)
    with pytest.raises(farm.NotYours):
        await farm.plow(session, constants, guest_body, plot)


async def test_summary_counts_losses_on_accrual_day(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """ "Minus half the harvest" is seen at once, not as a surprise at harvest.

    In numbers -- to whoever knows the agrotech: without it the same plot
    shows a symptom, not a loss count (D-057, checked in `test_agrotech`).
    """
    from src.models.identity import KnowledgeKind

    _, identity, body = await _farmstead(session)
    await world.learn(session, identity, SPELT, kind=KnowledgeKind.AGROTECH)
    plot = await _ready(session, constants, catalog, body)
    plot.sown_at = datetime.now(UTC) - _day(constants) * 2 - timedelta(hours=1)
    await session.flush()

    summary = await farm.survey(session, constants, catalog, identity.id)
    assert len(summary) == 1
    assert summary[0]["missed_days"] == 2
    assert summary[0]["asks_care"] is True


async def test_foreign_plot_not_surveyed(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The holder runs the estate: take the land first (06-farming)."""
    node, _, _ = await _farmstead(session)
    guest = await world.create_identity(session, f"Гость-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, guest, node)
    with pytest.raises(farm.NotYours):
        await farm.mark(session, constants, body, name="самозахват", area=10)


async def test_land_outside_a_city_is_never_privatized(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Nobody's land stays nobody's, and everybody farms it (D-198).

    It used to be taken on foot, and the first comer locked up a whole grove
    together with the barehand gathering on it (D-196).
    """
    stamp = uuid.uuid4().hex[:8]
    wild = await world.create_node(
        session,
        f"terra.wild.{stamp}",
        "Дикий угол",
        area_m2=100,
        properties={"fertility": 40},
    )
    first = await world.create_identity(session, f"Первый-{stamp}")
    body = await world.print_body(session, first, wild)

    with pytest.raises(world.LandError):
        await world.grant_node(session, wild, first)
    assert wild.owner_identity_id is None

    #: And yet the field is open: whoever ploughs it, farms it.
    await farm.mark(session, constants, body, name="своя", area=10)

    second = await world.create_identity(session, f"Второй-{stamp}")
    body2 = await world.print_body(session, second, wild)
    await farm.mark(session, constants, body2, name="соседняя", area=10)


async def test_civic_plot_is_handed_over_once(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Title is issued by a city, and a plot already held is not issued again."""
    stamp = uuid.uuid4().hex[:8]
    civic = await world.create_node(
        session,
        f"terra.town.{stamp}",
        "Городская земля",
        area_m2=100,
        properties={"fertility": 40},
    )
    civic.owner_city_id = uuid.uuid4()
    holder = await world.create_identity(session, f"Держатель-{stamp}")
    body = await world.print_body(session, holder, civic)

    await world.grant_node(session, civic, holder)
    assert civic.owner_identity_id == holder.id
    await farm.mark(session, constants, body, name="своя", area=10)

    other = await world.create_identity(session, f"Другой-{stamp}")
    other_body = await world.print_body(session, other, civic)
    with pytest.raises(world.LandError):
        await world.grant_node(session, civic, other)
    with pytest.raises(farm.NotYours):
        await farm.mark(session, constants, other_body, name="чужая", area=10)


async def test_a_riverside_bed_is_not_asked_to_carry_water(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """By a river the round takes water from the river (D-126), so the window
    must not name a load of it. On dry ground the same window must."""
    from src.models.identity import KnowledgeKind

    wet, identity, body = await _farmstead(session, water="river")
    await world.learn(session, identity, SPELT, kind=KnowledgeKind.AGROTECH)
    await _ready(session, constants, catalog, body, area=20)

    dry, _, _ = await _farmstead(session, water="none")
    dry.owner_identity_id = identity.id
    body.node_id = dry.id
    await session.flush()
    await _ready(session, constants, catalog, body, area=20)

    rows = {
        row["node_key"]: row for row in await farm.survey(session, constants, catalog, identity.id)
    }
    assert "water_need" not in rows[wet.key], "у реки воду не носят"
    assert rows[dry.key]["water_need"] > 0, "в сухом месте носят, и сколько — надо сказать"


async def test_the_split_field_can_be_sewn_back(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`farm.merge` is reachable from the socket, and refuses a plot with itself.

    The engine could merge from the day plots were cut, but no command led to
    it: a strip could be split and never sewn back, and the anti-exploit that
    makes merging honest (fertility by area, history by the heavier half,
    D-118) guarded a door nobody could open.
    """
    from src.api import commands as _registered  # noqa: F401 -- registers the command
    from src.api.registry import COMMANDS, Refused

    _, identity, body = await _farmstead(session)
    plot = await farm.mark(session, constants, body, name="поле", area=100)
    piece = await farm.split(session, constants, body, plot, 40, name="отрез")
    await session.flush()

    state = {"identity_id": identity.id}
    with pytest.raises(Refused):
        await COMMANDS["farm.merge"].run(
            state, session, {"plot": str(plot.id), "other": str(plot.id)}
        )

    answer = await COMMANDS["farm.merge"].run(
        state, session, {"plot": str(plot.id), "other": str(piece.id)}
    )
    assert answer["plot"] == str(plot.id)
    assert answer["area"] == pytest.approx(100)
    assert await session.get(Plot, piece.id) is None, "сведённая половина перестаёт быть"
