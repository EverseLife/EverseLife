# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The order book (D-003, D-047, D-127).

Checked is what the market was written this way for:

* the engine does not value goods -- people name the price (D-002);
* matter requires presence, disposing does not: loading and buying on foot,
  orders from anywhere;
* money moves, it does not appear: a deal's postings sum to zero (I2);
* the seller pays the sales tax, the buyer pays exactly the book price (D-127);
* an order lives by term and is cancelled by a job, not by a check on read.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import jobs, ledger, market, world
from src.models.inventory import Item
from src.models.ledger import AccountKind, PostingReason
from src.models.market import Order, OrderSide, OrderState, Trade
from src.units import amount_float, money

ORE = "Железная руда"
TERMINAL = "Терминал маркетплейса"


async def _city(session: AsyncSession, *, city=None):
    """A node with a terminal. One marketplace per city (D-100)."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.market.{stamp}", "Торг", area_m2=100)
    node.owner_city_id = None if city is None else city.id
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, TERMINAL, quality=70, origin="сценарий теста")
    return node


async def _authority(session: AsyncSession, catalog: Catalog):
    """An institutional city: it sets the tax rate, not the vault (D-154)."""
    from src.engine import city as town
    from src.models.world import Layer

    stamp = uuid.uuid4().hex[:8]
    delegate = await world.create_node(
        session, f"terra.city.{stamp}", "Город", area_m2=1, layer=Layer.PLANET
    )
    return await town.found(session, catalog, delegate, "Город")


async def _trader(session: AsyncSession, node, name: str, *, funds: float = 0):
    identity = await world.create_identity(session, f"{name}-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    if funds:
        account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=account.id,
            amount=money(funds),
        )
    return identity, body


async def _with_goods(
    session: AsyncSession,
    constants: Constants,
    node,
    name: str,
    *,
    qty: float = 10,
    quality: float = 65,
):
    identity, body = await _trader(session, node, name)
    pocket = await world.body_container(session, body)
    await world.grant_item(
        session, pocket, ORE, amount=qty, quality=quality, origin="сценарий теста"
    )
    await market.load(session, constants, body, ORE, qty)
    return identity, body


async def _balance(session: AsyncSession, identity_id: uuid.UUID) -> int:
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity_id)
    return await ledger.balance(session, account.id)


# --- tiers -------------------------------------------------------------------


def test_goods_traded_in_tiers(constants: Constants) -> None:
    """A continuous scale would make the book unreadable and kill liquidity (D-058)."""
    tiers = {market.tier_of(constants, q) for q in (0, 25, 50, 75, 100)}
    assert len(tiers) == len(constants[R.QUALITY_TIERS])
    assert market.tier_of(constants, 63) == market.tier_of(constants, 64), (
        "соседние числа обязаны попадать в одну позицию стакана"
    )
    #: Band bounds in the data are integers, quality is fractional: 39.5 does
    #: not fall between "..39" and "40.." but into the lower band.
    assert market.tier_of(constants, 39.5) == market.tier_of(constants, 39)


def test_qualityless_goods_have_one_position(constants: Constants) -> None:
    """Energy and money have no quality at all -- not zero, but none."""
    assert market.tier_of(constants, None) == constants[R.QUALITY_TIERS][0].name


# --- presence ----------------------------------------------------------------


async def test_no_trade_without_terminal(session: AsyncSession, constants: Constants) -> None:
    """A marketplace is a building, not a right. No building -- no market."""
    node = await world.create_node(
        session, f"terra.field.{uuid.uuid4().hex[:6]}", "Поле", area_m2=100
    )
    _, body = await _trader(session, node, "Селянин")
    with pytest.raises(market.NoTerminal):
        await market.load(session, constants, body, ORE, 1)


async def test_loaded_lies_in_terminal_not_pocket(
    session: AsyncSession, constants: Constants
) -> None:
    """The seller delivers goods once and manages them remotely from then on (D-047)."""
    node = await _city(session)
    identity, body = await _with_goods(session, constants, node, "Возчик", qty=10)
    await session.commit()

    pocket = await world.body_container(session, body)
    in_pocket = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == pocket.id, Item.type_key == ORE
        )
    )
    cell = await market.stall(session, node, identity.id)
    in_terminal = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == cell.id, Item.type_key == ORE
        )
    )
    assert in_pocket == 0
    assert amount_float(int(in_terminal)) == pytest.approx(10)


async def test_committed_to_order_cannot_be_taken_back(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Otherwise the same sack is sold twice."""
    node = await _city(session)
    identity, body = await _with_goods(session, constants, node, "Хитрец", qty=10, quality=65)
    tier = market.tier_of(constants, 65)
    await market.sell(
        session,
        constants,
        catalog,
        identity,
        node,
        type_key=ORE,
        tier=tier,
        price=money(5),
        quantity=8,
    )

    took = await market.take(session, constants, body, ORE, 10)
    assert took == pytest.approx(2), "свободны только те две, что не под ордером"


