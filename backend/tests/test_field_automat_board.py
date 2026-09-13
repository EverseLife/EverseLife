# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The field automaton's board and its tick (D-339): the plots and storages
it may be given, a programme changed or set again, a machine moved to another
yard, a machine whose programme broke, the owner's right to the node, and the
energy a tick promises its machines.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import agro_kit
from agro_kit import (
    LUBRICANT,
    chest,
    events_of,
    field,
    growing,
    liquid_in,
    plot_of,
    programmed,
    second_now,
)
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import agro, automat, energy, farm, world
from src.models.event import EventKind
from src.models.farm import PlotState
from src.units import ENERGY_PER_TARIFF_UNIT, MONEY_SCALE

WATER = "water"


# --- the board -------------------------------------------------------------------


async def test_the_board_takes_only_the_owners_big_enough_free_plots(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    place = await field(session, constants)
    small = await plot_of(session, constants, place.body, constants[R.AGRO_PLOT_MIN_AREA] - 1)
    t0 = second_now()
    with pytest.raises(agro.BadPlot):
        await programmed(session, constants, catalog, place, [{"do": "plow"}], [small], t0)

    too_many = [
        await plot_of(session, constants, place.body, name=f"поле {n}")
        for n in range(int(constants[R.AGRO_PLOTS_MAX]) + 1)
    ]
    with pytest.raises(agro.BadPlot):
        await programmed(session, constants, catalog, place, [{"do": "plow"}], too_many, t0)

    await programmed(session, constants, catalog, place, [{"do": "plow"}], too_many[:1], t0)
    second = await world.grant_item(
        session, place.yard, "field_automaton", quality=70, origin="тест"
    )
    with pytest.raises(agro.BadPlot):
        await agro.program(
            session,
            constants,
            catalog,
            place.body,
            second,
            steps=[{"do": "plow"}],
            plots=[str(too_many[0].id)],
            seeds=None,
            fertilizer=None,
            harvest=None,
            now=t0,
        )


async def test_the_board_names_only_dry_storages_of_its_own_yard(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    place = await field(session, constants)
    plot = await plot_of(session, constants, place.body)
    tank = await world.grant_item(session, place.yard, "fuel_tank", quality=60, origin="тест")
    t0 = second_now()
    with pytest.raises(agro.BadStore):
        await programmed(
            session, constants, catalog, place, [{"do": "plow"}], [plot], t0, seeds=tank
        )
    elsewhere = await field(session, constants)
    far = await chest(session, elsewhere.yard)
    with pytest.raises(agro.BadStore):
        await programmed(
            session, constants, catalog, place, [{"do": "plow"}], [plot], t0, harvest=far
        )


async def test_a_changed_programme_starts_from_its_first_line(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    plot = await plot_of(session, constants, place.body)
    t0 = second_now()
    steps = [{"do": "fallow", "days": 5}, {"do": "plow"}]
    row = await programmed(session, constants, catalog, place, steps, [plot], t0)
    row.cursor = 1
    await session.flush()
    #: The same programme again: the cursor stays.
    await programmed(session, constants, catalog, place, steps, [plot], t0 + timedelta(minutes=1))
    assert row.cursor == 1
    await programmed(
        session, constants, catalog, place, [{"do": "plow"}], [plot], t0 + timedelta(minutes=2)
    )
    assert row.cursor == 0


async def test_a_broken_machine_is_passed_over_and_can_still_be_stopped(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One machine whose programme the vault has since broken must not stop
    the world's fields, and its owner must still be able to take it off."""
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    t0 = second_now()
    broken_plot = await plot_of(session, constants, place.body, name="broken")
    broken = await programmed(
        session, constants, catalog, place, [{"do": "plow"}], [broken_plot], t0
    )
    #: As if the vault dropped the culture after the programme was set.
    broken.program = [{"do": "sow", "culture": "no_such_crop"}]
    broken_plot.state = PlotState.PLOWED
    other = await field(session, constants)
    await liquid_in(session, other.yard, LUBRICANT, 100)
    good_plot = await plot_of(session, constants, other.body, name="good")
    await programmed(session, constants, catalog, other, [{"do": "plow"}], [good_plot], t0)
    await session.flush()

    done = await agro.tick_fields(session, constants, now=t0 + timedelta(minutes=1))
    await session.refresh(good_plot)
    assert good_plot.state is PlotState.PLOWED, "the other machine worked"
    assert done == 1

    #: The failed savepoint expired what it had touched: a command loads afresh.
    await session.refresh(place.machine)
    await session.refresh(place.body)
    assert await agro.stop(session, constants, catalog, place.body, place.machine) is True
    assert await agro.of_item(session, place.machine) is None


async def test_a_machine_put_up_in_another_yard_works_there(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    place = await field(session, constants)
    first = await plot_of(session, constants, place.body, name="first")
    t0 = second_now()
    row = await programmed(session, constants, catalog, place, [{"do": "plow"}], [first], t0)
    elsewhere = await field(session, constants)
    await liquid_in(session, elsewhere.yard, LUBRICANT, 100)
    place.machine.container_id = elsewhere.yard.id
    await session.flush()
    moved = agro_kit.Field(
        elsewhere.node, elsewhere.yard, place.identity, place.body, place.machine
    )
    moved.body.node_id = elsewhere.node.id
    elsewhere.node.owner_identity_id = place.identity.id
    await session.flush()
    there = await plot_of(session, constants, place.body, name="there")
    await programmed(
        session, constants, catalog, moved, [{"do": "plow"}], [there], t0 + timedelta(minutes=1)
    )
    assert row.node_id == elsewhere.node.id
    await agro.advance(session, constants, row, catalog=catalog, now=t0 + timedelta(minutes=2))
    await session.refresh(there)
    assert there.state is PlotState.PLOWED


# --- the tick's promises ------------------------------------------------------------


async def test_two_machines_on_one_pool_do_not_both_promise_its_last_hour(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The pool holds one machine's minute: the tick promises it once, and the
    second machine stands with `no_power` instead of working on credit."""
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    first = await plot_of(session, constants, place.body, name="first")
    moment = second_now()
    row_a = await programmed(session, constants, catalog, place, [{"do": "plow"}], [first], moment)
    second_machine = await world.grant_item(
        session, place.yard, "field_automaton", quality=70, origin="тест"
    )
    second = await plot_of(session, constants, place.body, name="second")
    row_b = await agro.program(
        session,
        constants,
        catalog,
        place.body,
        second_machine,
        steps=[{"do": "plow"}],
        plots=[str(second.id)],
        seeds=None,
        fertilizer=None,
        harvest=None,
        now=moment,
    )
    pool = await energy.pool_of(session, constants, place.node)
    assert pool is not None
    one_minute = constants[R.AGRO_ENERGY_PER_HOUR] / 60
    pool.stored = one_minute * 1.5
    await session.flush()

    done = await agro.tick_fields(session, constants, now=moment + timedelta(minutes=1))
    await session.refresh(row_a)
    await session.refresh(row_b)
    assert done == 1
    assert sorted([row_a.trouble or "", row_b.trouble or ""]) == ["", "no_power"]


async def test_an_owner_who_cannot_pay_gets_no_work(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Whoever burns pays (D-135), and whoever cannot pay does not burn: the
    machine does not plough on the promise of a bill its owner cannot meet."""
    place = await field(session, constants, funded=False)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    plot = await plot_of(session, constants, place.body)
    moment = second_now()
    row = await programmed(session, constants, catalog, place, [{"do": "plow"}], [plot], moment)
    for minute in (1, 2):
        await agro.tick_fields(session, constants, now=moment + timedelta(minutes=minute))
    await session.refresh(plot)
    await session.refresh(row)
    assert plot.state is PlotState.IDLE
    assert row.trouble == "no_power"
    assert await events_of(session, EventKind.AGRO_STALLED) == 1


async def test_land_sold_from_under_the_machine_stops_it(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    plot = await plot_of(session, constants, place.body)
    moment = second_now()
    row = await programmed(session, constants, catalog, place, [{"do": "plow"}], [plot], moment)
    buyer = await world.create_identity(session, "Покупатель")
    place.node.owner_identity_id = buyer.id
    await session.flush()
    await agro.advance(session, constants, row, catalog=catalog, now=moment + timedelta(minutes=1))
    await session.refresh(plot)
    assert plot.state is PlotState.IDLE
    assert row.trouble == "not_entitled"


async def test_a_broken_machine_stands_with_a_fault_and_its_clock_moves(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A broken machine does not run up a debt of hours to be paid at once
    when mended: its stamp moves on, and the owner reads why it stands."""
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    plot = await plot_of(session, constants, place.body)
    moment = second_now()
    row = await programmed(session, constants, catalog, place, [{"do": "plow"}], [plot], moment)
    row.program = [{"do": "sow", "culture": "no_such_crop"}]
    plot.state = PlotState.PLOWED
    await session.flush()
    later = moment + timedelta(minutes=3)
    await agro.tick_fields(session, constants, now=later)
    await session.refresh(row)
    assert row.trouble == "fault"
    assert row.counted_at == later


async def test_a_small_bed_is_watered_when_a_big_one_cannot_be(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A shortage is the resource's for the size that failed, not the work's:
    water enough for the small bed only must not leave it dry behind the big."""
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 1000)
    moment = second_now()
    big = await growing(session, constants, catalog, place.body, moment, area=400)
    small = await growing(session, constants, catalog, place.body, moment, area=40)
    target = constants[R.FARM_SOWN_MOISTURE] + constants[R.AGRO_MOISTURE_BAND] + 5
    small_litres = farm.water_litres(constants, small, constants[R.FARM_SOWN_MOISTURE], target)
    await liquid_in(session, place.yard, WATER, small_litres * 1.5)
    row = await programmed(
        session,
        constants,
        catalog,
        place,
        [{"do": "moisture", "target": target}, {"do": "harvest"}],
        [big, small],
        moment,
    )
    await agro.advance(session, constants, row, catalog=catalog, now=moment + timedelta(minutes=1))
    await session.refresh(small)
    await session.refresh(big)
    assert float(small.moisture) == pytest.approx(target)
    assert float(big.moisture) < target
    assert row.trouble == "no_water"


async def test_a_short_draw_is_a_debt_paid_before_the_next_action(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bench emptied the pool between the promise and the draw: the minute
    went on credit, the machine owes it, and it does not act again until the
    debt is promised with the minute's own energy."""
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    first = await plot_of(session, constants, place.body, name="first")
    second = await plot_of(session, constants, place.body, name="second")
    moment = second_now()
    row = await programmed(
        session, constants, catalog, place, [{"do": "plow"}], [first, second], moment
    )
    real = automat.draw_energy

    async def nothing(*args, **kwargs):
        return 0.0

    monkeypatch.setattr(automat, "draw_energy", nothing)
    await agro.tick_fields(session, constants, now=moment + timedelta(minutes=1))
    await session.refresh(row)
    assert row.trouble == "no_power"
    owed = float(row.energy_owed)
    assert owed > 0

    #: The pool holds the minute but not the debt with it: no action.
    monkeypatch.setattr(automat, "draw_energy", real)
    pool = await energy.pool_of(session, constants, place.node)
    assert pool is not None
    pool.stored = owed * 0.5
    row.busy_until = None
    await session.flush()
    done = await agro.tick_fields(session, constants, now=moment + timedelta(minutes=2))
    assert done == 0

    #: With the debt covered, it pays and works.
    pool.stored = 10_000
    await session.flush()
    done = await agro.tick_fields(session, constants, now=moment + timedelta(minutes=3))
    await session.refresh(row)
    assert done == 1
    assert float(row.energy_owed) == 0


async def test_a_tariff_too_small_to_price_one_machine_still_prices_three(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The promise is priced as the bill is -- one sum per pool and owner -- so
    three machines whose minutes each round to nothing do not all run free on
    an empty purse when their sum does not."""
    place = await field(session, constants, funded=False)
    await liquid_in(session, place.yard, LUBRICANT, 1000)
    pool = await energy.pool_of(session, constants, place.node)
    assert pool is not None
    minute = constants[R.AGRO_ENERGY_PER_HOUR] / 60
    #: A tariff at which one minute costs a little under half a minor unit.
    pool.tariff = 0.45 / MONEY_SCALE * ENERGY_PER_TARIFF_UNIT / minute
    moment = second_now()
    rows = []
    for index in range(3):
        machine = await world.grant_item(
            session, place.yard, "field_automaton", quality=70, origin="тест"
        )
        plot = await plot_of(session, constants, place.body, name=f"поле {index}")
        rows.append(
            await agro.program(
                session,
                constants,
                catalog,
                place.body,
                machine,
                steps=[{"do": "plow"}],
                plots=[str(plot.id)],
                seeds=None,
                fertilizer=None,
                harvest=None,
                now=moment,
            )
        )
    await session.flush()
    done = await agro.tick_fields(session, constants, now=moment + timedelta(minutes=1))
    assert done < 3, "the sum of the minutes is priced, and the purse is empty"
