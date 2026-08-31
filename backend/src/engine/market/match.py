# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Crossing orders: best price, whoever came first, at the maker's terms.

A resting order names its terms first and the newcomer accepts them, so a deal
goes at the price of the one in the book (30-economy/02). Money moves through
escrow only -- frozen under a buy by `_hold`, paid out by `_settle`, returned
by `_release` -- and the seller gets exactly what the buyer paid minus tax and
commission (I2): not a coin appears or vanishes. Lock order on the market:
cell -> orders -> accounts; `_counterparts` takes the makers under lock so a
concurrent taker sees the decrement, not the snapshot.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import events, ledger
from src.engine.jobs import enqueue
from src.engine.market._base import (
    Fill,
    MarketError,
    NoGoods,
    _cost,
    _floor_of,
    _floor_sql,
    tier_span,
)
from src.engine.market.counter import _move, _stacks, stall
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.job import JobKind
from src.models.ledger import AccountKind, PostingReason
from src.models.market import Order, OrderSide, OrderState, Trade
from src.models.world import Node
from src.units import PERCENT, amount_float, money_str


async def _place(
    session: AsyncSession,
    constants: Constants,
    identity: Identity,
    node: Node,
    side: OrderSide,
    type_key: str,
    tier: str,
    price: int,
    quantity: int,
    *,
    min_quality: int | None = None,
    now: datetime | None,
) -> Order:
    moment = now or datetime.now(UTC)
    lifetime = timedelta(hours=constants[R.MARKET_ORDER_LIFETIME] * constants[R.TIME_DAY_TERRA])
    order = Order(
        node_id=node.id,
        identity_id=identity.id,
        side=side,
        type_key=type_key,
        tier=tier,
        min_quality=min_quality,
        price=price,
        amount_total=quantity,
        amount_left=quantity,
        expires_at=moment + lifetime,
    )
    session.add(order)
    await session.flush()

    event = await events.record(
        session,
        EventKind.ORDER_PLACED,
        actor_identity_id=identity.id,
        node_id=node.id,
        order_id=str(order.id),
        side=side.value,
        type_key=type_key,
        tier=tier,
        price=price,
        amount=amount_float(quantity),
    )
    await enqueue(
        session,
        JobKind.MARKET_ORDER_EXPIRY,
        order.expires_at,
        payload={"order": str(order.id)},
        dedup_key=f"market.expiry:{order.id}",
        cause_event_id=event.id,
    )
    return order


async def _match(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    order: Order,
    *,
    now: datetime | None,
) -> Fill:
    """Match the order with opposing ones: best price; at equal price, whoever came first.

    A counterpart acceptable by price may still be unreachable by quality: the
    buyer's floor is met by some of the seller's stacks and not by others
    (D-239). What is deliverable is counted per pair, and a pair with nothing
    deliverable is passed over -- the order goes on down the ladder instead of
    stopping at the first seller who cannot satisfy it.
    """
    moment = now or datetime.now(UTC)
    node = await session.get(Node, order.node_id)
    if node is None:  # pragma: no cover
        raise MarketError(key="market-order-off-node")
    trades: list[Trade] = []

    for counter in await _counterparts(session, constants, order):
        if order.amount_left <= 0:
            break
        buying = order if order.side is OrderSide.BUY else counter
        selling = counter if order.side is OrderSide.BUY else order
        floor = _floor_of(constants, buying)
        quantity = min(
            order.amount_left,
            counter.amount_left,
            await _sellable(session, constants, node, selling, floor),
        )
        if quantity <= 0:
            continue
        trades.append(
            await _execute(
                session, constants, catalog, order, counter, moment, quantity=quantity, floor=floor
            )
        )

    if order.amount_left <= 0:
        await _close(session, order, OrderState.FILLED, moment)
    return Fill(order=order, trades=tuple(trades))


async def _sellable(
    session: AsyncSession, constants: Constants, node: Node, sell_order: Order, floor: int
) -> int:
    """How much of a sell order's lot clears the buyer's floor (D-239).

    The order says how much is committed, the stacks say how good it is: a
    seller holding six poor ingots and four fine ones under one order hands a
    buyer who wants no worse than 75 exactly four, and the rest of the order
    waits for somebody less particular.

    A floor at or below the tier's own start needs no counting at all -- every
    stack in the tier clears it by definition -- and that is the ordinary case,
    the one a tier button makes.
    """
    if floor <= tier_span(constants, sell_order.tier)[0]:
        return sell_order.amount_left
    #: A resting sell order always has a cell behind it: this is a read, and a
    #: read does not create rows.
    stock = await stall(session, node, sell_order.identity_id, create=False)
    if stock is None:  # pragma: no cover -- a sell order without its cell is a bug
        return 0
    items = await _stacks(
        session, stock, sell_order.type_key, sell_order.tier, constants, floor=floor
    )
    return min(sell_order.amount_left, sum(item.amount for item in items))


