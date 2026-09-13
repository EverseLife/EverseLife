# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The field automaton (D-339): a programme of commands over plots.

Checked is what the decision asks of it:

* the programme reads as the closed list of D-120 with a plough, and the
  setpoints hold their whole season, from harvest to harvest;
* the actions go through the farm's own cores -- the same litres, dose and
  effect -- but out of the yard and the storages its owner named;
* the machine's harvest is its share, under its ceiling, without selection,
  and what does not fit waits on the bed;
* what a hand did is done;
* no lubricant or energy -- it stands, and the owner is told once; worn to
  nothing -- its bunker falls to the yard.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agro_kit import (
    LUBRICANT,
    SPELT,
    chest,
    events_of,
    field,
    goods_in,
    growing,
    held_in,
    liquid_in,
    plot_of,
    programmed,
    second_now,
    seeds_in,
)
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import agro, breed, farm, storage, world
from src.models.agro import FieldAutomat
from src.models.event import EventKind
from src.models.farm import PlotState
from src.models.inventory import Item
from src.units import PERCENT, amount_float

WATER = "water"


# --- the programme -------------------------------------------------------------


def test_a_programme_reads_the_closed_list_and_refuses_the_rest(
    constants: Constants, catalog: Catalog
) -> None:
    fertilizer = catalog.recipes.of_class("fertilizer")[0]
    rows = agro.parse(
        constants,
        catalog,
        [
            {"do": "plow"},
            {"do": "sow", "culture": SPELT},
            {"do": "moisture", "target": 55},
            {"do": "feed", "goods": fertilizer, "stage": "leaf"},
            {"do": "weed", "days": 5},
            {"do": "thin"},
            {"do": "harvest"},
            {"do": "fallow", "days": 3},
        ],
    )
    assert [row["do"] for row in rows] == [
        "plow",
        "sow",
        "moisture",
        "feed",
        "weed",
        "thin",
        "harvest",
        "fallow",
    ]
    for wrong in (
        [],
        [{"do": "moisture", "target": 50}],
        [{"do": "dig"}],
        [{"do": "sow", "culture": "no_such_crop"}],
        [{"do": "sow"}],
        [{"do": "harvest"}, {"do": "feed", "goods": fertilizer, "stage": "ripe"}],
        [{"do": "harvest"}, {"do": "moisture", "target": 0}],
        [{"do": "fallow", "days": 0}],
        [{"do": "fallow", "days": constants[R.AGRO_DAYS_MAX] + 1}],
        [{"do": "harvest"}, {"do": "weed", "days": 1e12}],
        [{"do": "harvest"}, {"do": "feed", "goods": SPELT, "stage": "leaf"}],
        [{"do": "harvest"}] * (int(constants[R.AGRO_PROGRAM_STEPS]) + 1),
    ):
        with pytest.raises(agro.BadProgram):
            agro.parse(constants, catalog, wrong)


def test_a_setpoint_holds_its_whole_season() -> None:
    program = [
        {"do": "sow", "culture": SPELT},
        {"do": "moisture", "target": 55},
        {"do": "harvest"},
        {"do": "fallow", "days": 2},
        {"do": "moisture", "target": 40},
    ]
    #: One season runs from the harvest to the harvest round the circle, and
    #: both of its moisture lines hold wherever the cursor stands in it -- on
    #: the fallow, on the sowing, on the harvest -- in the programme's order:
    #: the later line is the one the machine keeps (`plan` takes the last).
    season = [program[4], program[1]]
    for cursor in (3, 0, 2):
        assert agro.in_force(program, cursor) == season
    #: With no harvest at all the programme is one season, read from its first
    #: line wherever the cursor stands.
    endless = [
        {"do": "moisture", "target": 40},
        {"do": "fallow", "days": 1},
        {"do": "moisture", "target": 50},
    ]
    for cursor in range(3):
        assert agro.in_force(endless, cursor) == [endless[0], endless[2]]


# --- the season ------------------------------------------------------------------


