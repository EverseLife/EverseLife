# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Weeds and crowding (D-297), wave 2 of D-296.

Checked is what the system is built this way for:

* weeds come up with the land -- none on bare rock, fast on rich soil -- and
  a full cover drags the growth and quickens the drying;
* a weeding clears them; a thinning is once, early, and refused afterwards;
* the stand pays at the harvest: the culture's crowd penalty unthinned, the
  thinning's own cost thinned -- so it pays for flax and not for brome;
* the survey shows weeds past the threshold and a crowded stand from the leaf
  stage to everybody;
* two thinnings of one bed leave one.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from farm_kit import BROME, _farmstead, _hands_free, _norms, _sown, _stock, _weather
from src.api.commands.farm import _plot
from src.constants import Catalog, Constants, current, current_catalog
from src.constants import registry as R
from src.engine import farm, occupation, world
from src.engine.farm import life
from src.models.farm import Plot, PlotState
from src.models.identity import Body
from src.units import HARDINESS_SCALE, PERCENT, SCALE_MAX

FLAX = "flax"


# --- the pure model ----------------------------------------------------------


def test_weeds_come_up_with_the_land_and_drag_the_crop(
    constants: Constants, catalog: Catalog
) -> None:
    norm = _norms(constants, catalog)
    day = constants[R.TIME_DAY_TERRA]
    weather = _weather(river=True, temperature=constants[R.FARM_DRY_TEMP_REF])
    mid = (norm.band_min + norm.band_max) / 2
    clean = life.Life(mid, SCALE_MAX, 0.0)

    #: Bare rock grows no weeds; rich land grows the vault's share a day.
    rock = life.advance(constants, norm, weather, clean, hours=day, day_hours=day, fertility=0)
    assert rock.weeds == 0
    rich = life.advance(constants, norm, weather, clean, hours=day, day_hours=day, fertility=100)
    assert rich.weeds == pytest.approx(constants[R.FARM_WEED_PER_DAY], rel=1e-6)
    poor = life.advance(constants, norm, weather, clean, hours=day, day_hours=day, fertility=50)
    assert poor.weeds == pytest.approx(rich.weeds / 2, rel=1e-6)

    #: A full cover drags the growth by its share and drinks beside the crop.
    weedy = life.Life(mid, SCALE_MAX, 0.0, weeds=SCALE_MAX)
    choked = life.advance(constants, norm, weather, weedy, hours=day, day_hours=day, fertility=0)
    drag = constants[R.FARM_WEED_DRAG] / PERCENT
    assert choked.growth == pytest.approx(rock.growth * (1 - drag), rel=0.02)
    assert choked.moisture < rock.moisture
    assert life.weeds_thirst(constants, SCALE_MAX) == pytest.approx(
        1 + constants[R.FARM_WEED_THIRST] / PERCENT
    )
    #: Thinning is open up to the vault's stage and shut after it.
    until = str(constants[R.FARM_THIN_UNTIL])
    assert life.thinning_open(constants, life.SPROUT)
    assert life.thinning_open(constants, until)
    assert not life.thinning_open(constants, life.RIPE)


# --- the actions -------------------------------------------------------------