async def _counterparts(
    session: AsyncSession, constants: Constants, order: Order
) -> Sequence[Order]:
    """Opposing orders acceptable by price and reachable by quality, in fill order.

    A buy reaches its own tier and every tier above it; a sell is reached by
    every buy whose floor its tier can still satisfy (D-239). The stacks decide
    the rest -- this only refuses to fetch what cannot possibly fit.
    """
    other = OrderSide.SELL if order.side is OrderSide.BUY else OrderSide.BUY
    if order.side is OrderSide.BUY:
        floor = _floor_of(constants, order)
        reachable = [step.name for step in constants[R.QUALITY_TIERS] if step.to >= floor]
        quality_fits: ColumnElement[bool] = Order.tier.in_(reachable)
    else:
        quality_fits = _floor_sql(constants) <= tier_span(constants, order.tier)[1]
    stmt = select(Order).where(
        Order.node_id == order.node_id,
        Order.type_key == order.type_key,
        quality_fits,
        Order.side == other,
        Order.state == OrderState.ACTIVE,
        Order.id != order.id,
        #: A deal with one's own order is meaningless: money and goods would
        #: return to the same owner, and city turnover would grow out of nothing.
        Order.identity_id != order.identity_id,
    )
    if order.side is OrderSide.BUY:
        stmt = stmt.where(Order.price <= order.price).order_by(Order.price, Order.created_at)
    else:
        stmt = stmt.where(Order.price >= order.price).order_by(Order.price.desc(), Order.created_at)
    #: Locked for the match: `amount_left` of a maker is decremented below,
    #: and a concurrent taker must see the decrement, not the snapshot.
    stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalars().all()


async def _execute(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    taker: Order,
    maker: Order,
    moment: datetime,
    *,
    quantity: int,
    floor: int,
) -> Trade:
    """One deal: goods to the buyer, money to the seller, tax to the city.

    The tier of the deal is the **seller's**: a buy reaches above its own
    window (D-239), and what changes hands is the lot that exists, not the
    window the demand was written in.
    """
    #: The price of the one resting in the book: they named the terms first.
    price = maker.price
    cost = _cost(price, quantity)

    buy_order = taker if taker.side is OrderSide.BUY else maker
    sell_order = maker if taker.side is OrderSide.BUY else taker

    node = await session.get(Node, taker.node_id)
    if node is None:  # pragma: no cover
        raise MarketError(key="market-order-off-node")

    #: The goods travel from the seller's cell to the buyer's, staying in the
    #: terminal: they are still taken on foot (D-047).
    seller_stall = await stall(session, node, sell_order.identity_id)
    buyer_stall = await stall(session, node, buy_order.identity_id)
    moved = await _move(
        session,
        seller_stall,
        buyer_stall,
        sell_order.type_key,
        quantity,
        tier=sell_order.tier,
        constants=constants,
        floor=floor,
    )
    if moved < quantity:  # pragma: no cover -- the goods are held by the order
        raise NoGoods(key="market-goods-vanished-trade")

    tax_rate, fee_rate = await _charges(session, constants, catalog, node)
    tax = int(cost * tax_rate / PERCENT)
    fee = int(cost * fee_rate / PERCENT)

    trade = Trade(
        node_id=node.id,
        buy_order_id=buy_order.id,
        sell_order_id=sell_order.id,
        type_key=sell_order.type_key,
        tier=sell_order.tier,
        price=price,
        amount=quantity,
        tax=tax,
        fee=fee,
    )
    session.add(trade)

    taker.amount_left -= quantity
    maker.amount_left -= quantity
    await session.flush()

    event = await events.record(
        session,
        EventKind.TRADE_EXECUTED,
        actor_identity_id=buy_order.identity_id,
        node_id=node.id,
        trade_id=str(trade.id),
        type_key=sell_order.type_key,
        tier=sell_order.tier,
        price=price,
        amount=amount_float(quantity),
        seller=str(sell_order.identity_id),
        tax=tax,
        fee=fee,
    )
    await _settle(session, buy_order, sell_order, node, cost, tax, fee, event_id=event.id)
    #: Bought cheaper than one was ready to pay -- the difference is released at
    #: once rather than waiting for the order to close. Exactly what may be needed is frozen.
    await _release(
        session, buy_order, buy_order.escrowed - _cost(buy_order.price, buy_order.amount_left)
    )
    #: The filled maker is closed **after** the money has moved, not before.
    #: Closing a buy returns everything still frozen under it -- and the seller
    #: is paid out of exactly that: closed first, the settlement finds an empty
    #: escrow and a sell into a resting buy fails outright.
    if maker.amount_left <= 0:
        await _close(session, maker, OrderState.FILLED, moment)
    return trade