async def test_the_machine_ploughs_and_sows_out_of_the_seed_store(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    store = await chest(session, place.yard)
    await seeds_in(session, catalog, store, SPELT, 1000)
    plot = await plot_of(session, constants, place.body)
    t0 = second_now()
    row = await programmed(
        session,
        constants,
        catalog,
        place,
        [{"do": "plow"}, {"do": "sow", "culture": SPELT}, {"do": "harvest"}],
        [plot],
        t0,
        seeds=store,
        harvest=place.machine,
    )

    await agro.advance(session, constants, row, catalog=catalog, now=t0 + timedelta(minutes=1))
    await session.refresh(plot)
    assert plot.state is PlotState.PLOWED, "the plough goes first"
    plough = farm.plow_minutes(constants, plot)
    assert row.busy_until == t0 + timedelta(minutes=1 + plough), "held for the plough's norm"

    #: Still held: nothing else happens under the plough.
    await agro.advance(session, constants, row, catalog=catalog, now=t0 + timedelta(minutes=2))
    await session.refresh(plot)
    assert plot.state is PlotState.PLOWED

    after = t0 + timedelta(minutes=2 + plough)
    await agro.advance(session, constants, row, catalog=catalog, now=after)
    await session.refresh(plot)
    assert plot.state is PlotState.SOWN, "the cursor walked to the sowing and sowed"
    assert row.cursor == 1
    need = constants[R.FARM_SEED_RATE] * float(plot.area_m2)
    seed = catalog.plants.by_id(SPELT).seed
    assert await held_in(session, store, seed) == pytest.approx(1000 - need)
    assert await events_of(session, EventKind.PLOT_SOWN) == 1


async def test_the_machine_takes_up_a_plough_a_hand_paused_and_left(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A hand's plough paused with half done (D-277) is the hand's work done
    (D-339 p. 4): the machine finishes the strip and owes only the rest."""
    from farm_kit import _hands_free

    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    plot = await plot_of(session, constants, place.body)
    t0 = second_now()
    whole = farm.plow_minutes(constants, plot)
    await farm.plow(session, constants, place.body, plot, now=t0)
    await farm.plow_pause(
        session, constants, place.body, plot=plot, now=t0 + timedelta(minutes=whole / 2)
    )
    await _hands_free(session, place.body)
    row = await programmed(session, constants, catalog, place, [{"do": "plow"}], [plot], t0)
    moment = t0 + timedelta(minutes=whole / 2 + 1)
    await agro.advance(session, constants, row, catalog=catalog, now=moment)
    await session.refresh(plot)
    assert plot.state is PlotState.PLOWED
    assert row.busy_until == moment + timedelta(minutes=whole / 2), "only the half left"


async def test_a_feeding_by_hand_closes_the_stage_for_the_machine(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Fed in a stage is fed (D-339 p. 3, 4): a programme that names another
    fertilizer for the same stage does not feed a second time."""
    from farm_kit import _hands_free

    place = await field(session, constants, water="river")
    await liquid_in(session, place.yard, LUBRICANT, 100)
    first, second = catalog.recipes.of_class("fertilizer")[:2]
    store = await chest(session, place.yard)
    await goods_in(session, store, second, 100)
    t0 = second_now()
    plot = await growing(session, constants, catalog, place.body, t0)
    pocket = await world.body_container(session, place.body)
    await world.grant_item(session, pocket, first, amount=100, quality=60, origin="тест")
    await farm.feed(session, constants, catalog, place.body, plot, first, now=t0)
    await _hands_free(session, place.body)
    row = await programmed(
        session,
        constants,
        catalog,
        place,
        [{"do": "feed", "goods": second, "stage": "sprout"}, {"do": "harvest"}],
        [plot],
        t0,
        fertilizer=store,
    )
    await agro.advance(session, constants, row, catalog=catalog, now=t0 + timedelta(minutes=1))
    await session.refresh(plot)
    assert [given["goods"] for given in plot.fed["sprout"]] == [first]
    assert plot.overfed == 0
    assert await held_in(session, store, second) == 100


async def test_a_sowing_short_of_seeds_still_waters_what_it_sowed(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Seeds for one bed of two: the machine stands on the sowing with
    `no_seeds`, and the moisture written after the sowing still holds the bed
    it did sow -- a setpoint holds its whole season (D-339 p. 2)."""
    place = await field(session, constants, water="river")
    await liquid_in(session, place.yard, LUBRICANT, 1000)
    store = await chest(session, place.yard)
    one_bed = constants[R.FARM_SEED_RATE] * 40
    await seeds_in(session, catalog, store, SPELT, one_bed)
    first = await plot_of(session, constants, place.body, name="одно")
    second = await plot_of(session, constants, place.body, name="другое")
    for plot in (first, second):
        plot.state = PlotState.PLOWED
    await session.flush()
    t0 = second_now()
    target = constants[R.FARM_SOWN_MOISTURE] + constants[R.AGRO_MOISTURE_BAND] + 5
    row = await programmed(
        session,
        constants,
        catalog,
        place,
        [{"do": "sow", "culture": SPELT}, {"do": "moisture", "target": target}, {"do": "harvest"}],
        [first, second],
        t0,
        seeds=store,
    )
    await agro.advance(session, constants, row, catalog=catalog, now=t0 + timedelta(minutes=1))
    #: The sown bed is watered first -- it drinks -- and only then is the
    #: second sowing tried and found short.
    for _ in range(2):
        assert row.busy_until is not None
        await agro.advance(
            session, constants, row, catalog=catalog, now=row.busy_until + timedelta(minutes=1)
        )
    await session.refresh(first)
    await session.refresh(second)
    assert first.state is PlotState.SOWN and second.state is PlotState.PLOWED
    assert row.cursor == 0 and row.trouble == "no_seeds"
    assert float(first.moisture) == pytest.approx(target), "the sown bed is watered all the same"


async def test_a_sowing_with_no_plough_stands_and_says_why(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    store = await chest(session, place.yard)
    await seeds_in(session, catalog, store, SPELT, 1000)
    plot = await plot_of(session, constants, place.body)
    t0 = second_now()
    row = await programmed(
        session,
        constants,
        catalog,
        place,
        [{"do": "sow", "culture": SPELT}, {"do": "harvest"}],
        [plot],
        t0,
        seeds=store,
    )
    await agro.advance(session, constants, row, catalog=catalog, now=t0 + timedelta(minutes=1))
    await session.refresh(plot)
    assert plot.state is PlotState.IDLE
    assert row.trouble == "not_plowed"
    assert await events_of(session, EventKind.AGRO_STALLED) == 1


async def test_the_machine_holds_the_moisture_out_of_the_yard_vessels(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    tank = await liquid_in(session, place.yard, WATER, 500)
    t0 = second_now()
    plot = await growing(session, constants, catalog, place.body, t0)
    target = constants[R.FARM_SOWN_MOISTURE] + constants[R.AGRO_MOISTURE_BAND] + 5
    row = await programmed(
        session,
        constants,
        catalog,
        place,
        [{"do": "moisture", "target": target}, {"do": "harvest"}],
        [plot],
        t0,
    )
    await agro.advance(session, constants, row, catalog=catalog, now=t0 + timedelta(minutes=1))
    await session.refresh(plot)
    await session.refresh(tank)
    assert float(plot.moisture) == pytest.approx(target)
    #: The hand's litres to the drop: the moisture of the bed a minute after the sowing.
    assert amount_float(tank.amount) < 500
    assert row.busy_until is not None

    #: Back at the setpoint, nothing is due until it falls the band below.
    watered = amount_float(tank.amount)
    later = row.busy_until + timedelta(minutes=1)
    await agro.advance(session, constants, row, catalog=catalog, now=later)
    await session.refresh(tank)
    assert amount_float(tank.amount) == watered


async def test_by_a_river_the_water_costs_nothing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    place = await field(session, constants, water="river")
    await liquid_in(session, place.yard, LUBRICANT, 100)
    t0 = second_now()
    plot = await growing(session, constants, catalog, place.body, t0)
    target = constants[R.FARM_SOWN_MOISTURE] + constants[R.AGRO_MOISTURE_BAND] + 5
    row = await programmed(
        session,
        constants,
        catalog,
        place,
        [{"do": "moisture", "target": target}, {"do": "harvest"}],
        [plot],
        t0,
    )
    await agro.advance(session, constants, row, catalog=catalog, now=t0 + timedelta(minutes=1))
    await session.refresh(plot)
    assert float(plot.moisture) == pytest.approx(target)
    assert row.trouble is None


async def test_the_machine_feeds_in_its_stage_and_once(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    fertilizer = catalog.recipes.of_class("fertilizer")[0]
    store = await chest(session, place.yard)
    await goods_in(session, store, fertilizer, 100)
    t0 = second_now()
    plot = await growing(session, constants, catalog, place.body, t0)
    row = await programmed(
        session,
        constants,
        catalog,
        place,
        [{"do": "feed", "goods": fertilizer, "stage": "sprout"}, {"do": "harvest"}],
        [plot],
        t0,
        fertilizer=store,
    )
    await agro.advance(session, constants, row, catalog=catalog, now=t0 + timedelta(minutes=1))
    await session.refresh(plot)
    assert [given["goods"] for given in plot.fed["sprout"]] == [fertilizer]
    dose = constants[R.FARM_FERTILIZER_PER_M2] * float(plot.area_m2)
    assert await held_in(session, store, fertilizer) == pytest.approx(100 - dose)

    #: Fed in this stage: a second feeding would be the machine's overfeeding.
    assert row.busy_until is not None
    await agro.advance(
        session, constants, row, catalog=catalog, now=row.busy_until + timedelta(minutes=1)
    )
    await session.refresh(plot)
    assert len(plot.fed["sprout"]) == 1
    assert plot.overfed == 0


async def test_a_weeding_by_hand_restarts_the_machine_count(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    from farm_kit import _hands_free

    place = await field(session, constants, water="river")
    await liquid_in(session, place.yard, LUBRICANT, 1000)
    t0 = second_now()
    plot = await growing(session, constants, catalog, place.body, t0)
    row = await programmed(
        session,
        constants,
        catalog,
        place,
        [{"do": "weed", "days": 1}, {"do": "harvest"}],
        [plot],
        t0,
    )
    day = timedelta(hours=farm.day_hours(constants))

    await agro.advance(session, constants, row, catalog=catalog, now=t0 + day / 2)
    await session.refresh(plot)
    assert plot.weeded_at is None, "a day has not passed since the sowing"

    #: The hand weeds before the machine's day is up.
    hand = t0 + day * 0.75
    await farm.weed(session, constants, catalog, place.body, plot, now=hand)
    await _hands_free(session, place.body)
    await agro.advance(
        session, constants, row, catalog=catalog, now=t0 + day + timedelta(minutes=1)
    )
    await session.refresh(plot)
    assert plot.weeded_at == hand, "the hand's weeding counts: no second one a quarter day later"

    await agro.advance(
        session, constants, row, catalog=catalog, now=hand + day + timedelta(minutes=1)
    )
    await session.refresh(plot)
    assert plot.weeded_at == hand + day + timedelta(minutes=1)


async def test_the_machine_reaps_its_share_under_its_ceiling_without_selection(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    place = await field(session, constants, water="river", fertility=90)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    t0 = second_now()
    plot = await growing(session, constants, catalog, place.body, t0)
    plot.growth = 100
    await session.flush()
    plant = catalog.plants.by_id(SPELT)
    cultivar = await breed.landrace(session, catalog, SPELT)
    moment = t0 + timedelta(minutes=1)
    seen = farm.peek(
        constants,
        plant,
        farm.signs_of(plant, cultivar),
        place.node,
        await world.epoch(session),
        plot,
        moment,
    )
    hand = farm.crop_of(constants, plot, plant, farm.signs_of(plant, cultivar), seen)

    row = await programmed(
        session, constants, catalog, place, [{"do": "harvest"}], [plot], t0, harvest=place.machine
    )
    await agro.advance(session, constants, row, catalog=catalog, now=moment)
    await session.refresh(plot)
    assert plot.state is PlotState.IDLE

    share = constants[R.AGRO_YIELD_SHARE] / PERCENT
    bunker = await storage.inside(session, place.machine, create=False)
    assert bunker is not None
    reaped = (
        (await session.execute(select(Item).where(Item.container_id == bunker.id))).scalars().all()
    )
    goods = next(thing for thing in reaped if thing.type_key == plant.gives)
    seeds = next(thing for thing in reaped if thing.type_key == plant.seed)
    assert amount_float(goods.amount) == pytest.approx(hand.goods * share, abs=0.01)
    assert float(goods.quality) <= constants[R.AGRO_QUALITY_CAP]
    assert float(goods.quality) < hand.quality, "the land alone would have given better"
    assert float(seeds.vigor) < PERCENT, "no selection: the fund degrades"
    assert await events_of(session, EventKind.PLOT_HARVESTED) == 1


async def test_a_full_store_leaves_the_ripe_bed_waiting(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    place = await field(session, constants, water="river")
    await liquid_in(session, place.yard, LUBRICANT, 100)
    rack = await chest(session, place.yard, "rack")
    ingot = "iron_ingot"
    limit = storage.capacity(catalog, "rack") or 0.0
    await goods_in(session, rack, ingot, limit / catalog.recipes.mass_of(ingot))
    t0 = second_now()
    plot = await growing(session, constants, catalog, place.body, t0)
    plot.growth = 100
    await session.flush()
    row = await programmed(
        session, constants, catalog, place, [{"do": "harvest"}], [plot], t0, harvest=rack
    )
    for minute in (1, 2):
        await agro.advance(
            session, constants, row, catalog=catalog, now=t0 + timedelta(minutes=minute)
        )
    await session.refresh(plot)
    assert plot.state is PlotState.SOWN, "not a grain reaped past the capacity"
    assert row.trouble == "store_full"
    assert await events_of(session, EventKind.AGRO_STALLED) == 1, "told once, not every minute"


async def test_without_lubricant_the_machine_stands(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    place = await field(session, constants)
    plot = await plot_of(session, constants, place.body)
    t0 = second_now()
    row = await programmed(session, constants, catalog, place, [{"do": "plow"}], [plot], t0)
    await agro.advance(session, constants, row, catalog=catalog, now=t0 + timedelta(minutes=5))
    await session.refresh(plot)
    assert plot.state is PlotState.IDLE
    assert row.trouble == "no_lube"


async def test_without_energy_the_machine_stands(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    place = await field(session, constants, stored_energy=0)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    plot = await plot_of(session, constants, place.body)
    t0 = second_now()
    row = await programmed(session, constants, catalog, place, [{"do": "plow"}], [plot], t0)
    await agro.advance(session, constants, row, catalog=catalog, now=t0 + timedelta(minutes=5))
    await session.refresh(plot)
    assert plot.state is PlotState.IDLE
    assert row.trouble == "no_power"


async def test_a_fallow_waits_its_days(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 1000)
    plot = await plot_of(session, constants, place.body)
    t0 = second_now()
    row = await programmed(
        session,
        constants,
        catalog,
        place,
        [{"do": "fallow", "days": 1}, {"do": "plow"}],
        [plot],
        t0,
    )
    day = timedelta(hours=farm.day_hours(constants))
    await agro.advance(session, constants, row, catalog=catalog, now=t0 + day / 2)
    await session.refresh(plot)
    assert plot.state is PlotState.IDLE
    await agro.advance(
        session, constants, row, catalog=catalog, now=t0 + day + timedelta(minutes=1)
    )
    await session.refresh(plot)
    assert plot.state is PlotState.PLOWED


async def test_a_worn_out_machine_drops_its_bunker_in_the_yard(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    place = await field(session, constants)
    plot = await plot_of(session, constants, place.body)
    t0 = second_now()
    row = await programmed(session, constants, catalog, place, [{"do": "plow"}], [plot], t0)
    grain = await goods_in(session, place.machine, catalog.plants.by_id(SPELT).gives, 10)
    place.machine.condition = 1
    await session.flush()
    await agro.advance(session, constants, row, catalog=catalog, now=t0 + timedelta(days=30))
    assert await session.get(Item, place.machine.id) is None
    assert await session.scalar(select(func.count()).select_from(FieldAutomat)) == 0
    await session.refresh(grain)
    assert grain.container_id == place.yard.id
