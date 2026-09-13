# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A field automaton whose programme is taken off (D-339 p. 8): busy with an
action, it keeps its row and the minutes it owes, so a programme set again
waits them out; the tick passes the stopped row over, finds it by the mark the
database keeps, and sweeps it once the thing is gone.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

from agro_kit import (
    LUBRICANT,
    field,
    liquid_in,
    plot_of,
    programmed,
    second_now,
)
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import agro
from src.models.agro import FieldAutomat
from src.models.farm import PlotState


async def test_taking_the_programme_off_a_busy_machine_does_not_wipe_its_minutes(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The machine ploughs one strip and is busy for the ploughing's minutes.
    Its programme taken off and set again at once, it still owes them: the
    second strip waits, rather than being ploughed on time the stop wiped."""
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    first = await plot_of(session, constants, place.body, name="первое")
    second = await plot_of(session, constants, place.body, name="второе")
    moment = second_now()
    step = timedelta(minutes=constants[R.TIME_TICK])
    steps = [{"do": "plow"}]
    row = await programmed(session, constants, catalog, place, steps, [first, second], moment)
    await agro.tick_machines(session, constants, now=moment + step)
    await session.refresh(row)
    assert row.busy_until is not None and row.busy_until > moment + step
    owed = row.busy_until

    assert await agro.stop(
        session, constants, catalog, place.body, place.machine, now=moment + step
    )
    kept = await agro.of_item(session, place.machine)
    assert kept is not None and kept.busy_until == owed and not kept.program
    again = await programmed(
        session, constants, catalog, place, steps, [first, second], moment + step
    )
    assert again.busy_until == owed, "the programme set again inherits the minutes owed"

    await agro.tick_machines(session, constants, now=moment + 2 * step)
    await session.refresh(second)
    assert second.state is PlotState.IDLE, "the second strip waits out the first's ploughing"


async def test_a_stopped_machine_is_passed_over_and_its_row_goes_with_the_thing(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
) -> None:
    """Stopped while busy, the machine keeps its row for the minutes it owes,
    and the tick does not work it. Taken apart meanwhile, it leaves nothing
    behind: the tick sweeps the row."""
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    plot = await plot_of(session, constants, place.body)
    moment = second_now()
    step = timedelta(minutes=constants[R.TIME_TICK])
    await programmed(session, constants, catalog, place, [{"do": "plow"}], [plot], moment)
    await agro.tick_machines(session, constants, now=moment + step)
    await agro.stop(session, constants, catalog, place.body, place.machine, now=moment + step)
    kept = await agro.of_item(session, place.machine)
    assert kept is not None and not kept.program
    stamp = kept.counted_at

    await agro.tick_machines(session, constants, now=moment + 2 * step)
    await session.refresh(kept)
    assert kept.counted_at == stamp, "the tick passes a stopped machine over"

    row_id = kept.id
    await session.delete(place.machine)
    await session.flush()
    await agro.tick_machines(session, constants, now=moment + 3 * step)
    assert await session.get(FieldAutomat, row_id) is None


async def test_the_programmed_mark_follows_the_programme(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
) -> None:
    """The tick and the board find their machines by `programmed`, which the
    database keeps from `program`: on when a programme is set, off when it is
    taken off a busy machine -- the row stays, and the board no longer shows
    it -- and on again when it is set anew."""
    place = await field(session, constants)
    await liquid_in(session, place.yard, LUBRICANT, 100)
    plot = await plot_of(session, constants, place.body)
    moment = second_now()
    step = timedelta(minutes=constants[R.TIME_TICK])
    row = await programmed(session, constants, catalog, place, [{"do": "plow"}], [plot], moment)
    row_id = row.id
    mark = select(FieldAutomat.programmed).where(FieldAutomat.id == row_id)
    assert await session.scalar(mark) is True
    assert len((await agro.view(session, place.body))["machines"]) == 1
    await agro.tick_machines(session, constants, now=moment + step)

    await agro.stop(session, constants, catalog, place.body, place.machine, now=moment + step)
    kept = await agro.of_item(session, place.machine)
    assert kept is not None and not kept.program, "stopped while busy, the row stays"
    assert await session.scalar(mark) is False
    assert (await agro.view(session, place.body))["machines"] == [], "not shown as programmed"
    with pytest.raises(InvalidRequestError):
        #: Python reads `program`: the mark is the database's, not an attribute.
        _ = kept.programmed

    await programmed(session, constants, catalog, place, [{"do": "plow"}], [plot], moment + step)
    assert await session.scalar(mark) is True
    assert len((await agro.view(session, place.body))["machines"]) == 1
