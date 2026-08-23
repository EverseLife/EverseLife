# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Gear, the ground, storages, stations.

Split out of `api/session.py` (review 2026-08-23, wave 3): the
socket loop stayed there, the commands live by domain.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.commands.common import _alive, _own_item
from src.api.registry import Refused, command
from src.constants import current, current_catalog
from src.engine import (
    chat,
    gear,
    liquid,
    station,
    storage,
)
from src.models.chat import Utterance
from src.models.identity import Body, Identity
from src.models.inventory import Item


@command("gear.equip")
async def _gear_equip(state: dict, db: AsyncSession, message: dict) -> dict:
    """Wear a thing. One slot per thing: you cannot wear three backpacks (D-146)."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    slot = await gear.equip(db, current(), current_catalog(), body, item)
    return {
        "equipped": slot,
        "goods": item.type_key,
        "capacity": round(await gear.capacity(db, current(), current_catalog(), body), 2),
    }


@command("gear.unequip")
async def _gear_unequip(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take off a worn thing. It stays in the hands -- it was there anyway."""
    body = await _alive(state, db)
    removed = await gear.unequip(db, body, str(message["slot"]))
    return {
        "unequipped": None if removed is None else removed.type_key,
        "capacity": round(await gear.capacity(db, current(), current_catalog(), body), 2),
    }


@command("item.hand")
async def _item_hand(state: dict, db: AsyncSession, message: dict) -> dict:
    """Hand a thing to somebody standing here. In person on both sides.

    The hand-over speaks in the room: the chat gets an action line, because a
    transfer between two people is a fact the others in the room can see, and a
    silent one would be a way to move property unobserved.
    """
    giver = await _alive(state, db)
    item = await _own_item(db, giver, message["item"])
    taker = await db.get(Body, uuid.UUID(message["to"]))
    if taker is None:
        raise Refused("такого человека здесь нет")
    qty = message.get("amount")
    given = await storage.hand(
        db,
        current(),
        current_catalog(),
        giver,
        taker,
        item,
        None if qty is None else float(qty),
    )

    who = await db.get(Identity, taker.identity_id)
    await chat.say(
        db,
        current(),
        giver,
        f"передаёт {'—' if who is None else who.name}: {item.type_key}"
        + (f" ×{given:g}" if given != 1 else ""),
        kind=Utterance.ACTION,
    )
    return {"given": given, "goods": item.type_key}


@command("ground.drop")
async def _ground_drop(state: dict, db: AsyncSession, message: dict) -> dict:
    """Put a thing down here: under the roof if there is one, in the yard if not."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    qty = message.get("amount")
    put_down = await storage.drop(
        db,
        current(),
        current_catalog(),
        body,
        item,
        None if qty is None else float(qty),
    )
    return {"dropped": put_down, "goods": item.type_key}


@command("ground.pick")
async def _ground_pick(state: dict, db: AsyncSession, message: dict) -> dict:
    """Pick up what lies here. Somebody else's floor is not touched (D-192)."""
    body = await _alive(state, db)
    item = await db.get(Item, uuid.UUID(message["item"]))
    if item is None:
        raise Refused("нет такой вещи")
    qty = message.get("amount")
    taken = await storage.pick(
        db,
        current(),
        current_catalog(),
        body,
        item,
        None if qty is None else float(qty),
    )
    return {"picked": taken, "goods": item.type_key}


@command("storage.put")
async def _storage_put(state: dict, db: AsyncSession, message: dict) -> dict:
    """Put a thing from the hands into the node storage (D-181)."""
    body = await _alive(state, db)
    chest = await db.get(Item, uuid.UUID(message["storage"]))
    if chest is None:
        raise Refused("нет такого хранилища")
    item = await _own_item(db, body, message["item"])
    qty = message.get("amount")
    put = await storage.put(
        db,
        current(),
        current_catalog(),
        body,
        chest,
        item,
        None if qty is None else float(qty),
    )
    return {"stored": put, "goods": item.type_key}


@command("storage.take")
async def _storage_take(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take a thing from storage into the hands. The carry limit still applies."""
    body = await _alive(state, db)
    chest = await db.get(Item, uuid.UUID(message["storage"]))
    item = await db.get(Item, uuid.UUID(message["item"]))
    if chest is None or item is None:
        raise Refused("нет такой вещи")
    qty = message.get("amount")
    taken = await storage.take(
        db,
        current(),
        current_catalog(),
        body,
        chest,
        item,
        None if qty is None else float(qty),
    )
    return {"taken": taken, "goods": item.type_key}


@command("liquid.pour")
async def _liquid_pour(state: dict, db: AsyncSession, message: dict) -> dict:
    """Pour a liquid from one vessel into another (D-230).

    `from` and `to` are vessels -- in the hands or standing here; `goods`
    names the liquid (default: whatever is in the source), `amount` caps it.
    A liquid is never held loose: this is the one way it changes place.
    """
    body = await _alive(state, db)
    source = await db.get(Item, uuid.UUID(str(message.get("from") or "")))
    target = await db.get(Item, uuid.UUID(str(message.get("to") or "")))
    if source is None or target is None:
        raise Refused("нет такой тары")
    qty = message.get("amount")
    goods_, poured = await liquid.pour(
        db,
        current(),
        current_catalog(),
        body,
        source,
        target,
        message.get("goods") or None,
        None if qty is None else float(qty),
    )
    return {"poured": poured, "goods": goods_}


@command("station.place")
async def _station_place(state: dict, db: AsyncSession, message: dict) -> dict:
    """Place a machine in the node. In person and only at your own place (D-150)."""
    body = await _alive(state, db)
    item = await _own_item(db, body, message["item"])
    await station.place(db, current_catalog(), body, item)
    return {"placed": item.type_key}


@command("station.take")
async def _station_take(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take a machine back into the hands. One busy with work is not given up."""
    body = await _alive(state, db)
    item = await db.get(Item, uuid.UUID(message["item"]))
    if item is None:
        raise Refused("нет такого предмета")
    await station.take(db, current_catalog(), body, item)
    return {"taken": item.type_key}
