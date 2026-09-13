# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The field automaton on a short supply (D-339 p. 8, D-253): the family's
demand is counted before the pass, so a short pool gives every machine on it
the same share of its hours; a machine that will take nothing is not counted;
an action's clock runs only for the hours the machine had power; and a sliver
of power the lubricant cannot be measured for starts nothing.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from agro_kit import (
    LUBRICANT,
    events_of,
    field,
    liquid_in,
    plot_of,
    programmed,
    second_now,
)
from automat_kit import IRON, NAILS, _learn
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import agro, automat, energy, world
from src.engine.automat import bill as energy_bill
from src.models.agro import FieldAutomatPlot
from src.models.event import EventKind
from src.models.farm import PlotState
from src.units import amount_float


async def test_an_automat_and_a_field_automaton_on_a_short_pool_get_the_same_share(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An automat and a field automaton on one pool holding one and a half
    machine-minutes. The family's demand is counted before the pass, so the
    automat -- first in the pass -- does not take a whole minute and leave the
    field half: each gets three quarters of its minute, and the pool is spent."""
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    plot = await plot_of(session, constants, place.body)
    moment = second_now()
    row = await programmed(session, constants, catalog, place, [{"do": "plow"}], [plot], moment)
    await _learn(session, place.identity, NAILS)
    await world.grant_item(session, place.yard, IRON, amount=1000, quality=60, origin="тест")
    station = await world.grant_item(session, place.yard, "auto_station", quality=70, origin="тест")
    await automat.program(session, constants, catalog, place.body, station, NAILS, now=moment)
    pool = await energy.pool_of(session, constants, place.node)
    assert pool is not None
    minute = constants[R.AGRO_ENERGY_PER_HOUR] / 60
    assert constants[R.AUTO_ENERGY_PER_HOUR] / 60 == pytest.approx(minute)
    pool.stored = Decimal(str(minute * 1.5))
    await session.flush()

    drawn = energy_bill.pay
    billed: list[float] = []

    async def seen(session_, constants_, bills, *, now):
        billed.extend(one.hours * one.rate for one in bills)
        return await drawn(session_, constants_, bills, now=now)

    monkeypatch.setattr(energy_bill, "pay", seen)
    result = await agro.tick_machines(session, constants, now=moment + timedelta(minutes=1))
    await session.refresh(row)
    await session.refresh(plot)
    await session.refresh(pool)
    assert sorted(billed) == pytest.approx([minute * 0.75, minute * 0.75], abs=0.002)
    assert result.actions == 1, "the field automaton ploughs on its share"
    assert plot.state is PlotState.PLOWED
    assert row.trouble == "no_power"
    assert float(pool.stored) == pytest.approx(0, abs=0.001)


async def test_a_field_automaton_on_a_thin_grid_beside_an_automat_goes_slower_and_does_not_stall(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A grid that brings a third of one machine-minute a tick, an automat on it
    too. Neither machine starves: each is on for its share of every tick, the
    field automaton ploughs on the first and its clock for the ploughing runs
    only for the hours it had. It is slowed, not stopped: its window says
    `no_power`, and the journal, which tells why a machine stands, is not told."""
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    plot = await plot_of(session, constants, place.body)
    moment = second_now()
    row = await programmed(session, constants, catalog, place, [{"do": "plow"}], [plot], moment)
    await _learn(session, place.identity, NAILS)
    await world.grant_item(session, place.yard, IRON, amount=1000, quality=60, origin="тест")
    station = await world.grant_item(session, place.yard, "auto_station", quality=70, origin="тест")
    await automat.program(session, constants, catalog, place.body, station, NAILS, now=moment)
    pool = await energy.pool_of(session, constants, place.node)
    assert pool is not None
    pool.stored = Decimal(0)
    await session.flush()
    step = timedelta(minutes=constants[R.TIME_TICK])
    inflow = constants[R.AGRO_ENERGY_PER_HOUR] * constants[R.TIME_TICK] / 60 / 3

    now = moment
    busy = []
    for _ in range(6):
        pool.stored = Decimal(str(round(float(pool.stored) + inflow, 3)))
        await session.flush()
        now += step
        await agro.tick_machines(session, constants, now=now)
        await session.refresh(pool)
        await session.refresh(plot)
        await session.refresh(row)
        assert float(pool.stored) >= 0
        busy.append(row.busy_until)
    assert plot.state is PlotState.PLOWED, "the field automaton ploughed on its share"
    assert row.trouble == "no_power"
    assert await events_of(session, EventKind.AGRO_STALLED) == 0, "slowed, not stood"
    #: The ploughing stretches: every tick after it moves the end by the tick's
    #: unpowered part.
    gaps = [
        (later - earlier).total_seconds()
        for earlier, later in zip(busy[:-1], busy[1:], strict=True)
    ]
    assert all(gap > 0 for gap in gaps), "the clock ran only for the hours it had"


async def test_a_busy_machine_without_power_keeps_its_action_waiting(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The machine's clock runs only while it has power: a tick with none moves
    the end of the action it is busy with by the whole tick, a tick with half
    its minute by half (D-339 p. 8)."""
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    plot = await plot_of(session, constants, place.body)
    moment = second_now()
    row = await programmed(session, constants, catalog, place, [{"do": "plow"}], [plot], moment)
    step = timedelta(minutes=constants[R.TIME_TICK])
    await agro.tick_machines(session, constants, now=moment + step)
    await session.refresh(row)
    assert row.busy_until is not None
    ends = row.busy_until
    pool = await energy.pool_of(session, constants, place.node)
    assert pool is not None

    pool.stored = Decimal(0)
    await session.flush()
    await agro.tick_machines(session, constants, now=moment + 2 * step)
    await session.refresh(row)
    assert row.busy_until == ends + step, "a tick with no power does not count"

    minute = constants[R.AGRO_ENERGY_PER_HOUR] * constants[R.TIME_TICK] / 60
    pool.stored = Decimal(str(round(minute / 2, 3)))
    await session.flush()
    await agro.tick_machines(session, constants, now=moment + 3 * step)
    await session.refresh(row)
    assert (row.busy_until - (ends + step)).total_seconds() == pytest.approx(
        step.total_seconds() / 2, abs=1
    ), "half a tick of power counts half"


async def test_a_machine_that_will_take_nothing_does_not_shrink_the_others_share(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
) -> None:
    """Two field automatons on a pool holding one machine-minute, one of them
    with its plots taken away: it draws nothing, so it is not counted in the
    demand, and the other takes its whole minute."""
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    first = await plot_of(session, constants, place.body, name="первое")
    moment = second_now()
    row = await programmed(session, constants, catalog, place, [{"do": "plow"}], [first], moment)
    idle_machine = await world.grant_item(
        session, place.yard, "field_automaton", quality=70, origin="тест"
    )
    second = await plot_of(session, constants, place.body, name="второе")
    idle = await agro.program(
        session,
        constants,
        catalog,
        place.body,
        idle_machine,
        steps=[{"do": "plow"}],
        plots=[str(second.id)],
        seeds=None,
        fertilizer=None,
        harvest=None,
        now=moment,
    )
    await session.execute(delete(FieldAutomatPlot).where(FieldAutomatPlot.automat_id == idle.id))
    pool = await energy.pool_of(session, constants, place.node)
    assert pool is not None
    pool.stored = Decimal(str(constants[R.AGRO_ENERGY_PER_HOUR] * constants[R.TIME_TICK] / 60))
    await session.flush()

    await agro.tick_machines(
        session, constants, now=moment + timedelta(minutes=constants[R.TIME_TICK])
    )
    await session.refresh(row)
    await session.refresh(pool)
    assert row.trouble is None, "its whole minute"
    assert float(pool.stored) == pytest.approx(0, abs=0.001)


async def test_a_sliver_of_power_starts_no_action(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A pool of one grid step: hours whose lubricant cannot be measured are no
    hours -- nothing is billed and nothing is ploughed on them."""
    place = await field(session, constants)
    oil = await liquid_in(session, place.yard, LUBRICANT, 100)
    plot = await plot_of(session, constants, place.body)
    moment = second_now()
    row = await programmed(session, constants, catalog, place, [{"do": "plow"}], [plot], moment)
    pool = await energy.pool_of(session, constants, place.node)
    assert pool is not None
    pool.stored = Decimal("0.001")
    await session.flush()

    result = await agro.tick_machines(
        session, constants, now=moment + timedelta(minutes=constants[R.TIME_TICK])
    )
    await session.refresh(row)
    await session.refresh(plot)
    await session.refresh(pool)
    await session.refresh(oil)
    assert result.actions == 0
    assert plot.state is PlotState.IDLE
    assert row.trouble == "no_power"
    assert float(pool.stored) == pytest.approx(0.001)
    assert amount_float(oil.amount) == pytest.approx(100)
