# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Fields, breeding, foraging.

Split out of `api/session.py` (review 2026-08-23, wave 3): the
socket loop stayed there, the commands live by domain.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.commands.common import _alive, _body, _own_item
from src.api.registry import Refused, command
from src.constants import current, current_catalog
from src.engine import (
    breed,
    farm,
    forage,
)
from src.models.farm import Plot
from src.models.plant import Nursery, Variety


@command("farm.mark")
async def _farm_mark(state: dict, db: AsyncSession, message: dict) -> dict:
    """Mark out a field on the plot you stand on: `area` in square metres (D-126)."""
    body = await _alive(state, db)
    plot = await farm.mark(
        db,
        current(),
        body,
        name=str(message.get("name", "")),
        area=float(message["area"]),
    )
    return {"plot": str(plot.id)}


@command("farm.plow")
async def _farm_plow(state: dict, db: AsyncSession, message: dict) -> dict:
    """Plough the field: a work of hours finished by the worker (D-211)."""
    body = await _alive(state, db)
    plot = await farm.plow(db, current(), body, await _plot(db, message))
    return {"plowing": str(plot.id)}


@command("farm.sow")
async def _farm_sow(state: dict, db: AsyncSession, message: dict) -> dict:
    """Sow with seeds of a cultivar: the batch has both a cultivar and its own strength (D-057)."""
    body = await _alive(state, db)
    seeds = await _own_item(db, body, message["seeds"])
    plot = await farm.sow(db, current(), current_catalog(), body, await _plot(db, message), seeds)
    return {
        "sown": str(plot.id),
        "culture": plot.culture_id,
        "vigor": None if plot.seed_vigor is None else float(plot.seed_vigor),
    }


@command("farm.care")
async def _farm_care(state: dict, db: AsyncSession, message: dict) -> dict:
    """Tend the field: water and weed, as the crop's agrotech asks (D-057). In person, the body
    busy for the term."""
    body = await _alive(state, db)
    plot = await farm.care(db, current(), body, await _plot(db, message))
    return {"cared": str(plot.id), "credits": plot.care_credits}


@command("farm.harvest")
async def _farm_harvest(state: dict, db: AsyncSession, message: dict) -> dict:
    """Harvest. With selection the fund keeps its strength, without it degrades (D-067)."""
    body = await _alive(state, db)
    got = await farm.harvest(
        db,
        current(),
        current_catalog(),
        body,
        await _plot(db, message),
        select_seed=bool(message.get("select")),
    )
    return {"harvested": got, "selected": bool(message.get("select"))}


@command("breed.cross")
async def _breed_cross(state: dict, db: AsyncSession, message: dict) -> dict:
    """Cross two cultivars in the nursery: the result comes after a full cycle."""
    body = await _alive(state, db)
    one = await _own_item(db, body, message["a"])
    other = await _own_item(db, body, message["b"])
    nursery = await breed.cross(db, current(), current_catalog(), body, one, other)
    return {"nursery": str(nursery.id), "ready_at": nursery.ready_at.isoformat()}


@command("breed.gather")
async def _breed_gather(state: dict, db: AsyncSession, message: dict) -> dict:
    """Collect the seedlings. Empty means the cultivar was too similar and did not sprout
    (D-067)."""
    body = await _alive(state, db)
    nursery = await db.get(Nursery, uuid.UUID(message["nursery"]))
    if nursery is None:
        raise Refused(key="cmd-no-such-nursery")
    cultivar = await breed.gather_cross(db, current(), current_catalog(), body, nursery)
    if cultivar is None:
        return {"sprouted": False}
    return {"sprouted": True, "variety": str(cultivar.id), "traits": cultivar.traits}


@command("breed.name")
async def _breed_name(state: dict, db: AsyncSession, message: dict) -> dict:
    """Name a bred cultivar: the author's name is attached to it forever."""
    body = await _alive(state, db)
    cultivar = await db.get(Variety, uuid.UUID(message["variety"]))
    if cultivar is None:
        raise Refused(key="cmd-no-such-variety")
    cultivar = await breed.name_variety(db, body, cultivar, str(message["name"]))
    return {"variety": str(cultivar.id), "name": cultivar.name}


