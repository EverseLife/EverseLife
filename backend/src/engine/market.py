"""Стакан заявок узла (D-003, D-047, D-127).

Книга заявок местная и только местная: единая цена схлопнула бы географию и
убила бы перевозчика вместе с арбитражем (столп П3). Движок товар не оценивает
— он сводит встречные заявки, и цена есть то, что кто-то согласился заплатить
(D-002).

## Что где происходит

| Действие | Где | Почему |
|---|---|---|
| Загрузить товар в терминал | присутственно | материя перемещается только физически |
| Выставить, снять ордер на продажу | удалённо | товар привезён, дальше он биржевой актив |
| Купить | присутственно | иначе игрок скупает всё везде, не вставая |
| Забрать купленное | присутственно | то же правило материи |
| Смотреть стаканы любых городов | удалённо | цены знают все (D-047) |

Покупка — это выставление лимитной заявки на покупку, и потому она требует
присутствия: удалённая покупка превратила бы стаканы в фикцию из зависших
резервов. Остаток неисполненной заявки висит в стакане, деньги под него
заморожены, товар при исполнении ложится в терминал — забирать ногами.

## Откуда взялась каждая формула

**Ступень, а не число.** Товар торгуется позициями вида «железная руда,
хорошая»: ступени берутся из `quality.tiers` (D-058). Непрерывная шкала сделала
бы книгу нечитаемой и убила бы ликвидность.

**Приоритет.** Лучшая цена, при равной цене — кто раньше встал. Сделка идёт по
цене **стоявшего в стакане**: он назвал условие первым, пришедший его принял.
Рыночных заявок нет вовсе — только лимитные, так проще и честнее
(30-economy/02, открытые вопросы).

**Деньги.** Покупатель замораживает `цена × объём` при постановке заявки.
Исполнилось дешевле — разница возвращается сразу: заморожено ровно столько,
сколько может понадобиться, и ни монетой больше.

**Налог и комиссия.** `tax_trade` платит **продавец** долей от выручки в момент
исполнения (D-127): покупатель видит в стакане цену, и это и есть цена.
Комиссия терминала — `market.default_fee`, пока город не задал свою. Обе идут
в казну города, которому принадлежит узел; **нет города — нет и удержаний**:
деньги не могут исчезать в никуда (И2).

**Срок.** Ордер живёт `market.order_lifetime` терранских суток и снимается
заданием журнала, а не проверкой при чтении: истечение обязано случиться даже
если в стакан никто не заглядывает.

**Бронь с задатком** — единственное исключение из «купить только стоя здесь»
(D-047). Купец резервирует партию издалека, вносит `market.reservation_deposit`
и обязан забрать за `market.reservation_period` суток; не забрал — задаток
остаётся продавцу, товар возвращается в стакан. Мёртвых резервов не возникает
потому, что у брони есть цена и срок.

**В руки берут не больше предела** (D-146): забрать купленное мешает не жадность
терминала, а масса. Всё сверх — только транспортом.

## Чего здесь пока нет

* **Потолок цены, норма отпуска, пошлины** (D-122, D-123) — код-законы города,
  приезжают с городами на Э3;
* **Осиротевший терминал** (D-100) — требует содержания построек, то есть
  зданий и казны.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events, ledger, travel
from src.engine.jobs import enqueue, handler
from src.engine.world import body_container, node_container
from src.models.event import EventKind
from src.models.identity import Body, BodyState, Identity
from src.models.inventory import Container, ContainerKind, Item
from src.models.job import Job, JobKind
from src.models.ledger import AccountKind, PostingReason
from src.models.market import (
    Order,
    OrderSide,
    OrderState,
    Reservation,
    ReservationState,
    Trade,
)
from src.models.world import Node
from src.units import AMOUNT_SCALE, PERCENT, amount, amount_float, money_str


class MarketError(Exception):
    pass


class NoTerminal(MarketError):
    """В узле нет терминала. Маркетплейс в городе один (D-100)."""


class NotHere(MarketError):
    """Тело не в том узле. Материя требует присутствия (D-044)."""


class NotYours(MarketError):
    pass


class NoGoods(MarketError):
    """Товара в терминале нет либо он уже отдан под другой ордер."""


class BadOrder(MarketError):
    """Заявка бессмысленна: нулевой объём, нулевая цена, чужая ступень."""


class NoMoney(MarketError):
    """Нечем платить. Это ситуация в игре, а не ошибка сервера."""


#: Имя терминала в `build/recipes.json`. Один на город (D-100).
TERMINAL = "Терминал маркетплейса"


@dataclass(frozen=True, slots=True)
class Level:
    """Одна ступенька стакана: цена и весь объём по ней."""

    price: int
    amount: float


@dataclass(frozen=True, slots=True)
class Book:
    """Стакан по одной позиции: товар плюс ступень качества."""

    node: uuid.UUID
    type_key: str
    tier: str
    bids: tuple[Level, ...] = ()
    asks: tuple[Level, ...] = ()
    last: int | None = None

    @property
    def spread(self) -> int | None:
        if not self.bids or not self.asks:
            return None
        return self.asks[0].price - self.bids[0].price


@dataclass(frozen=True, slots=True)
class Fill:
    """Что произошло при постановке заявки."""

    order: Order
    trades: tuple[Trade, ...] = field(default_factory=tuple)

    @property
    def traded(self) -> float:
        return amount_float(sum(trade.amount for trade in self.trades))


# --- ступени ----------------------------------------------------------------


def tier_of(constants: Constants, quality: float | None) -> str:
    """Ступень качества товара. Пять ступеней — витрина стакана (D-058).

    Полоса тянется от своего начала до начала следующей: границы в данных
    целые (…39, 40…), а качество дробное, и 39.5 обязано попадать в нижнюю
    полосу, а не проваливаться между ними.
    """
    tiers = constants[R.QUALITY_TIERS]
    if quality is None:
        #: У энергии и денег качества нет вовсе — вся такая позиция одна.
        return tiers[0].name
    fitting = [tier for tier in sorted(tiers, key=lambda t: t.frm) if tier.frm <= quality]
    return fitting[-1].name if fitting else tiers[0].name


# --- терминал ---------------------------------------------------------------


async def terminal(session: AsyncSession, node: Node) -> Item:
    """Терминал узла. Нет терминала — нет и торговли, как нет её в чистом поле."""
    where = await node_container(session, node)
    found = (
        await session.execute(
            select(Item).where(Item.container_id == where.id, Item.type_key == TERMINAL).limit(1)
        )
    ).scalar_one_or_none()
    if found is None:
        raise NoTerminal(f"в узле {node.key} нет терминала маркетплейса")
    return found


async def stall(session: AsyncSession, node: Node, identity_id: uuid.UUID) -> Container:
    """Место личности в терминале узла: её загруженный товар и её покупки."""
    stmt = select(Container).where(
        Container.kind == ContainerKind.MARKET,
        Container.owner_id == identity_id,
        Container.node_id == node.id,
    )
    container = (await session.execute(stmt)).scalar_one_or_none()
    if container is None:
        container = Container(
            kind=ContainerKind.MARKET, owner_id=identity_id, node_id=node.id
        )
        session.add(container)
        await session.flush()
    return container


async def load(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    type_key: str,
    quantity: float,
) -> float:
    """Загрузить товар в терминал. Присутственное: везут ногами."""
    node = await _node_of(session, body)
    await terminal(session, node)
    inventory = await body_container(session, body)
    into = await stall(session, node, body.identity_id)

    moved = await _move(session, inventory, into, type_key, amount(quantity), tier=None,
                        constants=constants)
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
    """Забрать своё из терминала. Отданное под ордер не отдаётся дважды."""
    node = await _node_of(session, body)
    await terminal(session, node)
    stock = await stall(session, node, body.identity_id)
    inventory = await body_container(session, body)

    free = await _free(session, constants, node, body.identity_id, type_key, tier)
    want = min(amount(quantity), free)
    if want <= 0:
        raise NoGoods(f"свободного «{type_key}» в терминале нет: всё под ордерами")

    #: В руки берут не больше предела: за остальным приходят с повозкой (D-146).
    from src.constants import current_catalog
    from src.engine import gear

    await gear.check_carry(
        session, constants, current_catalog(), body, type_key, amount_float(want)
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


# --- ордера -----------------------------------------------------------------


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
    """Выставить заявку на продажу. Удалённое: товар уже привезён (D-047)."""
    await terminal(session, node)
    want = amount(quantity)
    _sane(price, want)

    free = await _free(session, constants, node, identity.id, type_key, tier)
    if free < want:
        raise NoGoods(
            f"в терминале свободно {amount_float(free)} «{type_key}» ступени «{tier}», "
            f"нужно {quantity}"
        )

    order = await _place(session, constants, identity, node, OrderSide.SELL, type_key, tier,
                         price, want, now=now)
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
    now: datetime | None = None,
) -> Fill:
    """Купить: лимитная заявка от присутствующего тела.

    Присутствие требуется именно здесь. Разреши покупку удалённо — и стаканы
    всех городов будут скуплены не вставая с места (D-047).
    """
    if body.state is not BodyState.ALIVE:
        raise NotHere("мёртвое тело не торгует")
    node = await _node_of(session, body)
    await terminal(session, node)

    want = amount(quantity)
    _sane(price, want)

    identity = await session.get(Identity, body.identity_id)
    if identity is None:  # pragma: no cover
        raise MarketError("тело без личности")

    order = await _place(session, constants, identity, node, OrderSide.BUY, type_key, tier,
                         price, want, now=now)
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
    """Забронировать партию из чужой заявки на продажу (D-047).

    Удалённое действие — и единственное исключение из правила «купить можно
    только стоя здесь». Купец, собираясь в дорогу, резервирует партию, вносит
    `market.reservation_deposit` от суммы и обязан забрать до срока
    `market.reservation_period`. Не забрал — задаток остаётся продавцу.

    Товар при этом **выходит из стакана**, но остаётся в ячейке продавца: он
    никуда не едет, пока за ним не приехали. Мёртвых резервов не возникает
    ровно потому, что у брони есть срок и цена.
    """
    moment = now or datetime.now(UTC)
    if order.side is not OrderSide.SELL:
        raise BadOrder("бронируют товар, а не заявку на покупку")
    if order.state is not OrderState.ACTIVE:
        raise BadOrder(f"заявка уже {order.state.value}")
    if order.identity_id == identity.id:
        raise NotYours("свой товар бронировать незачем: он и так ваш")

    want = amount(quantity)
    if want <= 0:
        raise BadOrder("бронь из нуля")
    if want > order.amount_left:
        raise NoGoods(
            f"в заявке свободно {amount_float(order.amount_left)}, "
            f"а брони просят {quantity}"
        )

    cost = _cost(order.price, want)
    задаток = int(cost * constants[R.MARKET_RESERVATION_DEPOSIT] / PERCENT)
    счёт = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    escrow = await ledger.account_for(session, AccountKind.ESCROW, identity.id)
    await ledger.transfer(
        session,
        PostingReason.ESCROW_HOLD,
        debit=счёт.id,
        credit=escrow.id,
        amount=задаток,
        memo={"бронь": order.type_key, "цена": money_str(order.price)},
    )

    #: Товар уходит из книги: чужой стакан не должен показывать то, что уже
    #: обещано другому.
    order.amount_left -= want
    срок = timedelta(
        hours=constants[R.MARKET_RESERVATION_PERIOD] * constants[R.TIME_DAY_TERRA]
    )
    бронь = Reservation(
        order_id=order.id,
        node_id=order.node_id,
        buyer_identity_id=identity.id,
        seller_identity_id=order.identity_id,
        type_key=order.type_key,
        tier=order.tier,
        price=order.price,
        amount=want,
        deposit=задаток,
        expires_at=moment + срок,
    )
    session.add(бронь)
    await session.flush()

    event = await events.record(
        session,
        EventKind.RESERVATION_HELD,
        actor_identity_id=identity.id,
        node_id=order.node_id,
        reservation_id=str(бронь.id),
        order_id=str(order.id),
        type_key=order.type_key,
        amount=amount_float(want),
        deposit=задаток,
        expires_at=бронь.expires_at.isoformat(),
    )
    await enqueue(
        session,
        JobKind.MARKET_RESERVATION_EXPIRY,
        бронь.expires_at,
        payload={"reservation": str(бронь.id)},
        dedup_key=f"market.reservation:{бронь.id}",
        cause_event_id=event.id,
    )
    return бронь


async def redeem(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    reservation: Reservation,
    *,
    now: datetime | None = None,
) -> Trade:
    """Выкупить бронь: доплатить остаток и забрать товар. Присутственно.

    Приехать обязательно — в этом весь смысл: бронь не отменяет географию, она
    позволяет её планировать.
    """
    moment = now or datetime.now(UTC)
    await travel.require_here(session, body)
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
    остаток = cost - reservation.deposit
    счёт = await ledger.account_for(
        session, AccountKind.IDENTITY, reservation.buyer_identity_id
    )
    escrow = await ledger.account_for(
        session, AccountKind.ESCROW, reservation.buyer_identity_id
    )
    if остаток > 0:
        await ledger.transfer(
            session,
            PostingReason.ESCROW_HOLD,
            debit=счёт.id,
            credit=escrow.id,
            amount=остаток,
            memo={"выкуп брони": reservation.type_key},
        )

    #: Товар едет из ячейки продавца в ячейку покупателя, оставаясь в
    #: терминале: забирать его всё равно ногами (D-047).
    продавец = await stall(session, node, reservation.seller_identity_id)
    покупатель = await stall(session, node, reservation.buyer_identity_id)
    moved = await _move(
        session, продавец, покупатель, reservation.type_key, reservation.amount,
        tier=reservation.tier, constants=constants,
    )
    if moved < reservation.amount:  # pragma: no cover — товар держится бронью
        raise NoGoods("товар исчез из терминала между бронью и выкупом")

    tax_rate, fee_rate = await _charges(session, constants, catalog, node)
    tax = int(cost * tax_rate / PERCENT)
    fee = int(cost * fee_rate / PERCENT)

    #: Бронь — сделка без встречной заявки: покупатель не выставлял ордер.
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

    продавец_счёт = await ledger.account_for(
        session, AccountKind.IDENTITY, reservation.seller_identity_id
    )
    postings = [
        ledger.Posting(escrow.id, -cost),
        ledger.Posting(продавец_счёт.id, cost - tax - fee),
    ]
    if tax or fee:
        #: Счёт казны заведён на узле-представителе города: там же, где пул
        #: энергии, и это один и тот же счёт (D-154).
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
    """Срок брони вышел: задаток продавцу, товар обратно в стакан (D-047)."""
    бронь = await session.get(Reservation, uuid.UUID(job.payload["reservation"]))
    if бронь is None:  # pragma: no cover
        raise MarketError(f"задание {job.id}: брони нет")
    if бронь.state is not ReservationState.HELD:
        return

    escrow = await ledger.account_for(
        session, AccountKind.ESCROW, бронь.buyer_identity_id
    )
    продавец = await ledger.account_for(
        session, AccountKind.IDENTITY, бронь.seller_identity_id
    )
    #: Задаток — плата за то, что товар ждал: он остаётся продавцу.
    await ledger.transfer(
        session,
        PostingReason.ESCROW_RELEASE,
        debit=escrow.id,
        credit=продавец.id,
        amount=бронь.deposit,
        memo={"просроченная бронь": бронь.type_key},
    )

    #: Товар возвращается в книгу, если заявка ещё жива. Снятая заявка держит
    #: его в ячейке продавца — он и так у себя.
    order = await session.get(Order, бронь.order_id)
    if order is not None and order.state is OrderState.ACTIVE:
        order.amount_left += бронь.amount

    бронь.state = ReservationState.LAPSED
    бронь.closed_at = job.run_at
    await session.flush()
    await events.record(
        session,
        EventKind.RESERVATION_LAPSED,
        actor_identity_id=бронь.buyer_identity_id,
        node_id=бронь.node_id,
        reservation_id=str(бронь.id),
        deposit=бронь.deposit,
    )


async def cancel(
    session: AsyncSession, order: Order, *, by: uuid.UUID, now: datetime | None = None
) -> Order:
    """Снять ордер. Удалённое действие: распоряжение присутствия не требует."""
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
    """Срок ордера вышел. Истечение — событие мира, а не следствие чтения."""
    order = await session.get(Order, uuid.UUID(job.payload["order"]))
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


# --- чтение -----------------------------------------------------------------


async def book(
    session: AsyncSession, node: Node, type_key: str, tier: str, *, depth: int
) -> Book:
    """Стакан по позиции. Публичен: цены знают все (D-047)."""
    bids = await _levels(session, node, type_key, tier, OrderSide.BUY, depth=depth)
    asks = await _levels(session, node, type_key, tier, OrderSide.SELL, depth=depth)
    last = await session.scalar(
        select(Trade.price)
        .where(Trade.node_id == node.id, Trade.type_key == type_key, Trade.tier == tier)
        .order_by(Trade.at.desc())
        .limit(1)
    )
    return Book(
        node=node.id, type_key=type_key, tier=tier, bids=bids, asks=asks, last=last
    )


async def positions(session: AsyncSession, node: Node) -> tuple[tuple[str, str], ...]:
    """Какие позиции вообще торгуются в узле: товар плюс ступень."""
    rows = await session.execute(
        select(Order.type_key, Order.tier)
        .where(Order.node_id == node.id, Order.state == OrderState.ACTIVE)
        .group_by(Order.type_key, Order.tier)
        .order_by(Order.type_key, Order.tier)
    )
    return tuple((row[0], row[1]) for row in rows)


# --- внутреннее -------------------------------------------------------------


def _sane(price: int, want: int) -> None:
    if price <= 0:
        raise BadOrder("цена должна быть положительной")
    if want <= 0:
        raise BadOrder("объём должен быть положительным")


def _cost(price: int, quantity: int) -> int:
    """Во что обойдётся объём по цене. Целое: копейка не теряется."""
    return price * quantity // AMOUNT_SCALE


async def _node_of(session: AsyncSession, body: Body) -> Node:
    """Узел, в котором тело **стоит**. В пути его нет нигде (D-107)."""
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
    now: datetime | None,
) -> Order:
    moment = now or datetime.now(UTC)
    lifetime = timedelta(
        hours=constants[R.MARKET_ORDER_LIFETIME] * constants[R.TIME_DAY_TERRA]
    )
    order = Order(
        node_id=node.id,
        identity_id=identity.id,
        side=side,
        type_key=type_key,
        tier=tier,
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
    """Свести заявку со встречными: лучшая цена, при равной — кто раньше встал."""
    moment = now or datetime.now(UTC)
    trades: list[Trade] = []

    for counter in await _counterparts(session, order):
        if order.amount_left <= 0:
            break
        trades.append(
            await _execute(session, constants, catalog, order, counter, moment)
        )

    if order.amount_left <= 0:
        await _close(session, order, OrderState.FILLED, moment)
    return Fill(order=order, trades=tuple(trades))


async def _counterparts(session: AsyncSession, order: Order) -> Sequence[Order]:
    """Встречные заявки, годные по цене, в порядке исполнения."""
    other = OrderSide.SELL if order.side is OrderSide.BUY else OrderSide.BUY
    stmt = select(Order).where(
        Order.node_id == order.node_id,
        Order.type_key == order.type_key,
        Order.tier == order.tier,
        Order.side == other,
        Order.state == OrderState.ACTIVE,
        Order.id != order.id,
        #: Со своей же заявкой сделка бессмысленна: деньги и товар вернулись бы
        #: к тому же владельцу, а оборот города вырос бы на пустом месте.
        Order.identity_id != order.identity_id,
    )
    if order.side is OrderSide.BUY:
        stmt = stmt.where(Order.price <= order.price).order_by(Order.price, Order.created_at)
    else:
        stmt = stmt.where(Order.price >= order.price).order_by(
            Order.price.desc(), Order.created_at
        )
    return (await session.execute(stmt)).scalars().all()


async def _execute(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    taker: Order,
    maker: Order,
    moment: datetime,
) -> Trade:
    """Одна сделка: товар покупателю, деньги продавцу, налог городу."""
    quantity = min(taker.amount_left, maker.amount_left)
    #: Цена стоявшего в стакане: он назвал условие первым.
    price = maker.price
    cost = _cost(price, quantity)

    buy_order = taker if taker.side is OrderSide.BUY else maker
    sell_order = maker if taker.side is OrderSide.BUY else taker

    node = await session.get(Node, taker.node_id)
    if node is None:  # pragma: no cover
        raise MarketError("ордер вне узла")

    #: Товар едет из ячейки продавца в ячейку покупателя, оставаясь в терминале:
    #: забирать его всё равно ногами (D-047).
    seller_stall = await stall(session, node, sell_order.identity_id)
    buyer_stall = await stall(session, node, buy_order.identity_id)
    moved = await _move(
        session, seller_stall, buyer_stall, taker.type_key, quantity,
        tier=taker.tier, constants=constants,
    )
    if moved < quantity:  # pragma: no cover — товар держится ордером
        raise NoGoods("товар исчез из терминала между проверкой и сделкой")

    tax_rate, fee_rate = await _charges(session, constants, catalog, node)
    tax = int(cost * tax_rate / PERCENT)
    fee = int(cost * fee_rate / PERCENT)

    trade = Trade(
        node_id=node.id,
        buy_order_id=buy_order.id,
        sell_order_id=sell_order.id,
        type_key=taker.type_key,
        tier=taker.tier,
        price=price,
        amount=quantity,
        tax=tax,
        fee=fee,
    )
    session.add(trade)

    taker.amount_left -= quantity
    maker.amount_left -= quantity
    if maker.amount_left <= 0:
        await _close(session, maker, OrderState.FILLED, moment)
    await session.flush()

    event = await events.record(
        session,
        EventKind.TRADE_EXECUTED,
        actor_identity_id=buy_order.identity_id,
        node_id=node.id,
        trade_id=str(trade.id),
        type_key=taker.type_key,
        tier=taker.tier,
        price=price,
        amount=amount_float(quantity),
        seller=str(sell_order.identity_id),
        tax=tax,
        fee=fee,
    )
    await _settle(session, buy_order, sell_order, node, cost, tax, fee, event_id=event.id)
    #: Купили дешевле, чем были готовы платить, — разница освобождается сразу,
    #: а не ждёт закрытия ордера. Заморожено ровно то, что может понадобиться.
    await _release(session, buy_order, buy_order.escrowed - _cost(buy_order.price,
                                                                 buy_order.amount_left))
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
    """Расчёт по сделке: из эскроу покупателя продавцу, налог и комиссия — городу.

    Продавец получает ровно то, что заплатил покупатель, минус налог и комиссия
    (И2). Ни одна монета не появляется и не исчезает.
    """
    escrow = await ledger.account_for(session, AccountKind.ESCROW, buy_order.identity_id)
    seller = await ledger.account_for(session, AccountKind.IDENTITY, sell_order.identity_id)

    postings = [ledger.Posting(escrow.id, -cost), ledger.Posting(seller.id, cost - tax - fee)]
    if tax or fee:
        #: Счёт казны заведён на узле-представителе города: там же, где пул
        #: энергии, и это один и тот же счёт (D-154).
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
    """Заморозить деньги покупателя под заявку."""
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
    """Вернуть покупателю замороженное — всё либо названную часть."""
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


async def _close(
    session: AsyncSession, order: Order, state: OrderState, moment: datetime
) -> None:
    order.state = state
    order.closed_at = moment
    if order.side is OrderSide.BUY:
        await _release(session, order)
    await session.flush()


async def _treasury(session: AsyncSession, node: Node):
    """Счёт казны города, которому принадлежит узел.

    Владелец узла хранится идентификатором города, а счёт казны заведён на его
    узле-представителе — там, где живёт и пул энергии. Одно место на все
    городские деньги: иначе налоги и тариф попадали бы в разные карманы.
    """
    from src.engine import city as town

    город = await town.by_id(session, node.owner_city_id)
    if город is None:  # pragma: no cover — владелец без города это баг
        raise MarketError(f"узел {node.key} принадлежит несуществующему городу")
    return await town.treasury(session, город)


async def _charges(
    session: AsyncSession, constants: Constants, catalog: Catalog, node: Node
) -> tuple[float, float]:
    """Ставка налога с продажи и комиссии терминала для узла.

    Ставку назначает **город** (D-127, D-154): движок берёт действующее
    значение его код-закона `tax_trade`. Город, ничего не решивший, живёт на
    умолчании `laws.json` — новый город работает, ничего не заполняя (D-130).
    Комиссия — `market.default_fee`, пока владелец терминала не задал свою.

    **Узел ничей — удержаний нет вовсе.** Не потому что так задумано, а потому
    что платить их некому: деньги не могут исчезать в никуда (И2).
    """
    from src.engine import city as town

    if node.owner_city_id is None:
        return 0.0, 0.0
    город = await town.by_id(session, node.owner_city_id)
    return (
        town.law_number(constants, catalog, город, "tax_trade"),
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
    """Сколько товара в терминале не отдано под ордера."""
    stock = await stall(session, node, identity_id)
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
) -> list[Item]:
    """Стопки нужного товара, худшие первыми: хорошее приберегают."""
    rows = (
        (
            await session.execute(
                select(Item)
                .where(Item.container_id == container.id, Item.type_key == type_key)
                .order_by(Item.quality.asc().nulls_first(), Item.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    if tier is None:
        return list(rows)
    return [
        item for item in rows if tier_of(constants, _quality(item)) == tier
    ]


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
) -> int:
    """Переложить товар из контейнера в контейнер, разделяя стопки по надобности."""
    left = quantity
    for item in await _stacks(session, source, type_key, tier, constants):
        if left <= 0:
            break
        take = min(left, item.amount)
        if take == item.amount:
            item.container_id = target.id
        else:
            #: Отделённая часть — та же вещь: у неё те же клеймо, срок, вид
            #: блюда и проба. Потерять их при делении стопки значило бы
            #: обезличить товар на прилавке.
            item.amount -= take
            session.add(
                Item(
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
                )
            )
        left -= take
    await session.flush()
    return quantity - left


async def _levels(
    session: AsyncSession,
    node: Node,
    type_key: str,
    tier: str,
    side: OrderSide,
    *,
    depth: int,
) -> tuple[Level, ...]:
    stmt = (
        select(Order.price, func.sum(Order.amount_left))
        .where(
            Order.node_id == node.id,
            Order.type_key == type_key,
            Order.tier == tier,
            Order.side == side,
            Order.state == OrderState.ACTIVE,
        )
        .group_by(Order.price)
        .limit(depth)
    )
    stmt = stmt.order_by(Order.price.desc() if side is OrderSide.BUY else Order.price)
    rows = await session.execute(stmt)
    return tuple(Level(price=row[0], amount=amount_float(int(row[1]))) for row in rows)
