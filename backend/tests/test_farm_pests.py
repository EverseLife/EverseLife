# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Pests (D-299), wave 3 of D-296.

Checked is what the system is built this way for:

* a bed kept in its band, weeded and thinned never falls ill at all -- the
  promise the whole decision rests on, and the reason there is no roll;
* each mistake of care breeds its own trouble: soaking a fungus, drought a
  mite, weeds the insects, a spoiled feeding the bacteria;
* the trouble spreads by the vault's share a day, cuts the harvest and the
  seed fund by what it took, and takes health until it kills;
* the sign shows past the threshold and names nothing: it is the eye's word;
* a treatment spends its dose, guards its own pest and freezes the pressure;
  against a trouble already struck it stops the spread and heals nothing;
* a preparation of the wrong class is a dose spent and no more;
* two treatments at once spend one dose.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from farm_kit import _farmstead, _hands_free, _norms, _sown, _stand, _stock, _weather
from src.api.commands.farm import _plot
from src.constants import Catalog, Constants, current, current_catalog
from src.constants import registry as R
from src.engine import farm, occupation, world
from src.engine.farm import life
from src.models.event import Event, EventKind
from src.models.farm import Plot
from src.models.identity import Body
from src.units import HARDINESS_SCALE, PERCENT, SCALE_MAX

#: The four preparations of the vault, by the pest each answers.
FUNGICIDE = "sulfur_dust"
ACARICIDE = "oil_emulsion"


def _band(constants: Constants, catalog: Catalog) -> tuple[float, float]:
    norm = _norms(constants, catalog)
    return norm.band_min, norm.band_max


# --- the pure model ----------------------------------------------------------


def test_a_bed_kept_by_the_book_never_falls_ill(constants: Constants, catalog: Catalog) -> None:
    """The promise of D-299: no roll anywhere, so care alone decides. A bed in
    its band, weeded, thinned and unfed builds no pressure at all."""
    norm = _norms(constants, catalog)
    day = constants[R.TIME_DAY_TERRA]
    low, high = _band(constants, catalog)
    weather = _weather(river=True, temperature=constants[R.FARM_DRY_TEMP_REF])
    walked = life.Life((low + high) / 2, SCALE_MAX, 0.0, thinned=True)

    #: A month of care as the text asks for it, on land rich enough to grow
    #: weeds (D-297): the bed is watered back into its band every day, weeded
    #: once the sign shows -- "полют, когда виден" -- thinned, and never fed at
    #: random. Rich land is the honest case: on bare rock nothing would drive.
    seen = constants[R.FARM_WEED_SEEN]
    for _ in range(30):
        walked = life.advance(
            constants, norm, weather, walked, hours=day, day_hours=day, fertility=100
        )
        walked = life.Life(
            (low + high) / 2,
            walked.health,
            walked.growth,
            thinned=True,
            weeds=0.0 if walked.weeds >= seen else walked.weeds,
            pest=walked.pest,
            illness=walked.illness,
            illness_kind=walked.illness_kind,
        )
    assert walked.illness_kind is None
    assert max(walked.pest.values()) == 0
    assert walked.illness == 0
    assert walked.health == SCALE_MAX


