# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What the field automaton tells its owner (D-339 p. 11): its window hears
every change of word, and the journal is told a word when the machine stood
for it -- each word once a Terran day, however the supply comes and goes or the
words take turns. Seconds too short to measure are no stall at all.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from agro_kit import (
    LUBRICANT,
    SPELT,
    events_of,
    field,
    liquid_in,
    plot_of,
    programmed,
    second_now,
)
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import agro, energy
from src.models.event import EventKind


async def test_a_supply_that_comes_and_goes_tells_the_journal_once_a_day(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A pool empty one tick and full the next: the machine stands, then works,
    then stands again. Its window hears every change; the journal is told the
    stall once, not every other tick (D-339 p. 11)."""
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    plot = await plot_of(session, constants, place.body)
    moment = second_now()
    row = await programmed(session, constants, catalog, place, [{"do": "plow"}], [plot], moment)
    pool = await energy.pool_of(session, constants, place.node)
    assert pool is not None
    step = timedelta(minutes=constants[R.TIME_TICK])
    now = moment
    words = []
    for tick in range(10):
        pool.stored = Decimal(0) if tick % 2 == 0 else Decimal(1000)
        await session.flush()
        now += step
        await agro.tick_machines(session, constants, now=now)
        await session.refresh(row)
        words.append(row.trouble)
    assert words.count("no_power") == 5 and words.count(None) == 5
    assert await events_of(session, EventKind.AGRO_STALLED) == 1, "told once a day"


async def test_a_tick_seconds_after_a_command_is_no_stall(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A tick two seconds after the programme is set, on a full pool: seconds
    whose lubricant the grid cannot show carry over, and the machine is not
    told it had no power."""
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    plot = await plot_of(session, constants, place.body)
    moment = second_now()
    row = await programmed(session, constants, catalog, place, [{"do": "plow"}], [plot], moment)
    await agro.tick_machines(session, constants, now=moment + timedelta(seconds=2))
    await session.refresh(row)
    assert row.trouble is None
    assert row.counted_at == moment, "the seconds carry over"
    assert await events_of(session, EventKind.AGRO_STALLED) == 0


async def test_two_words_taking_turns_are_each_told_once_a_day(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
) -> None:
    """A pool empty one tick and full the next, and a sowing the unploughed bed
    refuses: the words take turns -- no power, not ploughed -- and each is
    told once, not every tick."""
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    plot = await plot_of(session, constants, place.body)
    moment = second_now()
    row = await programmed(
        session, constants, catalog, place, [{"do": "sow", "culture": SPELT}], [plot], moment
    )
    pool = await energy.pool_of(session, constants, place.node)
    assert pool is not None
    step = timedelta(minutes=constants[R.TIME_TICK])
    now = moment
    words = set()
    for tick in range(8):
        pool.stored = Decimal(0) if tick % 2 == 0 else Decimal(1000)
        await session.flush()
        now += step
        await agro.tick_machines(session, constants, now=now)
        await session.refresh(row)
        words.add(row.trouble)
    assert {"no_power", "not_plowed"} <= words
    assert await events_of(session, EventKind.AGRO_STALLED) == 2, "each word once"


async def test_a_stall_that_comes_back_days_later_is_told_again(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
) -> None:
    """Told once, the power comes back; three days on the pool runs thin -- a
    slowed tick first, then a stop. The stop is news again: a day has passed."""
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 1000)
    plot = await plot_of(session, constants, place.body)
    moment = second_now()
    row = await programmed(session, constants, catalog, place, [{"do": "plow"}], [plot], moment)
    pool = await energy.pool_of(session, constants, place.node)
    assert pool is not None
    step = timedelta(minutes=constants[R.TIME_TICK])
    minute = constants[R.AGRO_ENERGY_PER_HOUR] * constants[R.TIME_TICK] / 60

    pool.stored = Decimal(0)
    await session.flush()
    await agro.tick_machines(session, constants, now=moment + step)
    assert await events_of(session, EventKind.AGRO_STALLED) == 1

    later = moment + timedelta(days=3)
    row.counted_at = later - step
    pool.stored = Decimal(str(round(minute / 2, 3)))
    await session.flush()
    await agro.tick_machines(session, constants, now=later)
    await session.refresh(row)
    assert row.trouble == "no_power"
    assert await events_of(session, EventKind.AGRO_STALLED) == 1, "slowed, not stood"

    pool.stored = Decimal(0)
    await session.flush()
    await agro.tick_machines(session, constants, now=later + step)
    assert await events_of(session, EventKind.AGRO_STALLED) == 2, "a stop three days on is news"
