# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The node's order book (D-003, D-047, D-127).

The order book is local and only local: a single price would collapse
geography and kill the hauler along with arbitrage (pillar P3). The engine does
not value goods -- it matches opposing orders, and the price is what somebody
agreed to pay (D-002).

## What happens where

| Action | Where | Why |
|---|---|---|
| Load goods into the terminal | in person | matter moves only physically |
| List, cancel a sell order | remote | the goods are delivered, from then on an exchange asset |
| Buy | in person | otherwise a player buys everything everywhere without standing up |
| Take bought goods | in person | the same rule of matter |
| View any city's books | remote | everyone knows the prices (D-047) |

Buying means placing a limit buy order, and therefore requires presence: remote
buying would turn the books into a fiction of stuck reserves. The unfilled
remainder rests in the book, money is frozen under it, on fill the goods land
in the terminal -- to be taken on foot.

## Where each formula came from

**A tier, not a number.** Goods trade as positions like "iron ore, good": tiers
come from `quality.tiers` (D-058). A continuous scale would make the book
unreadable and kill liquidity.

**What may stand in the book** (D-241). A thing the world knows how to hand
over, at one of those tiers, under the world's own name for it. Not a name
nobody has heard of, not a relic of the Forerunners -- nobody makes or carries
those (D-232) -- and not a liquid, which exists inside a vessel and nowhere
else (D-230). The rule lives here and not in the client's picker: a buy order
freezes money until it fills or expires, and whoever sends the order need not
be the game's own screen (D-224).

**Priority.** Best price; at equal price, whoever came first. A deal goes at
the price of **the one resting in the book**: they named the terms first, the
newcomer accepted. There are no market orders at all -- only limit ones,
simpler and fairer (30-economy/02, open questions).

**Money.** The buyer freezes `price * volume` on placing the order. Filled
cheaper -- the difference is returned at once: exactly as much is frozen as
may be needed, and not a coin more.

**Tax and commission.** `tax_trade` is paid by the **seller** as a share of
proceeds at fill time (D-127): the buyer sees the price in the book, and that
is the price. Terminal commission is `market.default_fee` until the city sets
its own. Both go to the treasury of the city that owns the node; **no city --
no withholdings**: money cannot vanish into nowhere (I2).

**Term.** An order lives `market.order_lifetime` Terran days and is cancelled
by a journal job, not by a check on read: expiry must happen even if nobody
looks into the book.

**Reservation with a deposit** is the only exception to "buy only standing
here" (D-047). A merchant reserves a lot from afar, pays
`market.reservation_deposit` and must collect within
`market.reservation_period` days; if not, the deposit stays with the seller and
the goods return to the book. Dead reserves do not arise because a reservation
has a price and a term.

**No more than the limit is taken in hand** (D-146): what stops you taking
bought goods is not the terminal's greed but mass. Everything beyond -- only by
vehicle.

## What is not here yet

* **Price ceiling, sales norm, duties** (D-122, D-123) -- city code-laws,
  arrive with cities on E3;
* **Orphaned terminal** (D-100) -- requires building maintenance, i.e.
  buildings and a treasury.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, ConstantError, Constants, current, current_catalog
from src.constants import current_catalog as _catalog
from src.constants import registry as R
from src.engine import city as town
from src.engine import craft, events, gear, goods, ledger, travel, world
from src.engine.errors import Refusal
from src.engine.jobs import enqueue, handler
from src.engine.world import body_container, node_container, station_names
from src.models.event import EventKind
from src.models.identity import Body, BodyState, Identity
from src.models.inventory import Container, ContainerKind, Item
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
from src.runtime import MARKET_BOOK_STEPS
from src.units import AMOUNT_SCALE, PERCENT, amount, amount_float, money_str


class MarketError(Refusal):
    pass


class NoTerminal(MarketError):
    """No terminal in the node. One marketplace per city (D-100)."""


class NotHere(MarketError):
    """The body is in the wrong node. Matter requires presence (D-044)."""


class NotYours(MarketError):
    pass


class NoGoods(MarketError):
    """The goods are not in the terminal, or already committed to another order."""


class BadOrder(MarketError):
    """The order is meaningless: zero volume, zero price, foreign tier."""


class Untradable(MarketError):
    """The goods cannot lie on a counter, so an order for them can never fill.

    Three ways that happens: the world knows no such thing at all, the thing is
    a relic of the Forerunners -- found, never made, never carried away
    (D-232) -- or it is a liquid, which exists inside a vessel and nowhere else
    (D-230, D-241). A buy order freezes money until it fills or expires, so an order
    that cannot fill is not a harmless typo but a stretch of somebody's purse
    locked for nothing.
    """