def test_each_mistake_breeds_its_own_trouble(constants: Constants, catalog: Catalog) -> None:
    """One mistake to each pest, and the sign it shows is its own (D-299)."""
    norm = _norms(constants, catalog)
    low, high = _band(constants, catalog)

    soaked = life.Life(SCALE_MAX, SCALE_MAX, 0.0, thinned=True)
    dry = life.Life(0.0, SCALE_MAX, 0.0, thinned=True)
    #: A full cover: past the sign, where the mistake is a mistake (D-299).
    weedy = life.Life((low + high) / 2, SCALE_MAX, 0.0, weeds=SCALE_MAX, thinned=True)
    burnt = life.Life(
        (low + high) / 2,
        SCALE_MAX,
        0.0,
        thinned=True,
        fed={life.SPROUT: [{"goods": "compost", "effect": life.BURN}]},
    )
    drives = {
        life.FUNGUS: life.pest_drives(constants, norm, soaked, life.SPROUT),
        life.MITE: life.pest_drives(constants, norm, dry, life.SPROUT),
        life.INSECT: life.pest_drives(constants, norm, weedy, life.SPROUT),
        life.BACTERIA: life.pest_drives(constants, norm, burnt, life.SPROUT),
    }
    for pest, seen in drives.items():
        assert seen[pest] == pytest.approx(1.0), pest
        assert sum(seen.values()) == pytest.approx(1.0), f"{pest} drives nothing else"

    #: And each shows its own sign once it is seen.
    struck = {
        pest: life.Life(
            (low + high) / 2,
            SCALE_MAX,
            0.0,
            thinned=True,
            illness=constants[R.FARM_PEST_SEEN],
            illness_kind=pest,
        )
        for pest in life.PESTS
    }
    for pest, bed in struck.items():
        signs = life.symptoms(constants, norm, bed, fertility=PERCENT, fertility_needed=0.0, fed=())
        assert life.PEST_SIGNS[pest] in signs
        #: And not before: a trouble under the threshold is not yet seen, so
        #: the journal has nothing to say either (D-299).
        early = life.Life(
            bed.moisture,
            bed.health,
            bed.growth,
            thinned=True,
            illness=constants[R.FARM_PEST_SEEN] - 1,
            illness_kind=pest,
        )
        quiet = life.symptoms(
            constants, norm, early, fertility=PERCENT, fertility_needed=0.0, fed=()
        )
        assert life.PEST_SIGNS[pest] not in quiet


def test_the_pressure_builds_by_the_mistake_and_falls_when_it_is_mended(
    constants: Constants, catalog: Catalog
) -> None:
    """The share of the mistake, the cultivar's fear and the crowd of an
    unthinned stand -- and nothing else -- decide how fast it comes."""
    norm = _norms(constants, catalog)
    day = constants[R.TIME_DAY_TERRA]
    low, high = _band(constants, catalog)
    weather = _weather(rain=PERCENT, river=True, temperature=constants[R.FARM_DRY_TEMP_REF])

    #: An hour of it: long enough to measure, short enough that the ground
    #: has not dried out from under the mistake being measured.
    soaked = life.Life(SCALE_MAX, SCALE_MAX, 0.0, thinned=True)
    hour = life.advance(constants, norm, weather, soaked, hours=1, day_hours=day, fertility=0)
    fear = norm.pest_risk / HARDINESS_SCALE
    assert hour.pest[life.FUNGUS] == pytest.approx(
        constants[R.FARM_PEST_PRESSURE] * fear / day, rel=0.02
    )
    #: An unthinned stand is close and airless: the same mistake costs more.
    crowded = life.advance(
        constants,
        norm,
        weather,
        life.Life(SCALE_MAX, SCALE_MAX, 0.0),
        hours=1,
        day_hours=day,
        fertility=0,
    )
    assert crowded.pest[life.FUNGUS] == pytest.approx(
        hour.pest[life.FUNGUS] * (1 + constants[R.FARM_CROWD_PEST] / PERCENT), rel=0.02
    )
    #: Half the mistake builds half as fast: the share is the whole of it.
    half = norm.band_max + (SCALE_MAX - norm.band_max) / 2
    lesser = life.advance(
        constants,
        norm,
        weather,
        life.Life(half, SCALE_MAX, 0.0, thinned=True),
        hours=1,
        day_hours=day,
        fertility=0,
    )
    assert lesser.pest[life.FUNGUS] == pytest.approx(hour.pest[life.FUNGUS] / 2, rel=0.05)

    #: Mended, the pressure falls back -- not at once, but it falls.
    mended = life.advance(
        constants,
        norm,
        weather,
        life.Life((low + high) / 2, SCALE_MAX, 0.0, thinned=True, pest={life.FUNGUS: 50.0}),
        hours=1,
        day_hours=day,
        fertility=0,
    )
    assert mended.pest[life.FUNGUS] == pytest.approx(
        50 - constants[R.FARM_PEST_RELIEF] / day, rel=0.02
    )


