# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The entrances: what a player may do to the book, and what time does to it.

`sell` is remote -- the goods are already delivered (D-047); `buy` demands a
present body, or the books of all cities get bought out without standing up.
The reservation (`reserve`/`redeem`/`lapse`) is the one exception to "buy only
standing here", priced by a deposit and a term. `cancel` is a player's word,
`expire` the journal's: an order's term runs out even if nobody looks into the
book.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current_catalog
from src.constants import registry as R
from src.engine import events, ledger, travel
from src.engine.jobs import enqueue, handler
from src.engine.market._base import (
    BadOrder,
    Fill,
    MarketError,
    NoGoods,
    NoMoney,
    NotHere,
    NotYours,
    _cost,
    _floor_sane,
    _sane,
    _tradable,
    _volume,
    tier_of,
    tier_span,
)
from src.engine.market.counter import _free, _move, _node_of, stall, terminal
from src.engine.market.match import _charges, _close, _hold, _match, _place, _treasury
from src.models.event import EventKind
from src.models.identity import Body, BodyState, Identity
from src.models.job import Job, JobKind
from src.models.ledger import AccountKind, LedgerAccount, PostingReason
from src.models.market import (
    Order,
    OrderSide,
    OrderState,
    Reservation,
    ReservationState,
    Trade,
)
from src.models.world import Node
from src.units import PERCENT, amount_float, money_str


async def sell(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    identity: Identity,
    node: Node,
    *,
    type_key: str,
    tier: str,
    price: int,
    quantity: float,
    now: datetime | None = None,
) -> Fill:
    """List a sell order. Remote: the goods are already delivered (D-047)."""
    await terminal(session, node)
    #: From here on the goods are known by the name the world gave them: what
    #: came in may have been a synonym, and a book of two names for one thing
    #: is two books.
    type_key = _tradable(constants, catalog, type_key, tier)
    want = _volume(catalog, type_key, quantity)
    _sane(price, want)

    free = await _free(session, constants, node, identity.id, type_key, tier)
    if free < want:
        raise NoGoods(
            key="market-not-enough-free",
            free=amount_float(free),
            goods=type_key,
            tier=tier,
            quantity=quantity,
        )

    order = await _place(
        session, constants, identity, node, OrderSide.SELL, type_key, tier, price, want, now=now
    )
    return await _match(session, constants, catalog, order, now=now)


async def buy(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    *,
    type_key: str,
    tier: str,
    price: int,
    quantity: float,
    min_quality: int | None = None,
    now: datetime | None = None,
) -> Fill:
    """Buy: a limit order from a present body, at a quality floor (D-239).

    Presence is required precisely here. Allow remote buying -- and the books
    of all cities get bought out without leaving one's seat (D-047).

    The floor is what the buyer will not go below, and the tier button is a
    floor too -- the start of its band: "хорошее" means "no worse than 60" and
    takes "отличное" along with it. Better goods at the price one named are no
    loss, and demand gathers on thresholds instead of scattering over five
    books.

    The order stands in the window where its floor begins, and the tier it is
    written on must be that window: a floor of 75 belongs to "хорошее", and an
    order that names "отличное" beside it is refused rather than moved.
    """
    if body.state is not BodyState.ALIVE:
        raise NotHere(key="market-dead-trades")
    node = await _node_of(session, body)
    await terminal(session, node)

    type_key = _tradable(constants, catalog, type_key, tier)
    floor = int(tier_span(constants, tier)[0]) if min_quality is None else int(min_quality)
    _floor_sane(constants, floor)
    #: The window an order stands in is the one its floor begins: named
    #: together, the two must agree. Quietly moving the order to another
    #: window would answer a request nobody made -- and whoever sends it need
    #: not be this game's own screen (D-224).
    if tier_of(constants, float(floor)) != tier:
        raise BadOrder(
            key="market-floor-not-in-tier",
            floor=floor,
            floor_tier=tier_of(constants, float(floor)),
            tier=tier,
        )
    want = _volume(catalog, type_key, quantity)
    _sane(price, want)

    identity = await session.get(Identity, body.identity_id)
    if identity is None:  # pragma: no cover
        raise MarketError(key="market-body-without-identity")

    order = await _place(
        session,
        constants,
        identity,
        node,
        OrderSide.BUY,
        type_key,
        tier,
        price,
        want,
        min_quality=floor,
        now=now,
    )
    try:
        await _hold(session, order, _cost(price, want))
    except ledger.InsufficientFunds as empty:
        #: The ledger's own sentence names an account id and minor units --
        #: not words for a player. The refusal is restated here, in the money
        #: the order asked for.
        raise NoMoney(key="market-not-enough-money", money=money_str(_cost(price, want))) from empty
    return await _match(session, constants, catalog, order, now=now)


