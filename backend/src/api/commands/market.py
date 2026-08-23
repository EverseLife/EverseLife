# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The terminal: orders, reservations, cells.

Split out of `api/session.py` (review 2026-08-23, wave 3): the
socket loop stayed there, the commands live by domain.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.commands.common import _alive, _body, _identity, _node
from src.api.commands.views import _money
from src.api.registry import Refused, command
from src.constants import current, current_catalog
from src.engine import (
    market,
)
from src.models.market import (
    Order,
    OrderSide,
    OrderState,
    Reservation,
)
from src.models.world import Node
from src.units import amount_float, money_str


@command("market.offers")
async def _market_offers(state: dict, db: AsyncSession, message: dict) -> dict:
    """Other people's sell orders in the node: what can be reserved (D-047).

    The book is public by tiers, but a reservation is taken from a specific
    order -- so orders must be named. The seller's name is not shown: the book
    trades goods, not reputation.
    """
    identity_id = state["identity_id"]
    node = await _node(db, message["node"]) if message.get("node") else None
    if node is None:
        body = await _body(db, identity_id)
        if body is None:
            raise Refused("нет живого тела")
        node = await db.get(Node, body.node_id)

    rows = (
        (
            await db.execute(
                select(Order)
                .where(
                    Order.node_id == node.id,
                    Order.side == OrderSide.SELL,
                    Order.state == OrderState.ACTIVE,
                    Order.identity_id != identity_id,
                )
                .order_by(Order.price)
            )
        )
        .scalars()
        .all()
    )
    return {
        "offers": [
            {
                "id": str(order.id),
                "goods": order.type_key,
                "tier": order.tier,
                "price": order.price,
                "left": amount_float(order.amount_left),
            }
            for order in rows
            if order.amount_left > 0
        ]
    }


@command("market.reserve")
async def _market_reserve(state: dict, db: AsyncSession, message: dict) -> dict:
    """Reserve a lot with a deposit. Remote: the reservation is the trip plan."""
    identity = await _identity(state, db)
    order = await db.get(Order, uuid.UUID(message["order"]))
    if order is None:
        raise Refused("нет такой заявки")
    reservation = await market.reserve(db, current(), identity, order, float(message["amount"]))
    return {
        "reservation": str(reservation.id),
        "deposit": money_str(reservation.deposit),
        "expires_at": reservation.expires_at.isoformat(),
        "money": await _money(db, identity.id),
    }


@command("market.redeem")
async def _market_redeem(state: dict, db: AsyncSession, message: dict) -> dict:
    """Redeem a reservation: pay the remainder and take. In person (D-047)."""
    body = await _alive(state, db)
    reservation = await db.get(Reservation, uuid.UUID(message["reservation"]))
    if reservation is None:
        raise Refused("нет такой брони")
    deal = await market.redeem(db, current(), current_catalog(), body, reservation)
    return {
        "trade": str(deal.id),
        "goods": deal.type_key,
        "amount": amount_float(deal.amount),
        "money": await _money(db, state["identity_id"]),
    }


@command("market.load")
async def _market_load(state: dict, db: AsyncSession, message: dict) -> dict:
    """Load goods into the terminal. In person: goods are carried on foot (D-047)."""
    body = await _alive(state, db)
    moved = await market.load(
        db,
        current(),
        body,
        message["goods"],
        float(message["amount"]),
        tier=message.get("tier"),
    )
    return {"loaded": moved, "goods": message["goods"]}


@command("market.take")
async def _market_take(state: dict, db: AsyncSession, message: dict) -> dict:
    """Take your own from the terminal -- bought goods too. Also on foot."""
    body = await _alive(state, db)
    moved = await market.take(
        db,
        current(),
        body,
        message["goods"],
        float(message["amount"]),
        tier=message.get("tier"),
    )
    return {"taken": moved, "goods": message["goods"]}


@command("market.sell")
async def _market_sell(state: dict, db: AsyncSession, message: dict) -> dict:
    """List a sell order. Remote: the goods are already delivered."""
    identity = await _identity(state, db)
    node = await _node(db, message["node"])
    fill = await market.sell(
        db,
        current(),
        current_catalog(),
        identity,
        node,
        type_key=message["goods"],
        tier=message["tier"],
        price=int(message["price"]),
        quantity=float(message["amount"]),
    )
    return _fill(fill)


@command("market.buy")
async def _market_buy(state: dict, db: AsyncSession, message: dict) -> dict:
    """Buy: a limit order from a present body.

    The node is deliberately not named -- you buy where you stand.
    """
    body = await _alive(state, db)
    fill = await market.buy(
        db,
        current(),
        current_catalog(),
        body,
        type_key=message["goods"],
        tier=message["tier"],
        price=int(message["price"]),
        quantity=float(message["amount"]),
    )
    return _fill(fill)


@command("market.cancel")
async def _market_cancel(state: dict, db: AsyncSession, message: dict) -> dict:
    """Cancel an order. Disposing requires no presence."""
    identity_id = state["identity_id"]
    order = await db.get(Order, uuid.UUID(message["order"]))
    if order is None:
        raise Refused("нет такого ордера")
    await market.cancel(db, order, by=identity_id)
    return {"cancelled": str(order.id)}


def _fill(fill: market.Fill) -> dict[str, Any]:
    """What came of the order: the order itself and deals, if any happened."""
    return {
        "order": str(fill.order.id),
        "state": fill.order.state.value,
        "left": amount_float(fill.order.amount_left),
        "traded": fill.traded,
        "trades": [
            {"price": trade.price, "amount": amount_float(trade.amount)} for trade in fill.trades
        ],
    }
