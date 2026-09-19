# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The seed's one-off steps: repairs a world takes once and never again (D-007).

Every step of the catch-up is idempotent by reading the world -- save the ones
whose result cannot be told from a player's choice the day after: a port its
owner emptied on purpose looks exactly like one the step has yet to draw. Such
a step cannot find out from the world whether it has run, so the world is
told: a row in `catch_up_step`, written in the same transaction as the step's
own writes.

A world laid fresh is born with every one-off step done (`born`): it never
lived under the rules they mend, and its first deploy must not mend what was
never broken.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.catchup import CatchUpStep

#: The lines of hulls that lived under the old default (D-288 as amended
#: 2026-09-04): `seed_catchup._lines_catch_up`.
LINES_DEFAULT_ENDED = "lines_default_ended"

#: The land highways took before D-356 made it a plot:
#: `seed_catchup._taken_land_is_plots`. Once, because after it the rule is the
#: engine's own (`city.land.annex_by_way`), and a run at every deploy would
#: turn any later location of a city that hangs off its node into a plot.
TAKEN_LAND_IS_PLOTS = "taken_land_is_plots"

#: Every one-off step there is. A new one is added here, so a fresh world is
#: born past it.
ONCE = (LINES_DEFAULT_ENDED, TAKEN_LAND_IS_PLOTS)


async def claim(session: AsyncSession, step: str) -> bool:
    """Take the step for this transaction: True when it has not run on this
    world, and then the caller runs it and calls `done` in the same transaction.

    Two deploys overlapping run the catch-up twice at the same moment. The lock
    comes before the question, so the second waits for the first to commit and
    finds its row, rather than running the step over the first one's writes.
    """
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"catch_up_step:{step}"}
    )
    return await session.get(CatchUpStep, step) is None


async def done(session: AsyncSession, step: str, **result: Any) -> None:
    """Mark the step done, with what it did."""
    session.add(CatchUpStep(step=step, result=result))
    await session.flush()


async def born(session: AsyncSession) -> None:
    """Mark every one-off step done on a world laid today (`ONCE`)."""
    for step in ONCE:
        await done(session, step, born=True)
