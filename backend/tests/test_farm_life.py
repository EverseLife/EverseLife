# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A bed's life by three scales (D-293).

Checked is what the system is built this way for:

* moisture leaves as a share of what is there and never runs dry by itself;
  the river, the rain and the culture's thirst set the pace;
* health falls in proportion to the gap from the culture's band, softened by
  hardiness, and a bed left without water dies -- the land paying the cycle;
* growth is paced by health, and a feeding's boost ends with its stage;
* a watering takes exactly the difference, from the river or from the hands;
* a feeding is what the culture's table says it is: a boost, a burn, or a
  crop run to leaf;
* the survey says two words and one curve and not a single norm -- and the
  norm is a text, read in the Library and remembered;
* an action holds the hands for its minutes; two waterings of one bed spend
  the water once.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from farm_kit import (
    BROME,
    SPELT,
    _farmstead,
    _hands_free,
    _norms,
    _sown,
    _stand,
    _stock,
    _weather,
)
from src.api.commands.farm import _plot
from src.constants import Catalog, Constants, current, current_catalog
from src.constants import registry as R
from src.engine import breed, farm, occupation, world
from src.engine.farm import life
from src.models.event import Event, EventKind
from src.models.farm import Plot, PlotState
from src.models.identity import Body
from src.models.job import Job, JobKind
from src.models.world import Node
from src.units import PERCENT, SCALE_MAX

FLAX = "flax"


# --- the pure model ----------------------------------------------------------


def test_moisture_leaves_as_a_share_and_never_runs_dry(
    constants: Constants, catalog: Catalog
) -> None:
    """Wet ground dries fast, dry ground barely: the share of a share is never nought."""
    norm = _norms(constants, catalog)
    day = constants[R.TIME_DAY_TERRA]
    weather = _weather(temperature=constants[R.FARM_DRY_TEMP_REF])
    start = life.Life(moisture=80.0, health=SCALE_MAX, growth=0.0)

    after = life.advance(constants, norm, weather, start, hours=day, day_hours=day)
    rate = constants[R.FARM_DRY_RATE] / PERCENT * norm.drink
    assert after.moisture == pytest.approx(80 * math.exp(-rate), rel=1e-3)

    later = life.advance(constants, norm, weather, start, hours=day * 7, day_hours=day)
    assert 0 < later.moisture < after.moisture


def test_the_river_the_rain_and_the_thirst_set_the_pace(
    constants: Constants, catalog: Catalog
) -> None:
    norm = _norms(constants, catalog)
    reference = constants[R.FARM_DRY_TEMP_REF]
    dry = life.dry_rate(constants, norm, _weather(), None)
    assert life.dry_rate(constants, norm, _weather(river=True), None) == pytest.approx(
        dry * constants[R.FARM_RIVER_DRY_SHARE] / PERCENT
    )
    assert life.dry_rate(constants, norm, _weather(rain=PERCENT), None) == pytest.approx(
        dry * (1 - constants[R.SITE_RAIN_WATER_OFFSET] / PERCENT)
    )
    assert life.dry_rate(constants, norm, _weather(), reference + 10) > dry
    assert life.dry_rate(constants, norm, _weather(), reference - 10) < dry
    #: A thirsty crop drinks faster than an undemanding one.
    thirsty = _norms(constants, catalog, FLAX)
    hardy = _norms(constants, catalog, BROME)
    assert life.dry_rate(constants, thirsty, _weather(), None) > life.dry_rate(
        constants, hardy, _weather(), None
    )


