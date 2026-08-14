"""Бронь с задатком и сроком (D-047).

Покупка удалённо невозможна: иначе игрок скупает всё везде, товар зависает в
резерве, а стаканы становятся фикцией. Бронь — разумное исключение, и она
устроена так, чтобы мёртвых резервов не возникало:

* задаток вносится сразу и уходит в эскроу — резерв стоит денег;
* товар выходит из стакана, но остаётся у продавца: он никуда не едет;
* забрать можно только приехав — география цела;
* не забрал в срок — задаток продавцу, товар обратно в книгу.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants, current, current_catalog
from src.constants import registry as R
from src.engine import jobs, ledger, market, travel, world
from src.models.ledger import AccountKind, PostingReason
from src.models.market import Order, ReservationState
from src.units import PERCENT, amount_float, money

ORE = "Железная руда"


async def _рынок(session: AsyncSession, *, цена=3, сколько=20, качество=64):
    """Узел с терминалом, продавец с товаром и покупатель с деньгами."""
    метка = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.mkt.{метка}", "Рынок", area_m2=200)
    двор = await world.node_container(session, node)
    await world.grant_item(session, двор, market.TERMINAL, quality=70, origin="тест")

    продавец = await world.create_identity(session, f"Продавец-{метка}")
    тело_продавца = await world.print_body(session, продавец, node)
    карман = await world.body_container(session, тело_продавца)
    await world.grant_item(
        session, карман, ORE, amount=сколько, quality=качество, origin="тест"
    )
    constants, catalog = current(), current_catalog()
    await market.load(session, constants, тело_продавца, ORE, сколько)
    заявка = (
        await market.sell(
            session, constants, catalog, продавец, node,
            type_key=ORE,
            tier=market.tier_of(constants, качество),
            price=money(цена),
            quantity=сколько,
        )
    ).order

    покупатель = await world.create_identity(session, f"Купец-{метка}")
    тело_купца = await world.print_body(session, покупатель, node)
    счёт = await ledger.account_for(session, AccountKind.IDENTITY, покупатель.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=счёт.id,
        amount=money(500), memo={},
    )
    return node, заявка, продавец, покупатель, тело_купца


# --- бронь ------------------------------------------------------------------


async def test_бронь_берёт_задаток_и_убирает_товар_из_стакана(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Резерв стоит денег и виден в книге: обещанное чужим не показывают."""
    node, заявка, _, покупатель, _ = await _рынок(session, цена=3, сколько=20)
    было = await ledger.balance(
        session, (await ledger.account_for(
            session, AccountKind.IDENTITY, покупатель.id)).id
    )

    бронь = await market.reserve(session, constants, покупатель, заявка, 10)

    сумма = money(3) * 10
    ожидаемый_задаток = int(сумма * constants[R.MARKET_RESERVATION_DEPOSIT] / PERCENT)
    assert бронь.deposit == ожидаемый_задаток
    assert amount_float(заявка.amount_left) == 10, "забронированное ушло из книги"

    стало = await ledger.balance(
        session, (await ledger.account_for(
            session, AccountKind.IDENTITY, покупатель.id)).id
    )
    assert было - стало == ожидаемый_задаток