def test_the_trouble_strikes_spreads_and_takes_the_health(
    constants: Constants, catalog: Catalog
) -> None:
    """Past the scale the trouble comes at `farm.pest_onset`, spreads by
    `farm.disease_spread` a day and saps the health while it stands."""
    norm = _norms(constants, catalog)
    day = constants[R.TIME_DAY_TERRA]
    low, high = _band(constants, catalog)
    weather = _weather(rain=PERCENT, river=True, temperature=constants[R.FARM_DRY_TEMP_REF])

    brink = life.Life(SCALE_MAX, SCALE_MAX, 0.0, thinned=True, pest={life.FUNGUS: SCALE_MAX - 0.1})
    struck = life.advance(constants, norm, weather, brink, hours=1, day_hours=day, fertility=0)
    assert struck.illness_kind == life.FUNGUS
    assert struck.illness == pytest.approx(constants[R.FARM_PEST_ONSET])
    assert struck.pest[life.FUNGUS] == 0, "the pressure discharged into the trouble"

    #: A day of it: the share grows by the vault's spread and the health pays
    #: for the share that stands.
    ill = life.Life(
        (low + high) / 2, SCALE_MAX, 0.0, thinned=True, illness=20.0, illness_kind=life.FUNGUS
    )
    later = life.advance(constants, norm, weather, ill, hours=day, day_hours=day, fertility=0)
    assert later.illness == pytest.approx(20 + constants[R.FARM_DISEASE_SPREAD], rel=0.02)
    assert later.health < SCALE_MAX
    #: While a bed is struck the other three pressures stand still: the farmer
    #: fights what came, not what might.
    assert later.pest.get(life.MITE, 0.0) == 0

    #: Left alone it kills -- slower than drought, but it kills.
    dead = life.advance(
        constants,
        norm,
        weather,
        life.Life(
            (low + high) / 2,
            SCALE_MAX,
            0.0,
            thinned=True,
            illness=SCALE_MAX,
            illness_kind=life.FUNGUS,
        ),
        hours=day * 60,
        day_hours=day,
        fertility=0,
    )
    assert dead.dead


def test_a_guard_freezes_the_pressure_and_holds_the_trouble_where_it_stands(
    constants: Constants, catalog: Catalog
) -> None:
    """What a treatment buys, in the model: its own pest builds nothing while
    it holds, and a trouble already struck stops spreading -- and no more."""
    norm = _norms(constants, catalog)
    day = constants[R.TIME_DAY_TERRA]
    low, high = _band(constants, catalog)
    weather = _weather(rain=PERCENT, river=True, temperature=constants[R.FARM_DRY_TEMP_REF])
    guarded = {life.FUNGUS: day * 2}

    soaked = life.Life(SCALE_MAX, SCALE_MAX, 0.0, thinned=True)
    held = life.advance(
        constants, norm, weather, soaked, hours=day, day_hours=day, fertility=0, guarded=guarded
    )
    assert held.pest[life.FUNGUS] == 0

    ill = life.Life(
        (low + high) / 2, SCALE_MAX, 0.0, thinned=True, illness=30.0, illness_kind=life.FUNGUS
    )
    stopped = life.advance(
        constants, norm, weather, ill, hours=day, day_hours=day, fertility=0, guarded=guarded
    )
    assert stopped.illness == pytest.approx(30.0), "a guard stops the spread"
    assert stopped.health < SCALE_MAX, "and never takes back what was struck"


# --- the bed and the hands ---------------------------------------------------


async def test_the_treatment_spends_its_dose_and_holds_the_guard(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The action: a norm by the area off the pocket, the pressure of its own
    pest to nought, and a guard by the class until the vault's day."""
    _, _, body = await _farmstead(session, water="river")
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, FUNGICIDE, amount=20, origin="тест")
    plot = await _sown(session, constants, catalog, body)
    plot.pest = {life.FUNGUS: 60.0, life.MITE: 40.0}
    await session.flush()
    moment = plot.sown_at

    _, pest, stopped = await farm.treat(
        session, constants, catalog, body, plot, FUNGICIDE, now=moment
    )
    assert (pest, stopped) == (life.FUNGUS, False)
    assert plot.pest[life.FUNGUS] == 0
    assert plot.pest[life.MITE] == pytest.approx(40.0), "the other pests are none of its business"
    dose = constants[R.FARM_PROTECTANT_PER_M2] * 10
    assert await _stock(session, pocket.id, FUNGICIDE) == pytest.approx(20 - dose)

    days = constants[R.FARM_PROTECT_DAYS][FUNGICIDE]
    klass = current_catalog().recipes.class_of(FUNGICIDE)
    until = datetime.fromisoformat(plot.guard[klass])
    assert until - moment == timedelta(hours=days * constants[R.TIME_DAY_TERRA])