# --- order matching ----------------------------------------------------------


async def test_deal_at_price_of_resting_order(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """They named the terms first, the newcomer accepted."""
    node = await _city(session)
    tier = market.tier_of(constants, 65)
    seller, _ = await _with_goods(session, constants, node, "Продавец", qty=10)
    buyer, body = await _trader(session, node, "Покупатель", funds=100)

    await market.sell(
        session,
        constants,
        catalog,
        seller,
        node,
        type_key=ORE,
        tier=tier,
        price=money(4),
        quantity=10,
    )
    #: The buyer is ready to give more -- and pays less, because it was not them resting.
    deal = await market.buy(
        session, constants, catalog, body, type_key=ORE, tier=tier, price=money(6), quantity=4
    )
    await session.commit()

    assert deal.traded == pytest.approx(4)
    assert deal.trades[0].price == money(4)


async def test_money_moves_not_appears(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Invariant I2 is held by the whole money construction."""
    node = await _city(session)
    tier = market.tier_of(constants, 65)
    seller, _ = await _with_goods(session, constants, node, "Кузнец", qty=10)
    buyer, body = await _trader(session, node, "Купец", funds=100)
    mass_before = await ledger.money_supply(session)

    await market.sell(
        session,
        constants,
        catalog,
        seller,
        node,
        type_key=ORE,
        tier=tier,
        price=money(5),
        quantity=10,
    )
    await market.buy(
        session, constants, catalog, body, type_key=ORE, tier=tier, price=money(5), quantity=10
    )
    await session.commit()

    assert await _balance(session, seller.id) == money(50)
    assert await _balance(session, buyer.id) == money(50)
    assert await ledger.money_supply(session) == mass_before, "денежная масса не выросла"


async def test_bought_waits_in_terminal_and_taken_on_foot(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Matter moves only physically (D-047)."""
    node = await _city(session)
    tier = market.tier_of(constants, 65)
    seller, _ = await _with_goods(session, constants, node, "Шахтёр", qty=10)
    buyer, body = await _trader(session, node, "Скупщик", funds=100)

    await market.sell(
        session,
        constants,
        catalog,
        seller,
        node,
        type_key=ORE,
        tier=tier,
        price=money(5),
        quantity=10,
    )
    await market.buy(
        session, constants, catalog, body, type_key=ORE, tier=tier, price=money(5), quantity=6
    )

    cell = await market.stall(session, node, buyer.id)
    lies = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == cell.id, Item.type_key == ORE
        )
    )
    assert amount_float(int(lies)) == pytest.approx(6)

    took = await market.take(session, constants, body, ORE, 6)
    await session.commit()
    assert took == pytest.approx(6)