async def test_weeding_clears_and_thinning_is_once_and_early(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _farmstead(session, water="river")
    plot = await _sown(session, constants, catalog, body)
    moment = plot.sown_at
    plot.weeds = Decimal(60)
    await session.flush()

    await farm.weed(session, constants, catalog, body, plot, now=moment)
    assert float(plot.weeds) == 0
    await _hands_free(session, body)

    await farm.thin(session, constants, catalog, body, plot, now=moment)
    assert plot.thinned is True
    await _hands_free(session, body)
    with pytest.raises(farm.WrongState):
        await farm.thin(session, constants, catalog, body, plot, now=moment)

    #: Past the stage the thinning is refused: the stand has closed.
    late = await _sown(session, constants, catalog, body)
    late.growth = Decimal(str(constants[R.FARM_STAGE_BOUNDS]["bloom"]))
    await session.flush()
    with pytest.raises(farm.WrongState):
        await farm.thin(session, constants, catalog, body, late, now=late.sown_at)


async def test_the_stand_pays_the_crowd_or_the_thinning(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Flax fears the crowd and brome does not: thinning pays for one, not the
    other -- and the window offers it to both alike (D-057)."""
    _, _, body = await _farmstead(session, water="river", fertility=55)
    crowd = constants[R.FARM_CROWD_PENALTY] / PERCENT
    loss = constants[R.FARM_THIN_LOSS] / PERCENT

    async def reap(culture: str, thinned: bool) -> float:
        plot = await _sown(session, constants, catalog, body, culture=culture)
        plot.growth = Decimal(SCALE_MAX)
        plot.thinned = thinned
        await session.flush()
        return await farm.harvest(session, constants, catalog, body, plot, now=plot.settled_at)

    def full(culture: str) -> float:
        plant = catalog.plants.by_id(culture)
        soil = min(55 / plant.requires.fertility, constants[R.FARM_SOIL_SHARE_CAP] / PERCENT)
        return 10 * plant.yield_per_m2 * soil

    flax = catalog.plants.by_id(FLAX).traits.density_risk / HARDINESS_SCALE
    assert await reap(FLAX, False) == pytest.approx(full(FLAX) * (1 - flax * crowd), rel=0.01)
    assert await reap(FLAX, True) == pytest.approx(full(FLAX) * (1 - loss), rel=0.01)
    brome = catalog.plants.by_id(BROME).traits.density_risk / HARDINESS_SCALE
    assert brome * crowd <= loss, "the test needs a crop whose thinning does not pay"
    assert await reap(BROME, False) >= await reap(BROME, True)


async def test_the_survey_shows_weeds_and_the_crowd_to_everybody(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, identity, body = await _farmstead(session, water="river")
    plot = await _sown(session, constants, catalog, body)
    plot.weeds = Decimal(str(constants[R.FARM_WEED_SEEN]))
    plot.growth = Decimal(str(constants[R.FARM_STAGE_BOUNDS]["leaf"]))
    await session.flush()

    (row,) = await farm.survey(session, constants, catalog, identity.id)
    assert life.WEEDY in row["symptoms"] and life.CROWDED in row["symptoms"]
    assert "thinned" not in row
    #: The pace the curve is drawn with carries the weeds' thirst (D-297).
    clean = life.dry_rate(constants, _norms(constants, catalog), _weather(river=True), None)
    assert row["dry_per_day"] > clean * PERCENT
    #: The crowd is paid for at the reaping, so the sign holds to the end.
    plot.growth = Decimal(SCALE_MAX)
    await session.flush()
    (ripe,) = await farm.survey(session, constants, catalog, identity.id)
    assert life.CROWDED in ripe["symptoms"]

    plot.thinned = True
    plot.weeds = Decimal(0)
    await session.flush()
    (row,) = await farm.survey(session, constants, catalog, identity.id)
    assert row["thinned"] is True
    assert life.WEEDY not in row["symptoms"] and life.CROWDED not in row["symptoms"]


async def test_two_thinnings_of_one_bed_leave_one(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
) -> None:
    _, _, body = await _farmstead(session, water="river")
    plot = await _sown(session, constants, catalog, body)
    plot_id, body_id = plot.id, body.id
    await session.commit()

    async def pull() -> bool:
        async with factory() as db, db.begin():
            own = await db.get(Body, body_id)
            assert own is not None
            try:
                strip = await _plot(db, {"plot": str(plot_id)})
                await farm.thin(db, current(), current_catalog(), own, strip)
            except (farm.WrongState, occupation.Busy):
                return False
            return True

    pulled = await asyncio.gather(pull(), pull())
    assert pulled.count(True) == 1, pulled
    async with factory() as db:
        strip = await db.get(Plot, plot_id)
        assert strip is not None and strip.state is PlotState.SOWN and strip.thinned


async def test_the_thinning_costs_the_seed_return_too(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A pulled seedling gives no seed (D-257, D-297): the fund comes back by
    the same stand share as the goods."""
    _, _, body = await _farmstead(session, water="river", fertility=55)
    plot = await _sown(session, constants, catalog, body, culture=FLAX)
    plant = catalog.plants.by_id(FLAX)
    pocket = await world.body_container(session, body)
    before = await _stock(session, pocket.id, plant.seed)
    plot.growth = Decimal(SCALE_MAX)
    plot.thinned = True
    await session.flush()
    await farm.harvest(session, constants, catalog, body, plot, now=plot.settled_at)

    soil = min(55 / plant.requires.fertility, constants[R.FARM_SOIL_SHARE_CAP] / PERCENT)
    expected = (
        constants[R.FARM_SEED_RATE]
        * 10
        * constants[R.FARM_SEED_RETURN]
        * soil
        * (1 - constants[R.FARM_THIN_LOSS] / PERCENT)
    )
    assert await _stock(session, pocket.id, plant.seed) - before == pytest.approx(
        expected, rel=0.01
    )