def test_health_falls_by_the_gap_and_drought_kills(constants: Constants, catalog: Catalog) -> None:
    norm = _norms(constants, catalog, FLAX)
    day = constants[R.TIME_DAY_TERRA]
    weather = _weather(river=True, temperature=constants[R.FARM_DRY_TEMP_REF])
    mid = (norm.band_min + norm.band_max) / 2

    #: Inside the band nothing is lost, and the day heals.
    inside = life.advance(
        constants, norm, weather, life.Life(mid, 80.0, 0.0), hours=1, day_hours=day
    )
    assert inside.health > 80

    #: Far below the band the loss is at least the gap's worth for the day --
    #: the gap only widens as the ground dries further.
    parched = life.Life(5.0, SCALE_MAX, 0.0)
    relief = 1 - constants[R.FARM_HARDINESS_RELIEF] / PERCENT * norm.hardiness / 5
    one_day = life.advance(constants, norm, weather, parched, hours=day, day_hours=day)
    floor = constants[R.FARM_STRESS_PER_POINT] * (norm.band_min - 5) * relief
    assert SCALE_MAX - one_day.health >= floor - 1e-6

    #: Left like that, the crop dies -- and stays dead.
    dead = life.advance(constants, norm, weather, parched, hours=day * 30, day_hours=day)
    assert dead.dead and dead.health == 0
    assert life.advance(constants, norm, weather, dead, hours=day, day_hours=day) is dead

    #: Hardiness softens the same gap (D-261): a hardy line lasts longer.
    tough = replace(norm, hardiness=5)
    assert (
        life.advance(constants, tough, weather, parched, hours=day, day_hours=day).health
        > one_day.health
    )


def test_growth_is_paced_by_health_and_a_boost_ends_with_its_stage(
    constants: Constants, catalog: Catalog
) -> None:
    norm = _norms(constants, catalog)
    day = constants[R.TIME_DAY_TERRA]
    weather = _weather(river=True, temperature=constants[R.FARM_DRY_TEMP_REF])
    mid = (norm.band_min + norm.band_max) / 2
    pace = SCALE_MAX / norm.cycle_days

    healthy = life.advance(
        constants, norm, weather, life.Life(mid, SCALE_MAX, 0.0), hours=day, day_hours=day
    )
    assert healthy.growth == pytest.approx(pace, rel=0.02)

    sick = life.advance(
        constants, norm, weather, life.Life(mid, 50.0, 0.0), hours=day, day_hours=day
    )
    assert sick.growth < healthy.growth * 0.6

    #: A boost doubles the pace and is dropped once the stage's bound is crossed.
    boosted = life.advance(
        constants,
        norm,
        weather,
        life.Life(mid, SCALE_MAX, 0.0, boost=100.0, boost_stage=life.SPROUT),
        hours=day,
        day_hours=day,
    )
    leaf = constants[R.FARM_STAGE_BOUNDS]["leaf"]
    assert leaf < boosted.growth < 2 * pace
    assert boosted.boost == 0 and boosted.boost_stage is None

    #: The words: stages by their bounds, health by its bands.
    bounds = constants[R.FARM_STAGE_BOUNDS]
    assert life.stage_of(constants, 0) == life.SPROUT
    assert life.stage_of(constants, bounds["leaf"]) == "leaf"
    assert life.stage_of(constants, bounds["fill"]) == "fill"
    assert life.stage_of(constants, SCALE_MAX) == life.RIPE
    bands = constants[R.FARM_HEALTH_BANDS]
    assert life.health_word(constants, SCALE_MAX) == "strong"
    assert life.health_word(constants, bands["strong"] - 0.1) == "weak"
    assert life.health_word(constants, bands["sick"] - 0.1) == "dying"


# --- the actions -------------------------------------------------------------


async def test_watering_takes_the_difference(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """By a river for nothing; on dry ground from the hands, and short of it
    the action does not start (D-126). Wetter than the target -- refused."""
    _, _, body = await _farmstead(session, water="river")
    plot = await _sown(session, constants, catalog, body)
    moment = plot.sown_at
    sown = constants[R.FARM_SOWN_MOISTURE]

    _, litres = await farm.water(session, constants, catalog, body, plot, sown + 30, now=moment)
    assert float(plot.moisture) == pytest.approx(sown + 30)
    assert litres == pytest.approx(30 / PERCENT * constants[R.FARM_WATER_PER_M2] * 10)
    await _hands_free(session, body)
    with pytest.raises(farm.WrongState):
        await farm.water(session, constants, catalog, body, plot, sown + 10, now=moment)

    dry, _, _ = await _farmstead(session, water="none")
    dry.owner_identity_id = body.identity_id
    body.node_id = dry.id
    await session.flush()
    strip = await _sown(session, constants, catalog, body)
    with pytest.raises(farm.NoWater):
        await farm.water(session, constants, catalog, body, strip, sown + 30, now=strip.sown_at)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, farm.WATER, amount=100, origin="тест")
    _, carried = await farm.water(
        session, constants, catalog, body, strip, sown + 30, now=strip.sown_at
    )
    assert await _stock(session, pocket.id, farm.WATER) == pytest.approx(100 - carried)


