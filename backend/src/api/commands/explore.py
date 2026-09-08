# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Exploration over the socket (D-321): aim at a point of the globe."""

from __future__ import annotations

import math

from sqlalchemy.ext.asyncio import AsyncSession

from src import globe
from src.api.commands.common import _alive
from src.api.registry import Refused, command
from src.constants import current
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


@command("explore.stop")
async def _explore_stop(state: dict, db: AsyncSession, message: dict) -> dict:
    """Turn back from a run: the scout stays where they set out from (D-327)."""
    body = await _alive(state, db)
    await explore.stop(db, body)
    return {"stopped": True}