async def test_frozen_surplus_returned_immediately(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Exactly what may be needed is frozen, and not a coin more."""
    node = await _city(session)
    tier = market.tier_of(constants, 65)
    seller, _ = await _with_goods(session, constants, node, "Рудокоп", qty=10)
    buyer, body = await _trader(session, node, "Богач", funds=100)

    await market.sell(
        session,
        constants,
        catalog,
        seller,
        node,
        type_key=ORE,
        tier=tier,
        price=money(4),
        quantity=10,
    )
    await market.buy(
        session, constants, catalog, body, type_key=ORE, tier=tier, price=money(6), quantity=10
    )
    await session.commit()

    #: Paid four apiece though ready to pay six: twenty came back at once.
    assert await _balance(session, buyer.id) == money(60)
    escrow = await ledger.account_for(session, AccountKind.ESCROW, buyer.id)
    assert await ledger.balance(session, escrow.id) == 0


async def test_seller_pays_tax(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The buyer sees the price in the book -- and that is the price (D-127)."""
    city = await _authority(session, catalog)
    node = await _city(session, city=city)
    tier = market.tier_of(constants, 65)
    seller, _ = await _with_goods(session, constants, node, "Обложенный", qty=10)
    buyer, body = await _trader(session, node, "Приезжий", funds=100)

    rate = float(catalog.laws.code_law_defaults()["tax_trade"])
    commission = constants[R.MARKET_DEFAULT_FEE]
    assert rate > 0

    await market.sell(
        session,
        constants,
        catalog,
        seller,
        node,
        type_key=ORE,
        tier=tier,
        price=money(5),
        quantity=10,
    )
    await market.buy(
        session, constants, catalog, body, type_key=ORE, tier=tier, price=money(5), quantity=10
    )
    await session.commit()

    price = money(50)
    withheld = int(price * rate / 100) + int(price * commission / 100)
    assert await _balance(session, buyer.id) == money(100) - price, (
        "покупатель платит ровно цену стакана"
    )
    assert await _balance(session, seller.id) == price - withheld

    #: The treasury is one per city and lives on its delegate node: the energy
    #: tariff proceeds go there too (D-154).
    from src.engine import city as town

    treasury = await town.treasury(session, city)
    assert await ledger.balance(session, treasury.id) == withheld


async def test_ownerless_node_withholds_nothing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Nobody to pay -- so nothing to withhold: money does not vanish (I2)."""
    node = await _city(session)
    tier = market.tier_of(constants, 65)
    seller, _ = await _with_goods(session, constants, node, "Вольный", qty=5)
    _, body = await _trader(session, node, "Вольный покупатель", funds=100)

    await market.sell(
        session,
        constants,
        catalog,
        seller,
        node,
        type_key=ORE,
        tier=tier,
        price=money(5),
        quantity=5,
    )
    await market.buy(
        session, constants, catalog, body, type_key=ORE, tier=tier, price=money(5), quantity=5
    )
    await session.commit()

    assert await _balance(session, seller.id) == money(25)


async def test_no_deal_with_own_order(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Otherwise city turnover is inflated out of nothing."""
    node = await _city(session)
    tier = market.tier_of(constants, 65)
    self_, body = await _with_goods(session, constants, node, "Сам себе", qty=5)
    account = await ledger.account_for(session, AccountKind.IDENTITY, self_.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=account.id, amount=money(100)
    )

    await market.sell(
        session,
        constants,
        catalog,
        self_,
        node,
        type_key=ORE,
        tier=tier,
        price=money(5),
        quantity=5,
    )
    deal = await market.buy(
        session, constants, catalog, body, type_key=ORE, tier=tier, price=money(5), quantity=5
    )
    assert deal.traded == 0


async def test_cannot_buy_without_money(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """This is an in-game situation, not a server error."""
    node = await _city(session)
    tier = market.tier_of(constants, 65)
    seller, _ = await _with_goods(session, constants, node, "Торговец", qty=5)
    _, body = await _trader(session, node, "Нищий")

    await market.sell(
        session,
        constants,
        catalog,
        seller,
        node,
        type_key=ORE,
        tier=tier,
        price=money(5),
        quantity=5,
    )
    with pytest.raises(market.NoMoney):
        await market.buy(
            session, constants, catalog, body, type_key=ORE, tier=tier, price=money(5), quantity=5
        )


async def test_order_remainder_rests_in_book(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The book gives asynchrony: sold while asleep."""
    node = await _city(session)
    tier = market.tier_of(constants, 65)
    seller, _ = await _with_goods(session, constants, node, "Оптовик", qty=4)
    _, body = await _trader(session, node, "Ждущий", funds=100)

    await market.sell(
        session,
        constants,
        catalog,
        seller,
        node,
        type_key=ORE,
        tier=tier,
        price=money(5),
        quantity=4,
    )
    deal = await market.buy(
        session, constants, catalog, body, type_key=ORE, tier=tier, price=money(5), quantity=10
    )
    await session.commit()

    assert deal.traded == pytest.approx(4)
    assert deal.order.state is OrderState.ACTIVE
    assert amount_float(deal.order.amount_left) == pytest.approx(6)

    book_ = await market.book(session, node, ORE, tier, depth=10)
    assert book_.bids and book_.bids[0].amount == pytest.approx(6)
    assert not book_.asks
    assert book_.last == money(5)


async def test_cancelled_order_returns_frozen(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node = await _city(session)
    tier = market.tier_of(constants, 65)
    buyer, body = await _trader(session, node, "Передумавший", funds=100)

    deal = await market.buy(
        session, constants, catalog, body, type_key=ORE, tier=tier, price=money(5), quantity=10
    )
    assert await _balance(session, buyer.id) == money(50)

    await market.cancel(session, deal.order, by=buyer.id)
    await session.commit()
    assert await _balance(session, buyer.id) == money(100)


async def test_foreign_order_cannot_be_cancelled(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node = await _city(session)
    tier = market.tier_of(constants, 65)
    _, body = await _trader(session, node, "Свой", funds=100)
    foreign, _ = await _trader(session, node, "Чужой")

    deal = await market.buy(
        session, constants, catalog, body, type_key=ORE, tier=tier, price=money(5), quantity=1
    )
    with pytest.raises(market.NotYours):
        await market.cancel(session, deal.order, by=foreign.id)


# --- term --------------------------------------------------------------------


async def test_order_expires_by_job(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Expiry is a world event, not a consequence of somebody looking in."""
    async with factory() as session, session.begin():
        node = await _city(session)
        tier = market.tier_of(constants, 65)
        buyer, body = await _trader(session, node, "Терпеливый", funds=100)
        deal = await market.buy(
            session, constants, catalog, body, type_key=ORE, tier=tier, price=money(5), quantity=10
        )
        term, order_id, identity_id = deal.order.expires_at, deal.order.id, buyer.id

    expected = timedelta(hours=constants[R.MARKET_ORDER_LIFETIME] * constants[R.TIME_DAY_TERRA])
    assert term - expected < datetime.now(UTC) + timedelta(minutes=1)

    #: Before the term the order lives.
    assert await jobs.run_one(factory, now=term - timedelta(hours=1)) is None
    job = await jobs.run_one(factory, now=term)
    assert job is not None and job.kind == "market.order_expiry"

    async with factory() as session:
        order = await session.get(Order, order_id)
        assert order is not None and order.state is OrderState.EXPIRED
        account = await ledger.account_for(session, AccountKind.IDENTITY, identity_id)
        assert await ledger.balance(session, account.id) == money(100), (
            "заморозка вернулась целиком"
        )


async def test_deal_stays_in_journal(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """An order can be cancelled, a deal cannot: city turnover is computed from it (D-100)."""
    node = await _city(session)
    tier = market.tier_of(constants, 65)
    seller, _ = await _with_goods(session, constants, node, "Летописец", qty=3)
    _, body = await _trader(session, node, "Свидетель", funds=100)

    await market.sell(
        session,
        constants,
        catalog,
        seller,
        node,
        type_key=ORE,
        tier=tier,
        price=money(7),
        quantity=3,
    )
    await market.buy(
        session, constants, catalog, body, type_key=ORE, tier=tier, price=money(7), quantity=3
    )
    await session.commit()

    deal_count = await session.scalar(
        select(func.count()).select_from(Trade).where(Trade.node_id == node.id)
    )
    assert deal_count == 1
    orders_ = (await session.execute(select(Order).where(Order.node_id == node.id))).scalars().all()
    assert {o.side for o in orders_} == {OrderSide.BUY, OrderSide.SELL}
    assert all(o.state is OrderState.FILLED for o in orders_)


async def test_own_orders_say_which_node_they_stand_in(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Otherwise nobody outside the server can tell what is still free to sell.

    The shelf is `look.stall`, the committed part is one's own sell orders --
    but only those in this node. An AI citizen (D-224) without the node spent
    ten minutes on «в терминале свободно 0» because it could not subtract.
    """
    import src.api.session  # noqa: F401, PLC0415 -- registers the commands
    from src.api.registry import COMMANDS

    here, elsewhere = await _city(session), await _city(session)
    seller, _ = await _with_goods(session, constants, here, "Продавец", qty=10, quality=65)
    tier = market.tier_of(constants, 65)
    await market.sell(
        session,
        constants,
        catalog,
        seller,
        here,
        type_key=ORE,
        tier=tier,
        price=money(5),
        quantity=8,
    )

    answer = await COMMANDS["orders"].run({"identity_id": seller.id}, session, {})
    rows = answer["orders"]["orders"]
    assert len(rows) == 1
    assert rows[0]["node_key"] == here.key and rows[0]["node"] == here.name
    assert rows[0]["node_key"] != elsewhere.key
    #: The reading changes nothing: `orders` is declared readonly.
    assert COMMANDS["orders"].readonly