class NoMoney(MarketError):
    """Nothing to pay with. This is an in-game situation, not a server error."""


#: The thing class of marketplace terminals (D-100, D-215).
TERMINAL = "Терминал"


@dataclass(frozen=True, slots=True)
class Level:
    """One rung of the book: a price and the whole volume at it."""

    price: int
    amount: float


@dataclass(frozen=True, slots=True)
class Book:
    """The book for one position: goods plus quality tier."""

    node: uuid.UUID
    type_key: str
    tier: str
    bids: tuple[Level, ...] = ()
    asks: tuple[Level, ...] = ()
    last: int | None = None
    #: The price step the rows are glued at, minor units. One means every
    #: price stands on its own row -- the book as the orders were written.
    step: int = 1

    @property
    def spread(self) -> int | None:
        if not self.bids or not self.asks:
            return None
        return self.asks[0].price - self.bids[0].price


@dataclass(frozen=True, slots=True)
class Fill:
    """What happened on placing an order."""

    order: Order
    trades: tuple[Trade, ...] = field(default_factory=tuple)

    @property
    def traded(self) -> float:
        return amount_float(sum(trade.amount for trade in self.trades))


# --- goods keys ----------------------------------------------------------------

#: A written knowledge carrier is a different good for every recipe on it
#: (D-209): a buyer of "Рецепт" must know **which**. On the counter it is
#: keyed as "Рецепт: Стекло" -- one string, so that orders, books and offers
#: work unchanged -- and split back into type and recipe where stacks are read.
CARRIER_SEP = ": "


def goods_key(item: Item) -> str:
    """The name the counter knows this stack by."""

    if item.type_key in craft.carrier_names() and item.recipe_key:
        return f"{item.type_key}{CARRIER_SEP}{item.recipe_key}"
    return item.type_key


def split_key(goods: str, catalog: Catalog | None = None) -> tuple[str, str | None]:
    """A counter name back into item type and, for a carrier, the recipe on it.

    The catalog is worth passing wherever the caller already holds one --
    whether a name is a carrier decides what the name **means**. The inner
    paths (`_stacks`, `_carrier`) deliberately ask the holder instead: they run
    where no catalog is threaded, and threading one to them is a change of its
    own rather than a line in this one.
    """

    head, sep, tail = goods.partition(CARRIER_SEP)
    if sep and head in craft.carrier_names(catalog) and tail:
        return head, tail
    return goods, None


# --- tiers -------------------------------------------------------------------


def tier_of(constants: Constants, quality: float | None) -> str:
    """The goods' quality tier. Five tiers are the book's shop window (D-058).

    A band stretches from its own start to the start of the next: bounds in the
    data are integers (..39, 40..), quality is fractional, and 39.5 must fall
    into the lower band rather than drop between them.
    """
    tiers = constants[R.QUALITY_TIERS]
    if quality is None:
        #: Energy and money have no quality at all -- the whole position is one.
        return tiers[0].name
    fitting = [tier for tier in sorted(tiers, key=lambda t: t.frm) if tier.frm <= quality]
    return fitting[-1].name if fitting else tiers[0].name


def tier_span(constants: Constants, tier: str) -> tuple[int, int]:
    """The quality a tier covers, both ends included. The floor rules live off it (D-239)."""
    for step in constants[R.QUALITY_TIERS]:
        if step.name == tier:
            return step.frm, step.to
    raise BadOrder(f"нет такой ступени качества: «{tier}»")


def _floor_of(constants: Constants, order: Order) -> int:
    """What the order will not go below.

    A buy written before the floor existed says nothing, and its tier's own
    start is read as the floor -- exactly what its tier button meant (D-239).
    A sell has no floor at all: it offers a lot, not a demand.
    """
    if order.min_quality is not None:
        return order.min_quality
    #: Down, never up: a band that starts at 39.5 must not have a floor that
    #: refuses the very stacks standing in it.
    return int(tier_span(constants, order.tier)[0])


def _floor_sql(constants: Constants) -> ColumnElement[int]:
    """The same floor, asked of the database: rows are picked by it, not read one by one."""
    return func.coalesce(
        Order.min_quality,
        case(
            {step.name: int(step.frm) for step in constants[R.QUALITY_TIERS]},
            value=Order.tier,
            else_=0,
        ),
    )


