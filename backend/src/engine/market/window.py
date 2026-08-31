# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Reading the book: the shop window, and only a window.

Public and remote -- everyone knows the prices (D-047) -- and a read that
writes nothing: rows are glued into a price step so a hundred orders a minor
unit apart stay readable, a bid rounds down and an ask up so no row ever
promises better terms than the orders inside it, and a buy with a floor is
shown in every window any lot of which would satisfy it (D-239).
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.engine.market._base import Book, Level, _floor_sql, tier_span
from src.models.market import Order, OrderSide, OrderState, Trade
from src.models.world import Node
from src.runtime import MARKET_BOOK_STEPS
from src.units import amount_float


async def book(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    type_key: str,
    tier: str,
    *,
    depth: int,
    step: int | None = None,
) -> Book:
    """The book by position. Public: everyone knows the prices (D-047).

    Prices are written to the minor unit, and a hundred orders a minor unit
    apart make a hundred rows nobody can read. So rows are **glued into a
    step**: a bid rounds down to its step, an ask rounds up, and neither ever
    reads better than the order behind it -- click a row, and what you name
    still crosses. `step=None` picks the finest step the depth can hold.
    """
    if step is None:
        step = await _step_for(session, constants, node, type_key, tier, depth=depth)
    bids = await _levels(
        session, constants, node, type_key, tier, OrderSide.BUY, depth=depth, step=step
    )
    asks = await _levels(
        session, constants, node, type_key, tier, OrderSide.SELL, depth=depth, step=step
    )
    #: `at` is the transaction's clock, so one sweeping order stamps all its
    #: deals alike; the id breaks the tie so that two rereads of one book do
    #: not name two different prices, and so that the name in the picker and
    #: the line under the book agree on which deal was last.
    last = await session.scalar(
        select(Trade.price)
        .where(Trade.node_id == node.id, Trade.type_key == type_key, Trade.tier == tier)
        .order_by(Trade.at.desc(), Trade.id.desc())
        .limit(1)
    )
    return Book(
        node=node.id, type_key=type_key, tier=tier, bids=bids, asks=asks, last=last, step=step
    )


async def positions(session: AsyncSession, node: Node) -> tuple[tuple[str, str], ...]:
    """Which positions trade in the node at all: goods plus tier."""
    rows = await session.execute(
        select(Order.type_key, Order.tier)
        .where(Order.node_id == node.id, Order.state == OrderState.ACTIVE)
        .group_by(Order.type_key, Order.tier)
        .order_by(Order.type_key, Order.tier)
    )
    return tuple((row[0], row[1]) for row in rows)


async def last_prices(session: AsyncSession, node: Node) -> dict[str, int]:
    """The last deal price for every goods the node has ever traded, any tier.

    The picker shows a **name**, and a name gathers all five tiers, so the
    price beside it is the freshest deal under that name whatever its quality.
    Only deals: a price with nobody's deal behind it would be the engine
    valuing goods, and the engine does not value goods (D-002). Never traded --
    no number, and the row says nothing rather than something invented.

    One row per name, off `ix_market_trade_last`: the panel rereads this on
    every trade in the node, deals are never deleted, and without that index
    the question sorts the node's whole trading history each time.
    """
    rows = await session.execute(
        select(Trade.type_key, Trade.price)
        .where(Trade.node_id == node.id)
        .distinct(Trade.type_key)
        .order_by(Trade.type_key, Trade.at.desc(), Trade.id.desc())
    )
    return {row[0]: row[1] for row in rows}


def _rung(side: OrderSide, step: int) -> ColumnElement[int]:
    """The price a row stands at once glued to the step.

    A bid rounds **down**, an ask rounds **up**: a row must never promise
    better terms than the orders inside it. Name the row's price back to the
    engine and it still crosses -- the buyer offers no less than the sellers
    ask, the seller asks no more than the buyers offer. Rounding the other way
    would draw a book that cannot be traded against.
    """
    if step <= 1:
        return Order.price
    #: Floor division, not `/`: on integer columns SQLAlchemy 2.0 renders `/`
    #: as true division through NUMERIC, and every price then lands in a
    #: bucket of its own -- the rows are not glued at all, silently.
    if side is OrderSide.BUY:
        return Order.price // step * step
    return (Order.price + step - 1) // step * step


