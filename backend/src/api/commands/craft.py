# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Crafting, the library, carriers, cooking.

Split out of `api/session.py` (review 2026-08-23, wave 3): the
socket loop stayed there, the commands live by domain.
"""

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.commands.common import _alive, _own_item, _stamp
from src.api.commands.views import _optional_uuid, _tiers
from src.api.registry import Refused, command
from src.constants import current, current_catalog
from src.engine import (
    craft,
    food,
    library,
)


@command("craft.plan")
async def _craft_plan(state: dict, db: AsyncSession, message: dict) -> dict:
    """Forecast before a batch. Spends nothing and reserves nothing (D-092)."""
    body = await _alive(state, db)
    output, units, extra = _craft_request(message)
    plan = await craft.plan(db, current(), current_catalog(), body, output, units, **extra)
    return {"plan": asdict(plan)}


@command("craft.start")
async def _craft_start(state: dict, db: AsyncSession, message: dict) -> dict:
    """Start a batch. From then on it runs by itself, including while the player is offline."""
    body = await _alive(state, db)
    output, units, extra = _craft_request(message)
    batch = await craft.start(db, current(), current_catalog(), body, output, units, **extra)
    return {
        "batch": str(batch.id),
        "output": batch.output,
        "quality": float(batch.quality),
        "ready_at": _stamp(batch.ready_at),
    }


@command("craft.repair")
async def _craft_repair(state: dict, db: AsyncSession, message: dict) -> dict:
    """Repair a thing: condition comes back, the ceiling drops (15-quality)."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    batch = await craft.repair(db, current(), current_catalog(), body, item, tiers=_tiers(message))
    return {"batch": str(batch.id), "ready_at": _stamp(batch.ready_at)}


@command("craft.recycle")
async def _craft_recycle(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take a thing apart for part of the materials. The return is always less than invested."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    batch = await craft.recycle(db, current(), current_catalog(), body, item)
    return {"batch": str(batch.id), "ready_at": _stamp(batch.ready_at)}


@command("library.copy")
async def _library_copy(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take a recipe in the Library: free, unconditional, but only in person (D-053)."""
    body = await _alive(state, db)
    key = message["recipe"]
    await craft.copy_recipe(db, current_catalog(), body, key)
    return {"learned": key}


@command("library.contribute")
async def _library_contribute(state: dict, db: AsyncSession, message: dict) -> dict:
    """Give a written carrier to the library one stands in: for good, with one's name (D-209)."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    entry = await library.contribute(db, current_catalog(), body, item)
    return {"contributed": entry.recipe}


@command("craft.invent")
async def _craft_invent(state: dict, db: AsyncSession, message: dict) -> dict:
    """Try to make something without a recipe (D-064, D-209).

    `composition` is what is laid out per unit of output, `units` how many
    units; `station` names the machine one stands at, empty for by hand.
    """
    body = await _alive(state, db)
    raw = message.get("composition") or {}
    if not isinstance(raw, dict):
        raise Refused("состав задаётся парами «вещь: сколько»")
    composition = {str(name): float(value) for name, value in raw.items()}
    result = await craft.invent(
        db,
        current(),
        current_catalog(),
        body,
        composition,
        float(message.get("units", 1)),
        station=message.get("station"),
        tiers=_tiers(message),
    )
    return {
        "success": result.success,
        "learned": list(result.learned),
        "burned": result.burned,
        "note": result.note,
        "batch": None
        if result.batch is None
        else {
            "id": str(result.batch.id),
            "output": result.batch.output,
            "quality": float(result.batch.quality),
            "ready_at": _stamp(result.batch.ready_at),
        },
    }


@command("craft.resume")
async def _craft_resume(state: dict, db: AsyncSession, message: dict) -> dict:
    """Go on with a waiting work by hand: the master is here and a machine is free (D-209)."""
    body = await _alive(state, db)
    batch = await craft.wake(db, body)
    if batch is None:
        raise Refused(
            "продолжать нечего: либо ничего не ждёт здесь, либо станция занята, "
            "либо работа уже идёт"
        )
    return {"batch": str(batch.id), "output": batch.output, "ready_at": _stamp(batch.ready_at)}


@command("carrier.read")
async def _carrier_read(state: dict, db: AsyncSession, message: dict) -> dict:
    """Copy the recipe off a carrier in the hands; the carrier stays (D-209)."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    learned = await craft.read_carrier(db, current_catalog(), body, item)
    return {"learned": None if learned is None else learned.key, "already": learned is None}


@command("carrier.wipe")
async def _carrier_wipe(state: dict, db: AsyncSession, message: dict) -> dict:
    """Erase a carrier back into a blank (D-209)."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    blank = await craft.wipe_carrier(db, current_catalog(), body, item)
    return {"item": str(blank.id), "goods": blank.type_key}


@command("food.eat")
async def _food_eat(state: dict, db: AsyncSession, message: dict) -> dict:
    """Eat a portion. Works on the road too: hardtack en route is normal (D-091)."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    restored = await food.eat(db, current(), current_catalog(), body, item)
    return {"restored": round(restored, 2), "stamina": float(body.stamina)}


@command("cook.pot")
async def _cook_pot(state: dict, db: AsyncSession, message: dict) -> dict:
    """Cook a pot: roles instead of a composition, portions -- cook.pot_portions (D-119)."""
    body = await _alive(state, db)
    batch = await craft.cook(
        db,
        current(),
        current_catalog(),
        body,
        str(message["output"]),
        dict(message.get("filling") or {}),
        tiers=_tiers(message),
    )
    return {
        "batch": str(batch.id),
        "flavor": batch.flavor,
        "quality": float(batch.quality),
        "ready_at": _stamp(batch.ready_at),
    }


def _craft_request(message: dict) -> tuple[str, float, dict[str, Any]]:
    """Parsing a batch request -- identical for forecast and start.

    Otherwise the forecast would be computed for one request and the batch
    would run on another.
    """
    return (
        message["output"],
        float(message.get("units", 1)),
        {
            "tool_item_id": _optional_uuid(message.get("tool")),
            "proportions": message.get("proportions"),
            #: "Put on automatic" is the master's decision: volume instead of
            #: quality, and an energy bill (D-035, D-058).
            "auto": bool(message.get("auto", False)),
            #: Which operation, when several give the same thing (D-196).
            "way": message.get("way"),
            #: For a knowledge carrier: which recipe goes onto it (D-209).
            "recipe_key": message.get("recipe"),
            #: Which quality tier feeds each input -- the master's choice (D-058).
            "tiers": _tiers(message),
        },
    )