# --- terminal ----------------------------------------------------------------


async def terminal(session: AsyncSession, node: Node) -> Item:
    """The node's terminal. No terminal -- no trade, as there is none in an open field."""
    where = await node_container(session, node)

    found = (
        await session.execute(
            select(Item)
            .where(
                Item.container_id == where.id,
                Item.type_key.in_(station_names(TERMINAL)),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if found is None:
        raise NoTerminal(f"в узле {node.key} нет терминала маркетплейса")
    #: A terminal in a frozen node is silent (D-231): the machine is here, the
    #: heat is not, and the rule is the same one that stops the workbench.
    from src.engine import frost  # noqa: PLC0415 -- lazy: breaks the import cycle with frost

    await frost.require_working(session, current(), node, found.type_key)
    return found


async def stall(
    session: AsyncSession,
    node: Node,
    identity_id: uuid.UUID,
    *,
    create: bool = True,
    lock: bool = False,
) -> Container | None:
    """The identity's cell in the node's terminal: its loaded goods and its purchases.

    With `create=False` a missing cell is None, not a new row: reads
    (`look`) must not write. With `lock=True` the cell's row is locked for
    the transaction: `sell`, `load` and `take` all read "what is free" and
    then move it, and two of them at once on one cell must queue (review
    2026-08-23). Lock order on the market: cell -> orders -> accounts."""
    stmt = select(Container).where(
        Container.kind == ContainerKind.MARKET,
        Container.owner_id == identity_id,
        Container.node_id == node.id,
    )
    if lock:
        stmt = stmt.with_for_update()
    container = (await session.execute(stmt)).scalar_one_or_none()
    if container is None and create:
        container = Container(kind=ContainerKind.MARKET, owner_id=identity_id, node_id=node.id)
        session.add(container)
        await session.flush()
    return container


async def load(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    type_key: str,
    quantity: float,
    *,
    tier: str | None = None,
) -> float:
    """Load goods into the terminal. In person: goods are carried on foot.

    `tier` names which stacks go: without it the worst go first, and the good
    ore stays in the sack for the smelt it was mined for (D-058).
    """
    node = await _node_of(session, body)
    await terminal(session, node)
    inventory = await body_container(session, body)
    into = await stall(session, node, body.identity_id, lock=True)

    moved = await _move(
        session,
        inventory,
        into,
        type_key,
        _volume(_catalog(), type_key, quantity),
        tier=tier,
        constants=constants,
    )
    await events.record(
        session,
        EventKind.MARKET_LOADED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        type_key=type_key,
        amount=amount_float(moved),
    )
    return amount_float(moved)


async def take(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    type_key: str,
    quantity: float,
    *,
    tier: str | None = None,
) -> float:
    """Take your own from the terminal. What is committed to an order is not given twice."""
    node = await _node_of(session, body)
    await terminal(session, node)
    stock = await stall(session, node, body.identity_id, lock=True)
    inventory = await body_container(session, body)

    free = await _free(session, constants, node, body.identity_id, type_key, tier)
    want = min(_volume(_catalog(), type_key, quantity), free)
    if want <= 0:
        raise NoGoods(f"свободного «{type_key}» в терминале нет: всё под ордерами")

    #: No more than the limit is taken in hand: for the rest come with a wagon (D-146).

    await gear.check_carry(
        session,
        constants,
        current_catalog(),
        body,
        split_key(type_key)[0],
        amount_float(want),
    )

    moved = await _move(session, stock, inventory, type_key, want, tier=tier, constants=constants)
    await events.record(
        session,
        EventKind.MARKET_TAKEN,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        type_key=type_key,
        amount=amount_float(moved),
    )
    return amount_float(moved)


# --- orders ------------------------------------------------------------------


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
            f"в терминале свободно {amount_float(free)} «{type_key}» ступени «{tier}», "
            f"нужно {quantity}"
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
        raise NotHere("мёртвое тело не торгует")
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
            f"качество {floor} — это ступень «{tier_of(constants, float(floor))}», "
            f"а заявка выписана на «{tier}»"
        )
    want = _volume(catalog, type_key, quantity)
    _sane(price, want)

    identity = await session.get(Identity, body.identity_id)
    if identity is None:  # pragma: no cover
        raise MarketError("тело без личности")

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
        raise NoMoney(str(empty)) from empty
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
        raise BadOrder("бронируют товар, а не заявку на покупку")
    if order.state is not OrderState.ACTIVE:
        raise BadOrder(f"заявка уже {order.state.value}")
    if order.identity_id == identity.id:
        raise NotYours("свой товар бронировать незачем: он и так ваш")

    want = _volume(current_catalog(), order.type_key, quantity)
    if want <= 0:
        raise BadOrder("бронь из нуля")
    if want > order.amount_left:
        raise NoGoods(
            f"в заявке свободно {amount_float(order.amount_left)}, а брони просят {quantity}"
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
        raise NotYours("чужая бронь")
    if reservation.state is not ReservationState.HELD:
        raise BadOrder(f"бронь уже {reservation.state.value}")
    if reservation.node_id != body.node_id:
        raise MarketError("бронь не здесь: за товаром приезжают")
    if moment > reservation.expires_at:
        raise BadOrder("срок брони вышел: задаток остался продавцу")

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
        raise NoGoods("товар исчез из терминала между бронью и выкупом")

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
        raise MarketError(f"задание {job.id}: брони нет")
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
        raise NotYours("чужой ордер")
    if order.state is not OrderState.ACTIVE:
        raise BadOrder(f"ордер уже {order.state.value}")
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
        raise MarketError(f"задание {job.id}: ордера нет")
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


# --- reading -----------------------------------------------------------------


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


# --- internal ----------------------------------------------------------------


def _sane(price: int, want: int) -> None:
    if price <= 0:
        raise BadOrder("цена должна быть положительной")
    if want <= 0:
        raise BadOrder("объём должен быть положительным")


def _floor_sane(constants: Constants, floor: int) -> None:
    """The floor is a quality, and quality is the world's scale -- not any number."""
    tiers = sorted(constants[R.QUALITY_TIERS], key=lambda step: step.frm)
    if not tiers[0].frm <= floor <= tiers[-1].to:
        raise BadOrder(
            f"качество называют числом от {tiers[0].frm} до {tiers[-1].to}, а не {floor}"
        )


def _tradable(constants: Constants, catalog: Catalog, type_key: str, tier: str) -> str:
    """What may stand in the book at all, and under which name it stands.

    Called by the entrances rather than by `_place`, and before the volume is
    counted: a name nobody knows must be answered as a name, not as a fraction
    of a piece by `_volume`.

    A written knowledge carrier is one position per recipe -- "Рецепт: Стекло"
    (D-209) -- so the name splits first and both halves are asked about: a
    carrier of a recipe this world does not have is as undeliverable as a
    thing this world does not have.

    The canonical name is **returned**, not merely checked. "Железо" is a name
    of the iron ingot like any other, but stacks are stored under the ingot's
    own name and orders are matched by string: an order left as it came would
    rest in the book against nothing and hold the money until its term ran
    out. One name per position, and it is the world's name.

    Only the two entrances that **invent** a position ask this. `load` and
    `take` move a thing that already exists, and a thing that exists is by
    that fact deliverable: hanging the check on them would refuse a lot the
    world itself put in somebody's hands.
    """
    book = catalog.recipes
    kind, recipe = split_key(type_key, catalog)
    if not book.exists(kind):
        raise Untradable(f"в мире нет такого товара: «{kind}»")
    if book.is_relic(kind):
        raise Untradable(f"«{kind}» — наследие Предтеч: такое не делают и не переносят")
    if book.is_liquid(kind):
        raise Untradable(
            f"«{kind}» — жидкость: она бывает только в таре, на прилавок её не выложить"
        )
    if tier not in {step.name for step in constants[R.QUALITY_TIERS]}:
        raise BadOrder(f"нет такой ступени качества: «{tier}»")
    if recipe is None:
        return book.resolve(kind)
    try:
        written = book.recipe(recipe)
    except ConstantError as unknown:
        raise Untradable(f"в мире нет такого рецепта: «{recipe}»") from unknown
    return f"{book.resolve(kind)}{CARRIER_SEP}{written.name}"


def _volume(catalog: Catalog, type_key: str, quantity: float) -> int:
    """The order's volume in internal units: a counted thing trades whole (D-212).

    Half an ingot cannot be delivered, so it cannot be offered either -- and an
    order for it would sit in the book unfillable, which is worse than a refusal.
    """

    return amount(goods.at_least_one(type_key, quantity, catalog=catalog))


def _cost(price: int, quantity: int) -> int:
    """What a volume costs at a price. Integer: not a cent is lost."""
    return price * quantity // AMOUNT_SCALE


async def _node_of(session: AsyncSession, body: Body) -> Node:
    """The node the body **stands** in. In transit it is nowhere (D-107)."""
    await travel.require_here(session, body)
    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise MarketError("тело вне узла")
    return node


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
        raise MarketError("ордер вне узла")
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
        raise MarketError("ордер вне узла")

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
        raise NoGoods("товар исчез из терминала между проверкой и сделкой")

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
        raise MarketError(f"узел {node.key} принадлежит несуществующему городу")
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


async def _free(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    identity_id: uuid.UUID,
    type_key: str,
    tier: str | None,
) -> int:
    """How much of the goods in the terminal is not committed to orders.

    Locks the cell: `sell` reads this and then commits the goods to an order,
    and two sells at once must not both see the same free stock."""
    stock = await stall(session, node, identity_id, lock=True)
    items = await _stacks(session, stock, type_key, tier, constants)
    have = sum(item.amount for item in items)

    stmt = select(func.coalesce(func.sum(Order.amount_left), 0)).where(
        Order.node_id == node.id,
        Order.identity_id == identity_id,
        Order.type_key == type_key,
        Order.side == OrderSide.SELL,
        Order.state == OrderState.ACTIVE,
    )
    if tier is not None:
        stmt = stmt.where(Order.tier == tier)
    reserved = int(await session.scalar(stmt) or 0)
    return max(0, have - reserved)


async def _stacks(
    session: AsyncSession,
    container: Container,
    type_key: str,
    tier: str | None,
    constants: Constants,
    *,
    floor: int = 0,
) -> list[Item]:
    """Stacks of the needed goods, worst first: the good ones are saved.

    `floor` drops what is worse than a buyer agreed to take (D-239). A thing
    without quality at all -- energy, money -- has no way to clear a floor
    above zero, and is dropped by the same rule rather than by a name in code.
    """
    kind, recipe = split_key(type_key)
    stmt = select(Item).where(Item.container_id == container.id, Item.type_key == kind)
    if recipe is not None:
        stmt = stmt.where(Item.recipe_key == recipe)
    elif kind in _carrier():
        #: A bare "Рецепт" on the counter is a blank one -- a written carrier
        #: is always named together with what is on it.
        stmt = stmt.where(Item.recipe_key.is_(None))
    #: The stacks themselves are locked: `_move` splits and re-parents them,
    #: and two trades off one stack must see each other's decrement.
    rows = (
        (
            await session.execute(
                stmt.order_by(
                    Item.quality.asc().nulls_first(), Item.created_at.asc()
                ).with_for_update()
            )
        )
        .scalars()
        .all()
    )
    fitting = list(rows)
    if tier is not None:
        fitting = [item for item in fitting if tier_of(constants, _quality(item)) == tier]
    if floor > 0:
        good_enough = []
        for item in fitting:
            quality = _quality(item)
            if quality is not None and quality >= floor:
                good_enough.append(item)
        fitting = good_enough
    return fitting


def _quality(item: Item) -> float | None:
    return None if item.quality is None else float(item.quality)


async def _move(
    session: AsyncSession,
    source: Container,
    target: Container,
    type_key: str,
    quantity: int,
    *,
    tier: str | None,
    constants: Constants,
    floor: int = 0,
) -> int:
    """Move goods from container to container, splitting stacks as needed.

    `floor` is the buyer's quality floor (D-239): below it nothing is taken,
    and above it the worst still goes first -- the seller keeps the better.
    """
    left = quantity
    for item in await _stacks(session, source, type_key, tier, constants, floor=floor):
        if left <= 0:
            break
        take = min(left, item.amount)
        if take == item.amount:
            item.container_id = target.id
            await world.stack_up(session, item)
        else:
            #: The split-off part is the same thing: same mark, shelf life, dish
            #: kind and fineness. Losing them when splitting a stack would
            #: depersonalise the goods on the counter.

            item.amount -= take
            sold = Item(
                container_id=target.id,
                type_key=item.type_key,
                amount=take,
                quality=item.quality,
                condition=item.condition,
                condition_cap=item.condition_cap,
                maker_identity_id=item.maker_identity_id,
                made_at=item.made_at,
                made_node_id=item.made_node_id,
                spoils_at=item.spoils_at,
                flavor=item.flavor,
                roles_filled=item.roles_filled,
                fineness=item.fineness,
                recipe_key=item.recipe_key,
            )
            session.add(sold)
            await world.stack_up(session, sold)
        left -= take
    await session.flush()
    return quantity - left


def _carrier() -> tuple[str, ...]:

    return craft.carrier_names()


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