def _in_window(constants: Constants, tier: str, side: OrderSide) -> ColumnElement[bool]:
    """Whose orders the window of one tier shows.

    Sellers stand in the tier of their lot. A buyer stands in every window
    **any** lot of which would satisfy them (D-239): a buy that takes nothing
    worse than 60 is real demand for "хорошее" and for everything above it,
    and a seller of "отличное" must see it -- otherwise the window says
    "empty" a moment before the deal happens. One order shown in several
    windows is still one order and fills once.

    A floor standing inside a band -- 75 within 60..79 -- is shown from the
    next window up, not from its own. Its own window holds lots it would
    refuse, and a bid drawn against asks it cannot take would cross them on
    the screen and never trade: a spread stuck at zero, saying a deal is there
    for the taking when there is none. Better to show that demand one window
    later than to draw a book that cannot be read.
    """
    if side is OrderSide.SELL:
        return Order.tier == tier
    return _floor_sql(constants) <= tier_span(constants, tier)[0]


async def _step_for(
    session: AsyncSession, constants: Constants, node: Node, type_key: str, tier: str, *, depth: int
) -> int:
    """The finest step at which the whole book fits the depth.

    Counted for every rung of the ladder at once, and for the two sides apart:
    a step that fits the bids but not the asks fits neither. Asked before the
    rows themselves, because the alternative -- read finely, glue afterwards --
    glues what the depth already cut off and draws a book with a hole in it.
    """
    #: Counted by the very expression the rows are drawn with: bids round down
    #: and asks round up, and the two do not always fall into the same number
    #: of rows. Counting both by the bid's rule would pick a step that then
    #: draws more ask rows than the depth shows -- a book with a hole in it,
    #: cut by the very `limit` this is meant to keep clear of.
    asked = [(step, side) for step in MARKET_BOOK_STEPS for side in (OrderSide.BUY, OrderSide.SELL)]
    tallies = [
        func.count(func.distinct(_rung(side, step))).filter(
            Order.side == side, _in_window(constants, tier, side)
        )
        for step, side in asked
    ]
    counted = (
        await session.execute(
            select(*tallies).where(
                Order.node_id == node.id,
                Order.type_key == type_key,
                Order.state == OrderState.ACTIVE,
                or_(
                    and_(Order.side == OrderSide.SELL, _in_window(constants, tier, OrderSide.SELL)),
                    and_(Order.side == OrderSide.BUY, _in_window(constants, tier, OrderSide.BUY)),
                ),
            )
        )
    ).one()
    fits: dict[int, bool] = {}
    for (step, _side), rows in zip(asked, counted, strict=True):
        fits[step] = fits.get(step, True) and int(rows or 0) <= depth
    for step in MARKET_BOOK_STEPS:
        if fits.get(step):
            return step
    return MARKET_BOOK_STEPS[-1]


async def _levels(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    type_key: str,
    tier: str,
    side: OrderSide,
    *,
    depth: int,
    step: int = 1,
) -> tuple[Level, ...]:
    rung = _rung(side, step)
    stmt = (
        select(rung, func.sum(Order.amount_left))
        .where(
            Order.node_id == node.id,
            Order.type_key == type_key,
            _in_window(constants, tier, side),
            Order.side == side,
            Order.state == OrderState.ACTIVE,
        )
        .group_by(rung)
        .limit(depth)
    )
    stmt = stmt.order_by(rung.desc() if side is OrderSide.BUY else rung)
    rows = await session.execute(stmt)
    return tuple(Level(price=int(row[0]), amount=amount_float(int(row[1]))) for row in rows)