async def test_feeding_is_what_the_table_says(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _farmstead(session, water="river")
    plant = catalog.plants.by_id(SPELT)
    right = next(row for row in plant.feeding if row.stage == life.SPROUT)
    wrong = "mineral_fertilizer" if right.fertilizer == "compost" else "compost"
    assert not any(row.stage == life.SPROUT and row.fertilizer == wrong for row in plant.feeding)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, right.fertilizer, amount=20, origin="тест")
    await world.grant_item(session, pocket, wrong, amount=20, origin="тест")

    plot = await _sown(session, constants, catalog, body)
    moment = plot.sown_at
    #: The right thing at the right stage: a boost to the end of the stage.
    _, stage, effect = await farm.feed(
        session, constants, catalog, body, plot, right.fertilizer, now=moment
    )
    assert (stage, effect) == (life.SPROUT, life.BOOST)
    assert float(plot.growth_boost) == pytest.approx(right.growth)
    assert plot.boost_stage == life.SPROUT
    await _hands_free(session, body)
    #: A second feeding in the same stage runs the crop to leaf, whatever it is.
    _, _, effect = await farm.feed(session, constants, catalog, body, plot, wrong, now=moment)
    assert effect == life.OVERFED and plot.overfed == 1
    await _hands_free(session, body)

    #: The wrong thing where nothing was given yet: a burn.
    other = await _sown(session, constants, catalog, body)
    _, _, effect = await farm.feed(
        session, constants, catalog, body, other, wrong, now=other.sown_at
    )
    assert effect == life.BURN
    assert float(other.health) == pytest.approx(SCALE_MAX - constants[R.FARM_FEED_WRONG_BURN])
    #: The dose is the land's (D-264): a norm per metre, whatever the effect.
    dose = constants[R.FARM_FERTILIZER_PER_M2] * 10
    assert await _stock(session, pocket.id, right.fertilizer) == pytest.approx(20 - dose)
    assert await _stock(session, pocket.id, wrong) == pytest.approx(20 - 2 * dose)