@command("breed.agrotech")
async def _breed_agrotech(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take the agrotech of a base crop in the Library: free, but on foot."""
    body = await _alive(state, db)
    knowledge = await breed.copy_agrotech(db, current_catalog(), body, str(message["culture"]))
    return {"learned": knowledge is not None, "culture": message["culture"]}


@command("breed.varieties")
async def _breed_varieties(state: dict, db: AsyncSession, message: dict) -> dict:
    """Own cultivars and ongoing crossings. Remote: can be viewed from anywhere."""
    body = await _body(db, state["identity_id"])
    cultivars = (
        (
            await db.execute(
                select(Variety).where(Variety.author_identity_id == state["identity_id"])
            )
        )
        .scalars()
        .all()
    )
    nurseries = (
        (
            await db.execute(
                select(Nursery).where(
                    Nursery.body_id == (body.id if body else None),
                    Nursery.done.is_(False),
                )
            )
        )
        .scalars()
        .all()
        if body
        else []
    )
    return {
        "varieties": [
            {
                "id": str(src.id),
                "name": src.name,
                "culture": src.culture_id,
                "stable": src.stable,
                "generation": src.generation,
                "traits": src.traits,
            }
            for src in cultivars
        ],
        "nurseries": [{"id": str(p.id), "ready_at": p.ready_at.isoformat()} for p in nurseries],
    }


@command("farm.split")
async def _farm_split(state: dict, db: AsyncSession, message: dict) -> dict:
    """Split a field into two: `area` goes to the new one."""
    body = await _alive(state, db)
    piece = await farm.split(
        db,
        current(),
        body,
        await _plot(db, message),
        float(message["area"]),
        name=str(message.get("name", "")),
    )
    return {"piece": str(piece.id)}


@command("farm.merge")
async def _farm_merge(state: dict, db: AsyncSession, message: dict) -> dict:
    """Merge two fields into one: `other` is folded into `plot` and ceases to be.

    The door the engine's `merge` had been waiting for. Without it a plot could
    be cut and never sewn back, so a farmer who split one strip too many was
    left with the pieces -- and the anti-exploit that makes merging honest
    (fertility by area, history by the heavier half, D-118) guarded nothing,
    because nobody could reach it.
    """
    body = await _alive(state, db)
    one_id = uuid.UUID(message["plot"])
    other_id = uuid.UUID(message["other"])
    if one_id == other_id:
        raise Refused(key="cmd-merge-one-plot")
    #: Both rows in one stable order, whichever way the player named them:
    #: two reversed merges otherwise meet at each other's second lock.
    locked = {pid: await _locked_plot(db, pid) for pid in sorted((one_id, other_id))}
    whole = await farm.merge(db, current(), body, locked[one_id], locked[other_id])
    return {"plot": str(whole.id), "area": float(whole.area_m2)}


@command("farm.survey")
async def _farm_survey(state: dict, db: AsyncSession, message: dict) -> dict:
    """Farm summary. Remote: readable even from the road (D-118)."""
    rows = await farm.survey(db, current(), current_catalog(), state["identity_id"])
    return {"plots": rows}


async def _plot(db: AsyncSession, message: dict) -> Plot:
    return await _locked_plot(db, uuid.UUID(message["plot"]))


async def _locked_plot(db: AsyncSession, plot_id: uuid.UUID) -> Plot:
    """The plot under the command's hands, taken for the transaction.

    Fertility, state and the sown fund are remainders like money (CLAUDE.md):
    without the lock two harvests of one field both see SOWN, both hand out
    the crop and the seed fund, and fertility is written twice from one read
    value. `populate_existing` for the same reason as `estate.hold_ground`:
    a copy loaded before the lock is reread after it, not served stale.

    Lock order: the body first (`_alive`), the plot second -- same as every
    farm command reaches this helper.
    """
    plot = await db.get(Plot, plot_id, with_for_update=True, populate_existing=True)
    if plot is None:
        raise Refused(key="cmd-no-such-plot")
    return plot


@command("forage.start")
async def _forage_start(state: dict, db: AsyncSession, message: dict) -> dict:
    """Start foraging the plot one stands on (D-210). The find shows by the deadline."""
    body = await _alive(state, db)
    row = await forage.start(db, current(), body)
    return {"forage": str(row.id), "ready_at": row.ready_at.isoformat()}


@command("forage.take")
async def _forage_take(state: dict, db: AsyncSession, message: dict) -> dict:
    """Pick the find up. The foraging ends there: searching again is a decision (D-211)."""
    body = await _alive(state, db)
    await forage.take(db, current(), current_catalog(), body)
    return {"taken": True}


@command("forage.pass")
async def _forage_pass(state: dict, db: AsyncSession, message: dict) -> dict:
    """Leave the find lying and search on."""
    body = await _alive(state, db)
    row = await forage.pass_(db, current(), body)
    return {"passed": True, "ready_at": row.ready_at.isoformat()}


@command("forage.stop")
async def _forage_stop(state: dict, db: AsyncSession, message: dict) -> dict:
    """End the foraging: whatever was under way or on offer is dropped."""
    body = await _alive(state, db)
    await forage.stop(db, body)
    return {"stopped": True}
