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
from sqlalchemy.ext.asyncio import AsyncSession

from farm_kit import BEANS, BROME, SPELT, _farmstead, _stand
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import farm, world
from src.engine.farm._base import _accrue_fallow
from src.models.estate import Building
from src.models.farm import Plot, PlotState
from src.models.inventory import Item
from src.models.world import Node
from src.units import ROUND_QUALITY, amount_float


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


def _grown(plot: Plot, health: float = 100) -> None:
    """The bed brought to ripeness by the test's hands: the life itself is
    checked in `test_farm_life` (D-296)."""
    plot.growth = Decimal(100)
    plot.health = Decimal(str(health))


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
    _grown(plot)
    await session.flush()
    collected = await farm.harvest(session, constants, catalog, body, plot, now=plot.settled_at)
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
    """Area x derived yield x fertility x health -- and nothing beyond.

    Fertility sits below the crop's norm on purpose: this test pins the
    proportional branch of the soil share, the cap has its own test below.
    """
    _, _, body = await _farmstead(session, fertility=40)
    plant = catalog.plants.by_id(SPELT)
    assert 40 / plant.requires.fertility < constants[R.FARM_SOIL_SHARE_CAP] / 100, (
        "тесту нужна доля ниже потолка, иначе пропорциональность не проверена"
    )
    plot = await _ready(session, constants, catalog, body, area=10)

    #: Full health: the bed is brought to ripeness by the test's hands.
    _grown(plot)
    await session.flush()
    collected = await farm.harvest(session, constants, catalog, body, plot, now=plot.settled_at)
    await session.commit()

    expected = 10 * plant.yield_per_m2 * (40 / plant.requires.fertility) * _stand(constants, plant)
    assert collected == pytest.approx(expected, rel=0.01)

    #: The collected stack is not a seed sack: we search by harvest quality,
    #: and it equals fertility taken by full health.
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


async def test_a_sick_bed_harvests_by_its_health(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The harvest is the health's share (D-296): a bed that suffered gives
    less, and the loss was visible as a word all along, never a surprise."""
    _, _, body = await _farmstead(session)
    plant = catalog.plants.by_id(SPELT)
    plot = await _ready(session, constants, catalog, body, area=10)
    _grown(plot, health=40)
    await session.flush()
    sick = await farm.harvest(session, constants, catalog, body, plot, now=plot.settled_at)
    soil = min(55 / plant.requires.fertility, constants[R.FARM_SOIL_SHARE_CAP] / 100)
    assert sick == pytest.approx(
        10 * plant.yield_per_m2 * soil * 0.4 * _stand(constants, plant), rel=0.01
    )


# --- the land remembers ------------------------------------------------------


async def test_monoculture_depletes_and_beans_restore(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _farmstead(session, fertility=55)
    plot = await _ready(session, constants, catalog, body, area=10)
    _grown(plot)
    await session.flush()
    moment = plot.settled_at

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
    _grown(plot)
    await session.flush()
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
    _grown(plot)
    await session.flush()
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

    _grown(plot)
    await session.flush()
    collected = await farm.harvest(session, constants, catalog, body, plot, now=plot.settled_at)
    expected = (
        10 * plant.yield_per_m2 * constants[R.FARM_SOIL_SHARE_CAP] / 100 * _stand(constants, plant)
    )
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

    recovery = constants[R.FARM_FERTILIZER_RECOVERY]
    await farm.fertilize(session, constants, body, plot, "compost", now=moment)
    assert float(plot.fertility) == pytest.approx(40 + recovery["compost"])
    await farm.fertilize(session, constants, body, plot, "mineral_fertilizer", now=moment)
    assert float(plot.fertility) == pytest.approx(
        40 + recovery["compost"] + recovery["mineral_fertilizer"]
    )
    assert recovery["mineral_fertilizer"] > recovery["compost"], (
        "минеральное обязано давать больше всех"
    )
    #: The engine knows a fertilizer by its class, not by its name (D-291):
    #: every row of the table is a member, and every member has a row.
    assert set(recovery) == set(catalog.recipes.of_class(farm.FERTILIZER))

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


def test_the_land_is_kept_at_the_scale_it_is_written_with() -> None:
    """`ROUND_QUALITY` and the fertility column are one number in two places.

    The accrual below rounds to this scale before it stores, and then measures
    how far the row moved to decide how much of the idleness it may claim.
    Widen the column without the constant and it measures against a number the
    row never held -- which is the whole of the defect it was written against.
    """
    assert Plot.__table__.c.fertility.type.scale == ROUND_QUALITY


async def test_the_land_heals_as_much_worked_often_as_worked_once(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Fallow is credited by elapsed time, not by how often it is asked about.

    Fertility is kept to a hundredth, and at two a day that is over eleven
    minutes of lying fallow before the column can show anything. Every touch of
    a plot accrues, and the stamp used to move whatever the column could show
    for it -- so a plot worked oftener than that recovered nothing at all, and
    the mechanic silently did not exist for anyone actually farming.
    """
    _, _, body = await _farmstead(session, area=200, fertility=30)
    often = await farm.mark(session, constants, body, name="частый", area=10)
    once = await farm.mark(session, constants, body, name="редкий", area=10)
    started = datetime.now(UTC)
    for plot in (often, once):
        plot.fertility = 30
        plot.idle_since = started
    await session.flush()

    #: Through the row every time, not in memory: the whole defect lives in the
    #: round trip, where `Numeric(6, 2)` rounds the write away. A test that
    #: keeps the value in the session never sees it -- this one did not, and
    #: passed on the unfixed code.
    steps, every = 24, timedelta(minutes=5)
    for tick in range(1, steps + 1):
        _accrue_fallow(constants, often, started + every * tick)
        await session.flush()
        await session.refresh(often, ["fertility", "idle_since"])
    _accrue_fallow(constants, once, started + every * steps)
    await session.flush()
    await session.refresh(once, ["fertility", "idle_since"])

    #: Two hours of fallow, worth a tenth at two a day -- while a single one of
    #: the twenty-four steps is worth less than the hundredth the column keeps.
    worth = constants[R.FARM_FALLOW_RECOVERY] * (steps * every) / _day(constants)
    #: Never more than the hours earned, and never more than the one step the
    #: column cannot yet show -- that part is not lost, it waits in the stamp.
    assert float(once.fertility) <= 30 + worth
    assert float(once.fertility) > 30 + worth - 0.01
    #: Exactly the same, not nearly: a tolerance of a hundredth here would
    #: permit precisely the hundredth the defect loses.
    assert Decimal(often.fertility) == Decimal(once.fertility)


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