async def test_свой_товар_не_бронируют(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, заявка, продавец, _, _ = await _рынок(session)
    with pytest.raises(market.NotYours):
        await market.reserve(session, constants, продавец, заявка, 1)


async def test_больше_чем_есть_не_забронируешь(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, заявка, _, покупатель, _ = await _рынок(session, сколько=5)
    with pytest.raises(market.NoGoods):
        await market.reserve(session, constants, покупатель, заявка, 50)


# --- выкуп ------------------------------------------------------------------


async def test_выкуп_доплачивает_остаток_и_отдаёт_товар(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Приехать обязательно: бронь не отменяет географию, она её планирует."""
    node, заявка, продавец, покупатель, тело = await _рынок(session, цена=3, сколько=20)
    бронь = await market.reserve(session, constants, покупатель, заявка, 10)

    счёт_купца = await ledger.account_for(
        session, AccountKind.IDENTITY, покупатель.id
    )
    счёт_продавца = await ledger.account_for(
        session, AccountKind.IDENTITY, продавец.id
    )
    было_у_купца = await ledger.balance(session, счёт_купца.id)
    было_у_продавца = await ledger.balance(session, счёт_продавца.id)

    сделка = await market.redeem(session, constants, catalog, тело, бронь)

    сумма = money(3) * 10
    assert бронь.state is ReservationState.REDEEMED
    #: Купец доплатил ровно остаток: задаток уже был в эскроу.
    assert было_у_купца - await ledger.balance(session, счёт_купца.id) == (
        сумма - бронь.deposit
    )
    #: Продавец получил всё, минус удержания города (в ничьём узле их нет).
    assert await ledger.balance(session, счёт_продавца.id) - было_у_продавца == (
        сумма - сделка.tax - сделка.fee
    )

    ячейка = await market.stall(session, node, покупатель.id)
    from sqlalchemy import select

    from src.models.inventory import Item

    товар = (
        await session.execute(
            select(Item).where(Item.container_id == ячейка.id, Item.type_key == ORE)
        )
    ).scalars().all()
    assert sum(amount_float(и.amount) for и in товар) == pytest.approx(10)


async def test_выкупить_можно_только_приехав(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Материя требует присутствия — бронь тут ничего не меняет (D-047)."""
    node, заявка, _, покупатель, тело = await _рынок(session)
    бронь = await market.reserve(session, constants, покупатель, заявка, 5)

    прочь = await world.create_node(
        session, f"terra.away.{uuid.uuid4().hex[:6]}", "Прочь", area_m2=50
    )
    await travel.connect(session, node, прочь, base_seconds=30)
    await travel.depart(session, constants, тело, прочь)

    with pytest.raises(travel.InTransit):
        await market.redeem(session, constants, catalog, тело, бронь)


async def test_чужую_бронь_не_выкупают(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node, заявка, _, покупатель, _ = await _рынок(session)
    бронь = await market.reserve(session, constants, покупатель, заявка, 5)

    чужой = await world.create_identity(session, f"Чужой-{uuid.uuid4().hex[:6]}")
    чужое_тело = await world.print_body(session, чужой, node)
    with pytest.raises(market.NotYours):
        await market.redeem(session, constants, catalog, чужое_тело, бронь)


# --- срок -------------------------------------------------------------------


async def test_срок_брони_из_вольта(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`market.reservation_period` суток, и сутки планетарные (D-008)."""
    _, заявка, _, покупатель, _ = await _рынок(session)
    момент = datetime.now(UTC)
    бронь = await market.reserve(
        session, constants, покупатель, заявка, 5, now=момент
    )
    срок = timedelta(
        hours=constants[R.MARKET_RESERVATION_PERIOD] * constants[R.TIME_DAY_TERRA]
    )
    assert бронь.expires_at == момент + срок


async def test_просроченная_бронь_отдаёт_задаток_продавцу_и_товар_в_стакан(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Не забрал — платишь за то, что товар ждал. Резерв не бывает мёртвым."""
    async with factory() as session, session.begin():
        _, заявка, продавец, покупатель, _ = await _рынок(session, цена=3, сколько=20)
        бронь = await market.reserve(session, constants, покупатель, заявка, 10)
        срок, бронь_id, заявка_id = бронь.expires_at, бронь.id, заявка.id
        задаток = бронь.deposit
        продавец_id = продавец.id

    задание = await jobs.run_one(factory, now=срок)
    assert задание is not None and задание.kind == "market.reservation_expiry"

    async with factory() as session:
        бронь = await session.get(type(бронь), бронь_id)
        заявка = await session.get(Order, заявка_id)
        assert бронь.state is ReservationState.LAPSED
        assert amount_float(заявка.amount_left) == 20, "товар вернулся в книгу"

        счёт = await ledger.account_for(session, AccountKind.IDENTITY, продавец_id)
        #: Продавец получил задаток — плату за ожидание.
        assert await ledger.balance(session, счёт.id) >= задаток


async def test_после_срока_выкупить_нельзя(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, заявка, _, покупатель, тело = await _рынок(session)
    момент = datetime.now(UTC)
    бронь = await market.reserve(
        session, constants, покупатель, заявка, 5, now=момент
    )
    with pytest.raises(market.BadOrder):
        await market.redeem(
            session, constants, catalog, тело, бронь,
            now=бронь.expires_at + timedelta(minutes=1),
        )
