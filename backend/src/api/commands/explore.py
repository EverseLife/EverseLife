# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Exploration over the socket (D-321): aim at a point of the globe."""

from __future__ import annotations

import math

from sqlalchemy.ext.asyncio import AsyncSession

from src import globe
from src.api.commands.common import _alive, _alive_read
from src.api.registry import Refused, command
from src.constants import current, current_catalog
from src.engine import explore


def _degrees(message: dict, name: str, limit: float) -> float:
    try:
        value = float(message[name])
    except (KeyError, TypeError, ValueError) as wrong:
        raise Refused(key="cmd-bad-point", field=name) from wrong
    if not math.isfinite(value) or abs(value) > limit:
        raise Refused(key="cmd-bad-point", field=name)
    return value


@command("explore.survey")
async def _explore_survey(state: dict, db: AsyncSession, message: dict) -> dict:
    """Send the body to explore the point `lat`, `lon` of its own planet.

    The landscape decides whether the aim is lawful and the road decides the
    price; the answer confirms the run and names the cell, the client learns
    the find from the journal when the job fires (D-226).
    """
    body = await _alive(state, db)
    point = (
        _degrees(message, "lat", globe.QUARTER_TURN),
        globe.wrap_lon(_degrees(message, "lon", globe.FULL_TURN)),
    )
    job = await explore.survey(db, current(), body, point)
    return {
        "job": str(job.id),
        #: When the scout gets **there**: a run is the walk one way, and it
        #: ends standing on the find (D-327). The key used to be `returns_at`,
        #: from the days the scout came back for free.
        "arrives_at": job.run_at.isoformat(),
        "cell": job.payload["cell"],
    }


@command("explore.peek", readonly=True)
async def _explore_peek(state: dict, db: AsyncSession, message: dict) -> dict:
    """What the field says at the point `lat`, `lon`, before the walk (D-321 addendum).

    The aim is judged as a run's would be, so a refusal comes here first; a
    lawful one answers with the readings of the public field and the chances
    of what is rolled -- never the roll, which is the find's own.
    """
    #: A read: the body's row is read, not locked -- a peek goes out on
    #: every tap and must not queue behind the body's own actions.
    body = await _alive_read(state, db)
    point = (
        _degrees(message, "lat", globe.QUARTER_TURN),
        globe.wrap_lon(_degrees(message, "lon", globe.FULL_TURN)),
    )
    return await explore.peek(db, current(), current_catalog(), body, point)


@command("explore.stop")
async def _explore_stop(state: dict, db: AsyncSession, message: dict) -> dict:
    """Turn back from a run: the scout stays where they set out from (D-327)."""
    body = await _alive(state, db)
    await explore.stop(db, body)
    return {"stopped": True}