async def reserve(
    session: AsyncSession,
    constants: Constants,
    identity: Identity,
    order: Order,
    quantity: float,
    *,
    now: datetime | None = None,
) -> Reservation:
    """Reserve a lot from somebody else's sell order (D-047).

    A remote action -- and the only exception to the rule "buy only standing
    here". A merchant preparing for the road reserves a lot, pays
    `market.reservation_deposit` of the sum and must collect before the
    `market.reservation_period` deadline. Did not collect -- the deposit stays
    with the seller.

    The goods **leave the book** but stay in the seller's cell: they go nowhere
    until somebody comes for them. Dead reserves do not arise exactly because a
    reservation has a term and a price.
    """
    moment = now or datetime.now(UTC)
    #: Lock order on the market: payer's account -> orders -> other accounts.
    #: `buy` holds the buyer's account (`_hold`) before it locks the sell
    #: orders; a reservation by the same identity from a second socket must
    #: take them in the same order, or the two deadlock.
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    await session.execute(
        select(LedgerAccount.id).where(LedgerAccount.id == account.id).with_for_update()
    )
    #: The order row is locked and reread before its remainder is read: two
    #: buyers reserving the last ten at once must queue, not both succeed
    #: (review 2026-08-23).
    await session.refresh(order, with_for_update=True)
    if order.side is not OrderSide.SELL:
        raise BadOrder(key="market-reserve-not-a-sale")
    if order.state is not OrderState.ACTIVE:
        raise BadOrder(key="market-order-not-active", state=order.state.value)
    if order.identity_id == identity.id:
        raise NotYours(key="market-reserve-own")

    want = _volume(current_catalog(), order.type_key, quantity)
    if want <= 0:
        raise BadOrder(key="market-reserve-zero")
    if want > order.amount_left:
        raise NoGoods(
            key="market-reserve-too-much",
            free=amount_float(order.amount_left),
            quantity=quantity,
        )

    cost = _cost(order.price, want)
    deposit = int(cost * constants[R.MARKET_RESERVATION_DEPOSIT] / PERCENT)
    escrow = await ledger.account_for(session, AccountKind.ESCROW, identity.id)
    await ledger.transfer(
        session,
        PostingReason.ESCROW_HOLD,
        debit=account.id,
        credit=escrow.id,
        amount=deposit,
        memo={"бронь": order.type_key, "цена": money_str(order.price)},
    )

    #: The goods leave the book: somebody else's book must not show what is
    #: already promised to another.
    order.amount_left -= want
    term = timedelta(hours=constants[R.MARKET_RESERVATION_PERIOD] * constants[R.TIME_DAY_TERRA])
    reservation = Reservation(
        order_id=order.id,
        node_id=order.node_id,
        buyer_identity_id=identity.id,
        seller_identity_id=order.identity_id,
        type_key=order.type_key,
        tier=order.tier,
        price=order.price,
        amount=want,
        deposit=deposit,
        expires_at=moment + term,
    )
    session.add(reservation)
    await session.flush()

    event = await events.record(
        session,
        EventKind.RESERVATION_HELD,
        actor_identity_id=identity.id,
        node_id=order.node_id,
        reservation_id=str(reservation.id),
        order_id=str(order.id),
        type_key=order.type_key,
        amount=amount_float(want),
        deposit=deposit,
        expires_at=reservation.expires_at.isoformat(),
    )
    await enqueue(
        session,
        JobKind.MARKET_RESERVATION_EXPIRY,
        reservation.expires_at,
        payload={"reservation": str(reservation.id)},
        dedup_key=f"market.reservation:{reservation.id}",
        cause_event_id=event.id,
    )
    return reservation