async def test_the_right_class_stops_a_trouble_and_the_wrong_one_is_a_dose_spent(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Which bottle answers which sign is the text's to teach (D-057): the
    engine takes any of the four and says only what it did."""
    _, _, body = await _farmstead(session, water="river")
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, FUNGICIDE, amount=20, origin="тест")
    await world.grant_item(session, pocket, ACARICIDE, amount=20, origin="тест")
    plot = await _sown(session, constants, catalog, body)
    plot.illness = Decimal(30)
    plot.illness_kind = life.FUNGUS
    await session.flush()
    moment = plot.sown_at

    #: The wrong class: the dose goes, the trouble stays untouched.
    _, pest, stopped = await farm.treat(
        session, constants, catalog, body, plot, ACARICIDE, now=moment
    )
    assert (pest, stopped) == (life.MITE, False)
    dose = constants[R.FARM_PROTECTANT_PER_M2] * 10
    assert await _stock(session, pocket.id, ACARICIDE) == pytest.approx(20 - dose)
    await _hands_free(session, body)

    #: The right one catches it -- and still takes nothing back.
    _, pest, stopped = await farm.treat(
        session, constants, catalog, body, plot, FUNGICIDE, now=moment
    )
    assert (pest, stopped) == (life.FUNGUS, True)
    assert float(plot.illness) == pytest.approx(30.0)


async def test_a_thing_of_no_class_is_refused_before_the_dose(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _farmstead(session, water="river")
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "compost", amount=20, origin="тест")
    plot = await _sown(session, constants, catalog, body)
    with pytest.raises(farm.FarmError):
        await farm.treat(session, constants, catalog, body, plot, "compost", now=plot.sown_at)
    assert await _stock(session, pocket.id, "compost") == pytest.approx(20)


async def test_the_pest_takes_its_share_of_the_harvest_and_of_the_fund(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """What the trouble struck gave nothing: the same share off the goods and
    off the seed fund (D-257, D-299)."""
    _, _, body = await _farmstead(session, water="river", fertility=55)
    plot = await _sown(session, constants, catalog, body)
    plant = catalog.plants.by_id(plot.culture_id)
    pocket = await world.body_container(session, body)
    plot.growth = Decimal(SCALE_MAX)
    plot.illness = Decimal(40)
    plot.illness_kind = life.FUNGUS
    await session.flush()
    before = await _stock(session, pocket.id, plant.seed)
    got = await farm.harvest(session, constants, catalog, body, plot, now=plot.settled_at)

    soil = min(55 / plant.requires.fertility, constants[R.FARM_SOIL_SHARE_CAP] / PERCENT)
    whole = 10 * plant.yield_per_m2 * soil * _stand(constants, plant)
    assert got == pytest.approx(whole * 0.6, rel=0.01)
    fund = (
        constants[R.FARM_SEED_RATE]
        * 10
        * constants[R.FARM_SEED_RETURN]
        * soil
        * _stand(constants, plant)
        * 0.6
    )
    assert await _stock(session, pocket.id, plant.seed) - before == pytest.approx(fund, rel=0.01)


async def test_the_bed_says_the_sign_and_the_guard_it_holds(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The survey shows the eye's word and the fact of a guard -- neither of
    which the client can work out for itself (D-225)."""
    _, identity, body = await _farmstead(session, water="river")
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, FUNGICIDE, amount=20, origin="тест")
    plot = await _sown(session, constants, catalog, body)
    plot.illness = Decimal(str(constants[R.FARM_PEST_SEEN]))
    plot.illness_kind = life.FUNGUS
    await session.flush()

    (row,) = await farm.survey(session, constants, catalog, identity.id)
    assert life.SPOTS in row["symptoms"]
    assert "guard" not in row
    #: The trouble itself is never a number on the wire: the sign is all.
    assert "illness" not in row and "pest" not in row

    await farm.treat(session, constants, catalog, body, plot, FUNGICIDE, now=datetime.now(UTC))
    (row,) = await farm.survey(session, constants, catalog, identity.id)
    assert current_catalog().recipes.class_of(FUNGICIDE) in row["guard"]