async def _settle(
    session: AsyncSession,
    buy_order: Order,
    sell_order: Order,
    node: Node,
    cost: int,
    tax: int,
    fee: int,
    *,
    event_id: int,
) -> None:
    """Settlement of a deal: from the buyer's escrow to the seller, tax and commission to the city.

    The seller gets exactly what the buyer paid, minus tax and commission (I2).
    Not a coin appears or vanishes.
    """
    escrow = await ledger.account_for(session, AccountKind.ESCROW, buy_order.identity_id)
    seller = await ledger.account_for(session, AccountKind.IDENTITY, sell_order.identity_id)

    postings = [ledger.Posting(escrow.id, -cost), ledger.Posting(seller.id, cost - tax - fee)]
    if tax or fee:
        #: The treasury account is created on the city's delegate node: the
        #: same place as the energy pool, and it is one and the same account (D-154).
        postings.append(ledger.Posting((await _treasury(session, node)).id, tax + fee))

    await ledger.post(
        session,
        PostingReason.TRADE,
        postings,
        event_id=event_id,
        memo={"tax": tax, "fee": fee, "цена": money_str(cost)},
    )
    buy_order.escrowed -= cost


async def _hold(session: AsyncSession, order: Order, sum_minor: int) -> None:
    """Freeze the buyer's money under an order."""
    account = await ledger.account_for(session, AccountKind.IDENTITY, order.identity_id)
    escrow = await ledger.account_for(session, AccountKind.ESCROW, order.identity_id)
    await ledger.transfer(
        session,
        PostingReason.ESCROW_HOLD,
        debit=account.id,
        credit=escrow.id,
        amount=sum_minor,
        memo={"order": str(order.id)},
    )
    order.escrowed += sum_minor
    await session.flush()


async def _release(session: AsyncSession, order: Order, sum_minor: int | None = None) -> None:
    """Return the frozen money to the buyer -- all of it or a named part."""
    back = order.escrowed if sum_minor is None else min(sum_minor, order.escrowed)
    if back <= 0:
        return
    account = await ledger.account_for(session, AccountKind.IDENTITY, order.identity_id)
    escrow = await ledger.account_for(session, AccountKind.ESCROW, order.identity_id)
    await ledger.transfer(
        session,
        PostingReason.ESCROW_RELEASE,
        debit=escrow.id,
        credit=account.id,
        amount=back,
        memo={"order": str(order.id)},
    )
    order.escrowed -= back


async def _close(session: AsyncSession, order: Order, state: OrderState, moment: datetime) -> None:
    order.state = state
    order.closed_at = moment
    if order.side is OrderSide.BUY:
        await _release(session, order)
    await session.flush()


async def _treasury(session: AsyncSession, node: Node):
    """The treasury account of the city that owns the node.

    The node's owner is stored as a city id, and the treasury account is
    created on its delegate node -- where the energy pool lives too. One place
    for all city money: otherwise taxes and tariff would land in different pockets.
    """

    city = await town.by_id(session, node.owner_city_id)
    if city is None:  # pragma: no cover -- an owner without a city is a bug
        raise MarketError(key="market-node-city-missing", node=node.key)
    return await town.treasury(session, city)


async def _charges(
    session: AsyncSession, constants: Constants, catalog: Catalog, node: Node
) -> tuple[float, float]:
    """The sales tax rate and terminal commission for the node.

    The **city** sets the rate (D-127, D-154): the engine takes the value in
    force of its code-law `tax_trade`. A city that decided nothing lives on the
    `laws.json` default -- a new city works without filling in anything (D-130).
    Commission is `market.default_fee` until the terminal owner sets its own.

    **The node is unowned -- no withholdings at all.** Not because it is meant
    that way, but because there is nobody to pay them to: money cannot vanish
    into nowhere (I2).
    """

    if node.owner_city_id is None:
        return 0.0, 0.0
    city = await town.by_id(session, node.owner_city_id)
    return (
        town.law_number(constants, catalog, city, "tax_trade"),
        constants[R.MARKET_DEFAULT_FEE],
    )