async def redeem(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    reservation: Reservation,
    *,
    now: datetime | None = None,
) -> Trade:
    """Redeem a reservation: pay the remainder and take the goods. In person.

    Coming is mandatory -- that is the whole point: a reservation does not
    cancel geography, it lets you plan it.
    """
    moment = now or datetime.now(UTC)
    await travel.require_here(session, body)
    #: Redeem and lapse race for the same row: whoever locks it first wins.
    await session.refresh(reservation, with_for_update=True)
    if reservation.buyer_identity_id != body.identity_id:
        raise NotYours(key="market-reservation-not-yours")
    if reservation.state is not ReservationState.HELD:
        raise BadOrder(key="market-reservation-not-held", state=reservation.state.value)
    if reservation.node_id != body.node_id:
        raise MarketError(key="market-reservation-elsewhere")
    if moment > reservation.expires_at:
        raise BadOrder(key="market-reservation-expired")

    node = await session.get(Node, reservation.node_id)
    await terminal(session, node)

    cost = _cost(reservation.price, reservation.amount)
    remainder = cost - reservation.deposit
    account = await ledger.account_for(session, AccountKind.IDENTITY, reservation.buyer_identity_id)
    escrow = await ledger.account_for(session, AccountKind.ESCROW, reservation.buyer_identity_id)
    if remainder > 0:
        await ledger.transfer(
            session,
            PostingReason.ESCROW_HOLD,
            debit=account.id,
            credit=escrow.id,
            amount=remainder,
            memo={"выкуп брони": reservation.type_key},
        )

    #: The goods travel from the seller's cell to the buyer's, staying in the
    #: terminal: they are still taken on foot (D-047).
    seller = await stall(session, node, reservation.seller_identity_id)
    buyer = await stall(session, node, reservation.buyer_identity_id)
    moved = await _move(
        session,
        seller,
        buyer,
        reservation.type_key,
        reservation.amount,
        tier=reservation.tier,
        constants=constants,
    )
    if moved < reservation.amount:  # pragma: no cover -- the goods are held by the reservation
        raise NoGoods(key="market-goods-vanished-reservation")

    tax_rate, fee_rate = await _charges(session, constants, catalog, node)
    tax = int(cost * tax_rate / PERCENT)
    fee = int(cost * fee_rate / PERCENT)

    #: A reservation is a deal without an opposing order: the buyer placed none.
    trade = Trade(
        node_id=node.id,
        buy_order_id=None,
        sell_order_id=reservation.order_id,
        type_key=reservation.type_key,
        tier=reservation.tier,
        price=reservation.price,
        amount=reservation.amount,
        tax=tax,
        fee=fee,
    )
    session.add(trade)
    reservation.state = ReservationState.REDEEMED
    reservation.closed_at = moment
    await session.flush()

    event = await events.record(
        session,
        EventKind.TRADE_EXECUTED,
        actor_identity_id=reservation.buyer_identity_id,
        node_id=node.id,
        trade_id=str(trade.id),
        reservation_id=str(reservation.id),
        type_key=reservation.type_key,
        tier=reservation.tier,
        price=reservation.price,
        amount=amount_float(reservation.amount),
        seller=str(reservation.seller_identity_id),
        tax=tax,
        fee=fee,
    )

    seller_acct = await ledger.account_for(
        session, AccountKind.IDENTITY, reservation.seller_identity_id
    )
    postings = [
        ledger.Posting(escrow.id, -cost),
        ledger.Posting(seller_acct.id, cost - tax - fee),
    ]
    if tax or fee:
        #: The treasury account is created on the city's delegate node: the
        #: same place as the energy pool, and it is one and the same account (D-154).
        postings.append(ledger.Posting((await _treasury(session, node)).id, tax + fee))
    await ledger.post(
        session,
        PostingReason.TRADE,
        postings,
        event_id=event.id,
        memo={"выкуп брони": money_str(cost), "tax": tax, "fee": fee},
    )
    return trade


