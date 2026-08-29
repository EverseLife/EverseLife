# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Farming by plots (D-118, D-105).

Checked is what the system is built this way for:

* land is finite: the sum of plots is no more than the node's area;
* the cycle is honest: not ploughed -- cannot sow, not ripe -- cannot harvest;
* neglect cuts the harvest by `farm.neglect_penalty` per day but does not zero it;
* monoculture depletes, beans restore, fallow heals over time;
* redrawing borders does not heal the land: inheritance on split and merge;
* by a river one waters from the river, in a dry place water is carried by hand.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import farm, jobs, world
from src.models.estate import Building
from src.models.farm import Plot, PlotState
from src.models.inventory import Item
from src.units import amount_float

SPELT = "spelt"
BEANS = "beans"


async def _farmstead(
    session: AsyncSession, *, water: str = "река", fertility: float = 55, area: float = 200
):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session,
        f"terra.farm.{stamp}",
        "Хутор",
        area_m2=area,
        properties={"вода": water, "плодородие": fertility},
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
    """Area x derived yield x fertility x care -- and nothing beyond."""
    _, _, body = await _farmstead(session, fertility=55)
    plant = catalog.plants.by_id(SPELT)
    plot = await _ready(session, constants, catalog, body, area=10)

    #: Full care: we do the round every day of the cycle.
    sown = plot.sown_at
    for day_ in range(int(plant.cycle_days)):
        await farm.care(session, constants, body, plot, now=sown + _day(constants) * day_)

    ripeness = farm.ripe_at(constants, plot, plant)
    collected = await farm.harvest(session, constants, catalog, body, plot, now=ripeness)
    await session.commit()

    expected = 10 * plant.yield_per_m2 * (55 / plant.requires.fertility)
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
    assert 55.0 in qualities, f"среди стопок нет урожая: {qualities}"


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

    share = 1 - constants[R.FARM_NEGLECT_PENALTY] * plant.cycle_days / 100
    full = 10 * plant.yield_per_m2 * (55 / plant.requires.fertility)
    assert abandoned == pytest.approx(max(0.0, full * share), rel=0.01)


async def test_care_is_daily_not_hourly(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _farmstead(session)
    plot = await _ready(session, constants, catalog, body)
    await farm.care(session, constants, body, plot, now=plot.sown_at)
    with pytest.raises(farm.WrongState):
        await farm.care(session, constants, body, plot, now=plot.sown_at + timedelta(hours=1))


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

    #: First cycle: the crop changed (from "nothing"), no depletion.
    await farm.harvest(session, constants, catalog, body, plot, now=moment)
    assert float(plot.fertility) == pytest.approx(55)

    #: Second cycle of the same crop in a row -- depletion.
    plot.state = PlotState.PLOWED
    plot.idle_since = None
    await session.flush()
    more = await _grain(session, body, catalog, SPELT)
    await farm.sow(session, constants, catalog, body, plot, more, now=moment)
    moment = farm.ripe_at(constants, plot, plant)
    await farm.harvest(session, constants, catalog, body, plot, now=moment)
    assert float(plot.fertility) == pytest.approx(55 - constants[R.FARM_SOIL_DEPLETION])
    assert plot.same_culture_cycles == 2

    #: Beans return their `restores_fertility` from the data.
    beans = catalog.plants.by_id(BEANS)
    assert beans.restores_fertility > 0, "иначе севообороту не на чем держаться"
    plot.state = PlotState.PLOWED
    plot.idle_since = None
    await session.flush()
    bean_seeds = await _grain(session, body, catalog, BEANS)
    await farm.sow(session, constants, catalog, body, plot, bean_seeds, now=moment)
    moment = farm.ripe_at(constants, plot, beans)
    before = float(plot.fertility)
    await farm.harvest(session, constants, catalog, body, plot, now=moment)
    assert float(plot.fertility) == pytest.approx(before + beans.restores_fertility)
    assert plot.same_culture_cycles == 1


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
        properties={"плодородие": 40},
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
        properties={"плодородие": 40},
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