async def test_a_bed_without_water_dies_and_the_land_pays(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _farmstead(session, water="none", fertility=55)
    plot = await _sown(session, constants, catalog, body, culture=FLAX)
    later = plot.sown_at + timedelta(days=30)

    state = await farm.settle(session, constants, catalog, plot, now=later)
    assert state.dead
    assert plot.state is PlotState.IDLE and plot.culture_id is None
    assert plot.last_culture == FLAX and plot.same_culture_cycles == 1
    assert float(plot.fertility) == pytest.approx(55 - constants[R.FARM_SOIL_DEPLETION])
    told = await session.scalar(
        select(func.count()).select_from(Event).where(Event.kind == EventKind.PLOT_DIED.value)
    )
    assert told == 1


async def test_the_tick_settles_every_bed_and_tells_the_ripening(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _farmstead(session, water="river")
    plot = await _sown(session, constants, catalog, body)
    norm = _norms(constants, catalog)
    plot.moisture = Decimal(str((norm.band_min + norm.band_max) / 2))
    plot.growth = Decimal(99)
    plot.settled_at = datetime.now(UTC) - timedelta(hours=constants[R.TIME_DAY_TERRA])
    await session.flush()

    assert await farm.tick_plots(session, constants, catalog) == {
        "plots_died": 0,
        "plots_ripened": 1,
    }
    assert float(plot.growth) == SCALE_MAX
    #: Once: the next pass finds it ripe already and says nothing again.
    assert await farm.tick_plots(session, constants, catalog) == {
        "plots_died": 0,
        "plots_ripened": 0,
    }
    told = await session.scalar(
        select(func.count()).select_from(Event).where(Event.kind == EventKind.PLOT_RIPENED.value)
    )
    assert told == 1


async def test_harvest_asks_full_growth_and_pays_by_health(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _farmstead(session, water="river", fertility=55)
    plant = catalog.plants.by_id(SPELT)
    plot = await _sown(session, constants, catalog, body)
    with pytest.raises(farm.WrongState):
        await farm.harvest(session, constants, catalog, body, plot, now=plot.sown_at)

    plot.growth = Decimal(SCALE_MAX)
    plot.health = Decimal(50)
    await session.flush()
    got = await farm.harvest(session, constants, catalog, body, plot, now=plot.settled_at)
    soil = min(55 / plant.requires.fertility, constants[R.FARM_SOIL_SHARE_CAP] / PERCENT)
    assert got == pytest.approx(
        10 * plant.yield_per_m2 * soil * 0.5 * _stand(constants, plant), rel=0.01
    )


# --- what is seen, and what is read ------------------------------------------


async def test_the_survey_says_two_words_and_one_curve(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The stage, the word of health, the moisture with its pace -- and not a
    norm among them (D-293): the band is the Library's text."""
    wet, identity, body = await _farmstead(session, water="river")
    plot = await _sown(session, constants, catalog, body)
    dry, _, _ = await _farmstead(session, water="none")
    dry.owner_identity_id = identity.id
    body.node_id = dry.id
    await session.flush()
    await _sown(session, constants, catalog, body)

    rows = {
        row["node_key"]: row for row in await farm.survey(session, constants, catalog, identity.id)
    }
    row = rows[wet.key]
    for key in ("moisture", "moisture_at", "dry_per_day", "stage", "health", "symptoms"):
        assert key in row, key
    for gone in (
        "ripe_at",
        "sown_at",
        "cycle_days",
        "missed_days",
        "asks_care",
        "agrotech",
        "water_need",
        "fertility_required",
    ):
        assert gone not in row, gone
    assert row["stage"] == life.SPROUT and row["health"] == "strong"
    assert "carried" not in row, "у реки воду не носят"
    assert rows[dry.key]["carried"] is True

    #: Below the band the bed shows thirst -- to everybody, no knowledge asked.
    plot.moisture = Decimal(5)
    await session.flush()
    (thirsty,) = [
        r
        for r in await farm.survey(session, constants, catalog, identity.id)
        if r["id"] == str(plot.id)
    ]
    assert life.THIRST in thirsty["symptoms"]


async def test_care_is_a_text_read_in_the_library_and_remembered(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The norm is words, not a gate (D-293): read on foot, kept for good."""
    _, identity, body = await _farmstead(session)
    with pytest.raises(breed.BreedError):
        await farm.read_care(session, constants, catalog, body, SPELT, locale="ru")

    node = await session.get(Node, body.node_id)
    node.properties = {**node.properties, "library": True}
    await session.flush()
    norm = _norms(constants, catalog)
    text = await farm.read_care(session, constants, catalog, body, SPELT, locale="ru")
    assert str(round(norm.band_min)) in text and str(round(norm.band_max)) in text
    english = await farm.read_care(session, constants, catalog, body, SPELT, locale="en")
    assert english != text and str(round(norm.band_min)) in english

    assert await farm.remember_care(session, catalog, body, SPELT) is not None
    assert await farm.remember_care(session, catalog, body, SPELT) is None
    notes = await farm.remembered(session, constants, catalog, identity.id, locale="en")
    assert [note["culture"] for note in notes] == [SPELT]
    assert notes[0]["text"] == english


async def test_an_action_holds_the_hands_for_its_minutes(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The effect is written at once; the hands are busy until the term (D-211)."""
    _, _, body = await _farmstead(session, water="river")
    plot = await _sown(session, constants, catalog, body)
    sown = constants[R.FARM_SOWN_MOISTURE]
    await farm.water(session, constants, catalog, body, plot, sown + 20)

    doing = await occupation.current(session, body)
    assert doing is not None and doing.kind == occupation.CARE
    with pytest.raises(occupation.Busy):
        await occupation.require_free(session, body)

    #: Once its hour has passed the hands are free, swept or not.
    job = await session.scalar(
        select(Job).where(Job.body_id == body.id, Job.kind == JobKind.FARM_CARE.value)
    )
    job.run_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.flush()
    assert await occupation.current(session, body) is None


async def test_two_waterings_of_one_bed_spend_the_water_once(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The bed is a remainder like money (CLAUDE.md): under the plot's lock the
    second watering of the same body finds the hands still busy with the first
    -- or, with another body, the ground already at the target -- and spends
    nothing either way."""
    _, _, body = await _farmstead(session, water="none")
    plot = await _sown(session, constants, catalog, body)
    sown = constants[R.FARM_SOWN_MOISTURE]
    litres = 30 / PERCENT * constants[R.FARM_WATER_PER_M2] * 10
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, farm.WATER, amount=litres, origin="тест")
    plot_id, body_id, pocket_id = plot.id, body.id, pocket.id
    await session.commit()

    async def pour() -> bool:
        async with factory() as db, db.begin():
            own = await db.get(Body, body_id)
            assert own is not None
            try:
                strip = await _plot(db, {"plot": str(plot_id)})
                await farm.water(db, current(), current_catalog(), own, strip, sown + 30)
            except (farm.WrongState, farm.NoWater, occupation.Busy):
                return False
            return True

    poured = await asyncio.gather(pour(), pour())
    assert poured.count(True) == 1, poured
    async with factory() as db:
        assert await _stock(db, pocket_id, farm.WATER) == pytest.approx(0)


async def test_two_feedings_of_one_bed_spend_the_fertilizer_once(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The dose is written off under the same locks as the water: one of two
    concurrent feedings goes through, the other is refused, the sack is spent once."""
    _, _, body = await _farmstead(session, water="river")
    plot = await _sown(session, constants, catalog, body)
    plant = catalog.plants.by_id(SPELT)
    right = next(row for row in plant.feeding if row.stage == life.SPROUT)
    dose = constants[R.FARM_FERTILIZER_PER_M2] * 10
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, right.fertilizer, amount=dose, origin="тест")
    plot_id, body_id, pocket_id = plot.id, body.id, pocket.id
    await session.commit()

    async def spread() -> bool:
        async with factory() as db, db.begin():
            own = await db.get(Body, body_id)
            assert own is not None
            try:
                strip = await _plot(db, {"plot": str(plot_id)})
                await farm.feed(db, current(), current_catalog(), own, strip, right.fertilizer)
            except (farm.WrongState, farm.FarmError, occupation.Busy):
                return False
            return True

    fed = await asyncio.gather(spread(), spread())
    assert fed.count(True) == 1, fed
    async with factory() as db:
        assert await _stock(db, pocket_id, right.fertilizer) == pytest.approx(0)
        strip = await db.get(Plot, plot_id)
        assert strip is not None and strip.overfed == 0, "второе кормление не прошло, жирования нет"


async def test_the_tick_and_a_watering_do_not_lose_each_other(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The tick writes a stale bed under its own row lock and judges it again
    there: whichever of the two commits second sees the other's stamp, and the
    watering's target is what the ground holds afterwards."""
    _, _, body = await _farmstead(session, water="river")
    plot = await _sown(session, constants, catalog, body)
    plot.settled_at = datetime.now(UTC) - timedelta(hours=constants[R.TIME_DAY_TERRA] * 2)
    await session.flush()
    sown = constants[R.FARM_SOWN_MOISTURE]
    plot_id, body_id = plot.id, body.id
    await session.commit()

    async def tick() -> None:
        async with factory() as db, db.begin():
            await farm.tick_plots(db, current(), current_catalog())

    async def pour() -> None:
        async with factory() as db, db.begin():
            own = await db.get(Body, body_id)
            assert own is not None
            strip = await _plot(db, {"plot": str(plot_id)})
            await farm.water(db, current(), current_catalog(), own, strip, sown + 30)

    await asyncio.gather(tick(), pour())
    async with factory() as db:
        strip = await db.get(Plot, plot_id)
        assert strip is not None and strip.state is PlotState.SOWN
        assert strip.settled_at is not None
        #: The target, less at most the tick's own walk from the watering's stamp.
        assert sown + 30 - 1 < float(strip.moisture) <= sown + 30