@handler(JobKind.MARKET_RESERVATION_EXPIRY)
async def lapse(session: AsyncSession, job: Job) -> None:
    """The reservation term is up: deposit to the seller, goods back to the book (D-047)."""
    reservation = await session.get(
        Reservation, uuid.UUID(job.payload["reservation"]), with_for_update=True
    )
    if reservation is None:  # pragma: no cover
        raise MarketError(key="market-job-no-reservation", job=str(job.id))
    if reservation.state is not ReservationState.HELD:
        return

    #: The order is locked before any money moves: every path on the market
    #: takes orders first and accounts second, and this one must not be the
    #: exception that closes a cycle with `buy` (review 2026-08-23).
    order = await session.get(Order, reservation.order_id, with_for_update=True)

    escrow = await ledger.account_for(session, AccountKind.ESCROW, reservation.buyer_identity_id)
    seller = await ledger.account_for(session, AccountKind.IDENTITY, reservation.seller_identity_id)
    #: The deposit is payment for the goods having waited: it stays with the seller.
    await ledger.transfer(
        session,
        PostingReason.ESCROW_RELEASE,
        debit=escrow.id,
        credit=seller.id,
        amount=reservation.deposit,
        memo={"просроченная бронь": reservation.type_key},
    )

    #: The goods return to the book if the order is still alive. A cancelled
    #: order keeps them in the seller's cell -- they are at home anyway.
    if order is not None and order.state is OrderState.ACTIVE:
        order.amount_left += reservation.amount

    reservation.state = ReservationState.LAPSED
    reservation.closed_at = job.run_at
    await session.flush()
    await events.record(
        session,
        EventKind.RESERVATION_LAPSED,
        actor_identity_id=reservation.buyer_identity_id,
        node_id=reservation.node_id,
        reservation_id=str(reservation.id),
        deposit=reservation.deposit,
    )


async def cancel(
    session: AsyncSession, order: Order, *, by: uuid.UUID, now: datetime | None = None
) -> Order:
    """Cancel an order. A remote action: disposing requires no presence."""
    await session.refresh(order, with_for_update=True)
    if order.identity_id != by:
        raise NotYours(key="market-order-not-yours")
    if order.state is not OrderState.ACTIVE:
        raise BadOrder(key="market-order-already", state=order.state.value)
    await _close(session, order, OrderState.CANCELLED, now or datetime.now(UTC))
    await events.record(
        session,
        EventKind.ORDER_CANCELLED,
        actor_identity_id=order.identity_id,
        node_id=order.node_id,
        order_id=str(order.id),
    )
    return order


@handler(JobKind.MARKET_ORDER_EXPIRY)
async def expire(session: AsyncSession, job: Job) -> None:
    """The order term is up. Expiry is a world event, not a consequence of reading."""
    order = await session.get(Order, uuid.UUID(job.payload["order"]), with_for_update=True)
    if order is None:  # pragma: no cover
        raise MarketError(key="market-job-no-order", job=str(job.id))
    if order.state is not OrderState.ACTIVE:
        return

    await _close(session, order, OrderState.EXPIRED, job.run_at)
    await events.record(
        session,
        EventKind.ORDER_EXPIRED,
        actor_identity_id=order.identity_id,
        node_id=order.node_id,
        order_id=str(order.id),
    )
