# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The tellers: how an event is worded for the one it happened to -- travel,
print, market, mining. Each registers itself with the Hub's registry on
import, through the door.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.push.pump import Sink, teller
from src.models.event import Event
from src.models.world import Node

# -- tellers ---------------------------------------------------------------


@teller("knowledge.learned")
async def _knowledge_learned(
    db: AsyncSession, row: Event, sink: Sink, message: dict[str, Any]
) -> dict[str, Any] | None:
    payload = row.payload or {}
    if payload.get("key"):
        message["key"] = payload["key"]
    if payload.get("name"):
        message["name"] = payload["name"]
    if payload.get("kind_of_knowledge"):
        message["kind"] = payload["kind_of_knowledge"]
    return message


async def _named_node(db: AsyncSession, row: Event, message: dict[str, Any]) -> dict[str, Any]:
    if row.node_id is not None:
        node = await db.get(Node, row.node_id)
        if node is not None:
            message["node"] = {"key": node.key, "name": node.name}
    return message


@teller("travel.arrived")
async def _travel_arrived(
    db: AsyncSession, row: Event, sink: Sink, message: dict[str, Any]
) -> dict[str, Any] | None:
    return await _named_node(db, row, message)


@teller("travel.started")
async def _travel_started(
    db: AsyncSession, row: Event, sink: Sink, message: dict[str, Any]
) -> dict[str, Any] | None:
    return await _named_node(db, row, message)


@teller("body.printed")
async def _body_printed(
    db: AsyncSession, row: Event, sink: Sink, message: dict[str, Any]
) -> dict[str, Any] | None:
    return await _named_node(db, row, message)


def _carry(row: Event, message: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Copy the named payload keys into the message -- what the recipient
    could have seen by asking. Ids stay in the journal."""
    payload = row.payload or {}
    for key in keys:
        if payload.get(key) is not None:
            message[key] = payload[key]
    return message


#: The book is public (D-047): a trade or an order in the node is told to
#: everyone in it with goods, tier, price and amount -- never with names.
@teller("market.trade")
async def _market_trade(
    db: AsyncSession, row: Event, sink: Sink, message: dict[str, Any]
) -> dict[str, Any] | None:
    message.pop("who", None)
    return _carry(row, message, "type_key", "tier", "price", "amount")


@teller("market.order_placed")
async def _market_order_placed(
    db: AsyncSession, row: Event, sink: Sink, message: dict[str, Any]
) -> dict[str, Any] | None:
    message.pop("who", None)
    return _carry(row, message, "side", "type_key", "tier", "price", "amount")


@teller("market.order_cancelled")
async def _market_order_cancelled(
    db: AsyncSession, row: Event, sink: Sink, message: dict[str, Any]
) -> dict[str, Any] | None:
    message.pop("who", None)
    return _carry(row, message, "order_id")


@teller("market.order_expired")
async def _market_order_expired(
    db: AsyncSession, row: Event, sink: Sink, message: dict[str, Any]
) -> dict[str, Any] | None:
    message.pop("who", None)
    return _carry(row, message, "order_id")


#: The face: every swing is told with what it brought; a collapse with what
#: it took. The miner's own numbers -- bystanders hear the collapse alone.
#: Digging a caved-in working out (D-301) has no teller and needs none: it is
#: announced rather than journalled, and an announcement carries its own
#: payload straight to the sinks (`pump._deliver_touch`) without passing
#: here. A teller for it would read like a filter that is not on that road.
@teller("mining.swing")
async def _mining_swing(
    db: AsyncSession, row: Event, sink: Sink, message: dict[str, Any]
) -> dict[str, Any] | None:
    return _carry(row, message, "mined", "quality")


@teller("mining.collapsed")
async def _mining_collapsed(
    db: AsyncSession, row: Event, sink: Sink, message: dict[str, Any]
) -> dict[str, Any] | None:
    if sink.identity_id == row.actor_identity_id:
        return _carry(row, message, "lost", "wounded", "killed")
    return message