async def test_the_strike_is_told_the_hour_it_happens(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A pest is one of the ends of things (D-226): the tick tells it where it
    finds it, by its sign."""
    _, identity, body = await _farmstead(session, water="river")
    plot = await _sown(session, constants, catalog, body)
    plot.moisture = Decimal(SCALE_MAX)
    plot.thinned = True
    plot.pest = {life.FUNGUS: SCALE_MAX - 1}
    #: Ripe within the same walk: two crossings in one settling, and the
    #: journal owes a line to each -- the ripening must not be swallowed.
    plot.growth = Decimal(99)
    plot.settled_at = datetime.now(UTC) - timedelta(hours=constants[R.TIME_DAY_TERRA])
    await session.flush()

    tally = await farm.tick_plots(session, constants, catalog)
    assert tally["plots_struck"] == 1
    assert tally["plots_ripened"] == 1
    assert plot.illness_kind == life.FUNGUS
    told = [
        event
        for event in (await session.execute(select(Event))).scalars()
        if event.kind == EventKind.PLOT_STRUCK.value
    ]
    assert len(told) == 1
    #: The sign, not the name of the trouble: the journal says what the eye saw.
    assert told[0].payload["sign"] == life.SPOTS
    assert "pest" not in told[0].payload
    kinds = {event.kind for event in (await session.execute(select(Event))).scalars()}
    assert EventKind.PLOT_RIPENED.value in kinds, "the ripening is told too"


async def test_two_treatments_of_one_bed_spend_the_dose_once(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The dose is written off under the same locks as the water and the
    fertilizer: one of two concurrent treatments goes through, the bottle is
    spent once."""
    _, _, body = await _farmstead(session, water="river")
    plot = await _sown(session, constants, catalog, body)
    dose = constants[R.FARM_PROTECTANT_PER_M2] * 10
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, FUNGICIDE, amount=dose, origin="тест")
    plot_id, body_id, pocket_id = plot.id, body.id, pocket.id
    await session.commit()

    async def dust() -> bool:
        async with factory() as db, db.begin():
            own = await db.get(Body, body_id)
            assert own is not None
            try:
                strip = await _plot(db, {"plot": str(plot_id)})
                await farm.treat(db, current(), current_catalog(), own, strip, FUNGICIDE)
            except (farm.WrongState, farm.FarmError, occupation.Busy):
                return False
            return True

    done = await asyncio.gather(dust(), dust())
    assert done.count(True) == 1, done
    async with factory() as db:
        assert await _stock(db, pocket_id, FUNGICIDE) == pytest.approx(0)
        strip = await db.get(Plot, plot_id)
        assert strip is not None and strip.guard, "the one that went through left its guard"


def test_neglect_meets_a_pest_inside_the_cycle(constants: Constants, catalog: Catalog) -> None:
    """The other half of the promise (D-299): the numbers must let a careless
    farmer actually meet the trouble, or four classes of preparation are dead
    weight. A bed sown and left -- unwatered, unweeded, unthinned -- is struck
    before the crop it carries would have ripened."""
    norm = _norms(constants, catalog)
    day = constants[R.TIME_DAY_TERRA]
    weather = _weather(temperature=constants[R.FARM_DRY_TEMP_REF])
    bed = life.Life(constants[R.FARM_SOWN_MOISTURE], SCALE_MAX, 0.0)

    struck = ripe = None
    for elapsed in range(1, 61):
        bed = life.advance(constants, norm, weather, bed, hours=day, day_hours=day, fertility=100)
        if bed.illness_kind and struck is None:
            struck = elapsed
        if bed.ripe and ripe is None:
            ripe = elapsed
        if bed.dead:
            break
    assert struck is not None, "a bed nobody touches must fall ill"
    assert ripe is None or struck <= ripe, "and fall ill before it would have ripened"
