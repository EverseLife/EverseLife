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
from src.engine import craft, jobs, ledger, market, world
from src.models.inventory import Item
from src.models.ledger import AccountKind, PostingReason
from src.models.market import Order, OrderSide, OrderState, Trade
from src.units import amount_float, money

ORE = "iron_ore"
TERMINAL = "market_terminal"


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


# --- what may stand in the book ----------------------------------------------


def _relic(catalog: Catalog) -> str:
    """A thing of the Forerunners, asked of the catalog: names are content (D-232)."""
    return next(material.name for material in catalog.recipes.materials if material.relic)


async def test_unknown_goods_are_not_bought(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A name nobody has heard of cannot be delivered, and the money is frozen for it."""
    node = await _city(session)
    identity, body = await _trader(session, node, "Фантазёр", funds=100)
    before = await _balance(session, identity.id)

    with pytest.raises(market.Untradable):
        await market.buy(
            session,
            constants,
            catalog,
            body,
            type_key="Философский камень",
            tier=market.tier_of(constants, 65),
            price=money(5),
            quantity=1,
        )

    orders = await session.scalar(select(func.count(Order.id)))
    assert orders == 0, "ордера на несуществующий товар не заводится"
    assert await _balance(session, identity.id) == before, "и деньги не морозятся"


async def test_unknown_goods_are_not_sold(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The seller's entrance keeps the same rule: the book holds only real things."""
    node = await _city(session)
    identity, _ = await _trader(session, node, "Продавец пустоты")

    with pytest.raises(market.Untradable):
        await market.sell(
            session,
            constants,
            catalog,
            identity,
            node,
            type_key="Философский камень",
            tier=market.tier_of(constants, 65),
            price=money(5),
            quantity=1,
        )


async def test_relic_is_not_traded(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Nobody makes what the Forerunners left, and nobody carries it away (D-232)."""
    node = await _city(session)
    _, body = await _trader(session, node, "Мародёр", funds=100)

    with pytest.raises(market.Untradable):
        await market.buy(
            session,
            constants,
            catalog,
            body,
            type_key=_relic(catalog),
            tier=market.tier_of(constants, 65),
            price=money(5),
            quantity=1,
        )


async def test_liquid_is_a_position_since_the_tank(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """D-255 opened the counter to liquids: the terminal is a vessel of its
    own, and an order for a liquid is as ordinary as one for ore. The cycle
    itself is pinned by the tank tests at the end of this file."""
    node = await _city(session)
    _, body = await _trader(session, node, "Водовоз", funds=100)

    fill = await market.buy(
        session,
        constants,
        catalog,
        body,
        type_key=catalog.recipes.liquid[0],
        tier=market.tier_of(constants, 65),
        price=money(1),
        quantity=1,
    )
    assert fill.traded == 0, "ордер встал в стакан: жидкость — обычная позиция"


async def test_written_carrier_is_a_position_of_its_own(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A carrier is named together with what is written on it (D-209): it stays tradable."""
    node = await _city(session)
    _, body = await _trader(session, node, "Книжник", funds=100)
    #: Named by the Russian display word on purpose: the counter must
    #: canonicalize both halves to the world's ids (D-251).
    carrier = f"{craft.carrier_names(catalog)[0]}{market.CARRIER_SEP}Стекло"
    canonical_carrier = (
        f"{craft.carrier_names(catalog)[0]}{market.CARRIER_SEP}"
        f"{catalog.recipes.recipe('Стекло').type_key}"
    )

    fill = await market.buy(
        session,
        constants,
        catalog,
        body,
        type_key=carrier,
        tier=market.tier_of(constants, 65),
        price=money(5),
        quantity=1,
    )
    assert fill.order.type_key == canonical_carrier
    assert fill.order.state is OrderState.ACTIVE


async def test_carrier_of_unknown_recipe_is_not_traded(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A carrier is a good per recipe, so an unheard-of recipe is an unheard-of good."""
    node = await _city(session)
    _, body = await _trader(session, node, "Мистификатор", funds=100)

    with pytest.raises(market.Untradable):
        await market.buy(
            session,
            constants,
            catalog,
            body,
            type_key=f"{craft.carrier_names(catalog)[0]}{market.CARRIER_SEP}Философский камень",
            tier=market.tier_of(constants, 65),
            price=money(5),
            quantity=1,
        )


async def test_synonym_trades_under_the_world_name(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One position, one name: a synonym order must meet the goods, not rest beside them.

    "Железо" is the iron ingot -- but stacks lie under the ingot's own name and
    orders match by string, so an order left as it came would hang unfillable
    with the buyer's money frozen under it until its term ran out.
    """
    #: Asked of the book, not written by hand: a material, so that the lot is
    #: an ordinary one -- a station would drag the machine's own rules in.
    book = catalog.recipes
    stuff = {material.type_key for material in book.materials}
    synonym, canonical = next(
        (name, book.resolve(real))
        for name, real in book.synonyms.items()
        if book.resolve(real) in stuff
    )
    node = await _city(session)
    tier = market.tier_of(constants, 65)

    seller, seller_body = await _trader(session, node, "Кузнец")
    pocket = await world.body_container(session, seller_body)
    await world.grant_item(
        session, pocket, canonical, amount=4, quality=65, origin="сценарий теста"
    )
    await market.load(session, constants, seller_body, canonical, 4)
    await market.sell(
        session,
        constants,
        catalog,
        seller,
        node,
        type_key=canonical,
        tier=tier,
        price=money(5),
        quantity=4,
    )

    _, body = await _trader(session, node, "Покупатель", funds=100)
    fill = await market.buy(
        session, constants, catalog, body, type_key=synonym, tier=tier, price=money(5), quantity=4
    )

    assert fill.order.type_key == canonical, "в книге стоит имя мира, а не присланный синоним"
    assert fill.traded == pytest.approx(4), "и потому ордер встретился со встречным"


async def test_foreign_tier_is_not_traded(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Positions are the world's five tiers (D-058); a sixth one nobody could fill."""
    node = await _city(session)
    _, body = await _trader(session, node, "Ценитель", funds=100)
    tiers = {step.name for step in constants[R.QUALITY_TIERS]}
    assert "божественное" not in tiers

    with pytest.raises(market.BadOrder):
        await market.buy(
            session,
            constants,
            catalog,
            body,
            type_key=ORE,
            tier="божественное",
            price=money(5),
            quantity=1,
        )


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

    book_ = await market.book(session, constants, node, ORE, tier, depth=10)
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
    with pytest.raises(market.NotYours) as refused:
        await market.cancel(session, deal.order, by=foreign.id)
    assert refused.value.key == "market-order-not-yours"


async def test_a_sell_into_a_resting_buy_pays_the_seller(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The other way round: the buy waits in the book and a sell fills it whole.

    The buyer's money is frozen under the order, and the seller is paid out of
    exactly that freeze -- so the filled order may only be closed once the
    money has moved. Closed first, it hands the whole freeze back and the deal
    finds an empty escrow.
    """
    node = await _city(session)
    tier = market.tier_of(constants, 65)
    buyer, body = await _trader(session, node, "Терпеливый", funds=100)
    await market.buy(
        session, constants, catalog, body, type_key=ORE, tier=tier, price=money(5), quantity=5
    )

    seller, _ = await _with_goods(session, constants, node, "Пришлый", qty=5)
    fill = await market.sell(
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

    assert fill.traded == pytest.approx(5)
    assert await _balance(session, seller.id) == money(25), "продавец получил выручку"
    assert await _balance(session, buyer.id) == money(75), "у покупателя списалось ровно столько же"
    escrow = await ledger.account_for(session, AccountKind.ESCROW, buyer.id)
    assert await ledger.balance(session, escrow.id) == 0, "под исполненным ордером ничего не висит"


# --- the buyer's floor -------------------------------------------------------


async def _shelved(
    session: AsyncSession, constants: Constants, node, name: str, lots: dict[float, float]
):
    """A seller whose terminal cell holds the named qualities of ore."""
    identity, body = await _trader(session, node, name)
    pocket = await world.body_container(session, body)
    for quality, qty in lots.items():
        await world.grant_item(
            session, pocket, ORE, amount=qty, quality=quality, origin="сценарий теста"
        )
    await market.load(session, constants, body, ORE, sum(lots.values()))
    return identity, body


async def _stall_of(session: AsyncSession, node, identity_id) -> list[Item]:
    cell = await market.stall(session, node, identity_id)
    rows = await session.execute(select(Item).where(Item.container_id == cell.id))
    return list(rows.scalars().all())


async def test_a_buy_reaches_above_its_own_tier(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A tier button is a floor: "хорошее" means "no worse than 60" and takes "отличное" too."""
    node = await _city(session)
    seller, _ = await _shelved(session, constants, node, "Мастер", {85: 5})
    _, body = await _trader(session, node, "Разборчивый", funds=100)

    await market.sell(
        session,
        constants,
        catalog,
        seller,
        node,
        type_key=ORE,
        tier=market.tier_of(constants, 85),
        price=money(5),
        quantity=5,
    )
    fill = await market.buy(
        session,
        constants,
        catalog,
        body,
        type_key=ORE,
        tier=market.tier_of(constants, 65),
        price=money(5),
        quantity=5,
    )

    assert fill.traded == pytest.approx(5), "лот лучше запрошенного — это не повод не торговать"
    assert fill.trades[0].tier == market.tier_of(constants, 85), (
        "ступень сделки — продавцова: из рук в руки идёт лот, а не окно спроса"
    )


async def test_a_floor_inside_a_tier_leaves_the_worse_lot(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The order says how much is committed, the stacks say how good it is (D-239)."""
    node = await _city(session)
    tier = market.tier_of(constants, 65)
    seller, _ = await _shelved(session, constants, node, "Оптовик", {62: 6, 78: 4})
    _, body = await _trader(session, node, "Придира", funds=100)

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
    fill = await market.buy(
        session,
        constants,
        catalog,
        body,
        type_key=ORE,
        tier=tier,
        price=money(5),
        quantity=10,
        min_quality=75,
    )

    assert fill.traded == pytest.approx(4), "порог берёт только те стопки, что его проходят"
    assert amount_float(fill.order.amount_left) == pytest.approx(6), (
        "остальное ждёт продавца попроще, а не считается исполненным"
    )


async def test_the_worst_that_clears_the_floor_goes_first(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Above the floor the rule is the old one: the seller keeps the better."""
    node = await _city(session)
    tier = market.tier_of(constants, 65)
    seller, _ = await _shelved(session, constants, node, "Запасливый", {76: 1, 79: 1})
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
        quantity=2,
    )
    await market.buy(
        session,
        constants,
        catalog,
        body,
        type_key=ORE,
        tier=tier,
        price=money(5),
        quantity=1,
        min_quality=75,
    )

    got = await _stall_of(session, node, buyer.id)
    assert [float(item.quality) for item in got] == [76]


async def test_a_floor_written_before_the_rule_is_its_tier_start(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """An order queued before floors existed keeps the terms it was written on.

    Its tier button meant "no worse than the start of this tier", and that is
    exactly how the empty floor is read -- nothing under a live order changes.
    """
    node = await _city(session)
    _, body = await _trader(session, node, "Старожил", funds=100)
    resting = await market.buy(
        session,
        constants,
        catalog,
        body,
        type_key=ORE,
        tier=market.tier_of(constants, 65),
        price=money(5),
        quantity=5,
    )
    #: As the row lay in the database before the column existed.
    resting.order.min_quality = None
    await session.flush()

    seller, _ = await _shelved(session, constants, node, "Ювелир", {85: 5})
    fill = await market.sell(
        session,
        constants,
        catalog,
        seller,
        node,
        type_key=ORE,
        tier=market.tier_of(constants, 85),
        price=money(5),
        quantity=5,
    )
    assert fill.traded == pytest.approx(5)


async def test_demand_is_shown_in_every_window_it_can_be_met_in(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A seller of the fine lot must see the plain buyer -- or the window lies (D-239)."""
    node = await _city(session)
    _, body = await _trader(session, node, "Неприхотливый", funds=100)
    await market.buy(
        session,
        constants,
        catalog,
        body,
        type_key=ORE,
        tier=market.tier_of(constants, 65),
        price=money(5),
        quantity=5,
    )

    fine = await market.book(session, constants, node, ORE, market.tier_of(constants, 85), depth=10)
    assert [level.price for level in fine.bids] == [money(5)]

    poor = await market.book(session, constants, node, ORE, market.tier_of(constants, 25), depth=10)
    assert poor.bids == (), "ниже своего порога спрос не показывается: там его не исполнить"


async def test_a_floor_inside_a_band_shows_from_the_next_window_up(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A bid must not cross an ask it cannot take, or the spread lies for good.

    A floor of 75 stands inside "хорошее" (60..79): part of that window it
    would refuse. Drawn there, it would sit above the asks of sellers holding
    62-quality ore and never trade with them -- a spread stuck at zero with no
    deal behind it. It is shown from "отличное", where any lot suits it.
    """
    node = await _city(session)
    _, body = await _trader(session, node, "Придира", funds=100)
    await market.buy(
        session,
        constants,
        catalog,
        body,
        type_key=ORE,
        tier=market.tier_of(constants, 65),
        price=money(5),
        quantity=5,
        min_quality=75,
    )

    own = await market.book(session, constants, node, ORE, market.tier_of(constants, 65), depth=10)
    assert own.bids == (), "в своём окне лежат лоты, которые заявка не возьмёт"
    fine = await market.book(session, constants, node, ORE, market.tier_of(constants, 85), depth=10)
    assert [level.price for level in fine.bids] == [money(5)]


async def test_a_floor_and_a_tier_must_agree(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Named together, they name one window -- or the order is refused, not moved."""
    node = await _city(session)
    _, body = await _trader(session, node, "Путаник", funds=100)

    with pytest.raises(market.BadOrder):
        await market.buy(
            session,
            constants,
            catalog,
            body,
            type_key=ORE,
            tier=market.tier_of(constants, 85),
            price=money(5),
            quantity=1,
            min_quality=60,
        )


async def test_a_floor_is_a_quality_of_this_world(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Any number is not a quality: the scale is the vault's."""
    node = await _city(session)
    _, body = await _trader(session, node, "Мечтатель", funds=100)

    with pytest.raises(market.BadOrder):
        await market.buy(
            session,
            constants,
            catalog,
            body,
            type_key=ORE,
            tier=market.tier_of(constants, 65),
            price=money(5),
            quantity=1,
            min_quality=200,
        )


# --- the step the book is read at --------------------------------------------


async def test_book_glues_rows_into_a_step_that_fits(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Prices are written to the minor unit; a book of rows that fine is a wall.

    Read at the finest step, the depth cuts the book off and the rest is
    simply not shown. Read at the step the server picks, the whole book fits
    and not a unit of it is lost on the way.
    """
    node = await _city(session)
    tier = market.tier_of(constants, 65)
    _, body = await _trader(session, node, "Толпа", funds=1000)
    #: Twenty five bids one minor unit apart -- more rows than the depth, and
    #: exactly the case that made the panel unreadable.
    for shift in range(25):
        await market.buy(
            session,
            constants,
            catalog,
            body,
            type_key=ORE,
            tier=tier,
            price=money(5) + shift,
            quantity=1,
        )

    finest = await market.book(session, constants, node, ORE, tier, depth=20, step=1)
    assert finest.step == 1
    assert len(finest.bids) == 20, "по минорной единице стакан не помещается и обрезается"

    fitted = await market.book(session, constants, node, ORE, tier, depth=20)
    assert fitted.step > 1, "сервер сам берёт шаг покрупнее"
    assert len(fitted.bids) <= 20
    assert len(fitted.asks) <= 20, "шаг выбран так, чтобы поместились обе стороны"
    assert sum(level.amount for level in fitted.bids) == pytest.approx(25), (
        "склейка меняет строки, а не объём: он весь на месте"
    )


async def test_a_row_never_promises_better_than_the_orders_in_it(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A bid rounds down, an ask rounds up: name the row's price back and it still crosses."""
    node = await _city(session)
    tier = market.tier_of(constants, 65)
    odd = money(5) + 7

    seller, _ = await _with_goods(session, constants, node, "Продавец", qty=1)
    await market.sell(
        session, constants, catalog, seller, node, type_key=ORE, tier=tier, price=odd, quantity=1
    )
    _, body = await _trader(session, node, "Покупатель", funds=100)
    #: Below the ask, so that the two rest in the book instead of trading.
    await market.buy(
        session, constants, catalog, body, type_key=ORE, tier=tier, price=odd - 100, quantity=1
    )

    glued = await market.book(session, constants, node, ORE, tier, depth=20, step=10)
    assert glued.asks[0].price >= odd, "строка продавцов не дешевле, чем они просят"
    assert glued.bids[0].price <= odd - 100, "строка покупателей не дороже, чем они дают"
    assert glued.asks[0].price % 10 == 0 and glued.bids[0].price % 10 == 0


# --- what it went for --------------------------------------------------------


async def test_last_price_is_a_deal_not_an_order(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Beside a name stands what it went for -- and nothing at all until it went (D-002)."""
    node = await _city(session)
    tier = market.tier_of(constants, 65)
    seller, _ = await _with_goods(session, constants, node, "Продавец", qty=5)
    _, body = await _trader(session, node, "Покупатель", funds=100)

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
    assert await market.last_prices(session, node) == {}, (
        "выставленный ордер — это запрос, а не цена: сделки ещё не было"
    )

    await market.buy(
        session, constants, catalog, body, type_key=ORE, tier=tier, price=money(5), quantity=2
    )
    assert await market.last_prices(session, node) == {ORE: money(5)}


async def test_last_price_is_the_freshest_across_tiers(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One name gathers all five tiers: beside it stands the freshest deal, whatever its quality."""
    node = await _city(session)
    good = market.tier_of(constants, 65)
    poor = market.tier_of(constants, 25)
    seller, seller_body = await _with_goods(session, constants, node, "Рудовоз", qty=5, quality=65)
    pocket = await world.body_container(session, seller_body)
    await world.grant_item(session, pocket, ORE, amount=5, quality=25, origin="сценарий теста")
    await market.load(session, constants, seller_body, ORE, 5)
    _, body = await _trader(session, node, "Скупщик", funds=100)

    for tier, price in ((good, money(9)), (poor, money(2))):
        await market.sell(
            session,
            constants,
            catalog,
            seller,
            node,
            type_key=ORE,
            tier=tier,
            price=price,
            quantity=5,
        )
        await market.buy(
            session, constants, catalog, body, type_key=ORE, tier=tier, price=price, quantity=5
        )
        #: Two commands, two transactions -- `at` is the transaction's clock,
        #: and without the commit both deals would be stamped the same instant.
        await session.commit()

    assert await market.last_prices(session, node) == {ORE: money(2)}, (
        "последней была сделка по плохой руде — она и стоит рядом с именем"
    )


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


async def test_own_buy_order_carries_the_hand_named_floor(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The buyer's own terms must be recoverable from the answer (D-239, D-225).

    A floor typed by hand inside the band cannot be read off the tier button,
    so `orders` carries it. The band's own start is derivable from the tier
    and the `/public` tiers constant, and a sell has no floor at all -- in
    neither case does the key get into the row.
    """
    import src.api.session  # noqa: F401, PLC0415 -- registers the commands
    from src.api.registry import COMMANDS

    node = await _city(session)
    buyer, body = await _trader(session, node, "Разборчивый", funds=100)
    tier = market.tier_of(constants, 75)
    frm, to = market.tier_span(constants, tier)
    floor = frm + 1
    assert floor <= to, "the band must be wide enough for a hand-named floor"

    #: Prices stay under the ask below, so both buys stand instead of filling.
    await market.buy(
        session,
        constants,
        catalog,
        body,
        type_key=ORE,
        tier=tier,
        price=money(5),
        quantity=1,
        min_quality=floor,
    )
    await market.buy(
        session, constants, catalog, body, type_key=ORE, tier=tier, price=money(4), quantity=1
    )
    #: A hand that types exactly the band's start names the default: `buy`
    #: already stores the two identically, and the tier says everything.
    await market.buy(
        session,
        constants,
        catalog,
        body,
        type_key=ORE,
        tier=tier,
        price=money(3),
        quantity=1,
        min_quality=frm,
    )
    seller, _ = await _with_goods(session, constants, node, "Продавец", qty=2, quality=75)
    await market.sell(
        session,
        constants,
        catalog,
        seller,
        node,
        type_key=ORE,
        tier=tier,
        price=money(9),
        quantity=2,
    )

    answer = await COMMANDS["orders"].run({"identity_id": buyer.id}, session, {})
    floors = {row["price"]: row.get("min_quality") for row in answer["orders"]["orders"]}
    assert floors == {money(5): floor, money(4): None, money(3): None}

    sold = await COMMANDS["orders"].run({"identity_id": seller.id}, session, {})
    (sell_row,) = sold["orders"]["orders"]
    assert sell_row["side"] == "sell" and "min_quality" not in sell_row


# --- the terminal's tank (D-255) ---------------------------------------------


LUBRICANT = "lubricant"


async def _with_canister(
    session: AsyncSession, node, name: str, *, fill: float = 0, funds: float = 0
):
    """A trader carrying a canister, optionally with lubricant already in it."""
    from src.engine import storage

    identity, body = await _trader(session, node, name, funds=funds)
    pocket = await world.body_container(session, body)
    canister = await world.grant_item(session, pocket, "canister", quality=60, origin="тест")
    if fill > 0:
        inside = await storage.inside(session, canister)
        await world.grant_item(session, inside, LUBRICANT, amount=fill, quality=55, origin="тест")
    return identity, body, canister


async def test_a_liquid_trades_out_of_the_tank_and_into_a_vessel(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The whole cycle of D-255: the seller pours in, the deal moves cells,
    the buyer pours out -- and the liquid lies loose nowhere on the way."""
    from src.engine import storage

    node = await _city(session)
    seller_id, seller, seller_can = await _with_canister(session, node, "Нефтяник", fill=50)
    loaded = await market.load(session, constants, seller, LUBRICANT, 30)
    assert loaded == pytest.approx(30)
    inside = await storage.inside(session, seller_can)
    left = (
        await session.execute(
            select(Item).where(Item.container_id == inside.id, Item.type_key == LUBRICANT)
        )
    ).scalar_one()
    assert amount_float(left.amount) == pytest.approx(20), "из канистры ушло ровно налитое"

    await market.sell(
        session,
        constants,
        catalog,
        seller_id,
        node,
        type_key=LUBRICANT,
        tier=market.tier_of(constants, 55),
        price=money(2),
        quantity=30,
    )

    buyer_id, buyer, buyer_can = await _with_canister(session, node, "Покупатель", funds=1000)
    fill = await market.buy(
        session,
        constants,
        catalog,
        buyer,
        type_key=LUBRICANT,
        tier=market.tier_of(constants, 55),
        price=money(2),
        quantity=30,
    )
    assert fill.traded == pytest.approx(30)

    #: Poured by the vessel's room (D-255): the canister holds twenty
    #: kilograms of it, the remainder waits in the tank for the next trip.
    room_units = 20 / catalog.recipes.mass_of(LUBRICANT)
    taken = await market.take(session, constants, buyer, LUBRICANT, 30)
    assert taken == pytest.approx(room_units, abs=0.01)
    inside = await storage.inside(session, buyer_can)
    got = (
        await session.execute(
            select(Item).where(Item.container_id == inside.id, Item.type_key == LUBRICANT)
        )
    ).scalar_one()
    assert amount_float(got.amount) == pytest.approx(taken), "слито ровно по месту тары"

    #: Nowhere on the way did the liquid lie loose in a pocket (D-230).
    pockets = [
        await world.body_container(session, seller),
        await world.body_container(session, buyer),
    ]
    for pocket in pockets:
        loose = (
            (
                await session.execute(
                    select(Item).where(Item.container_id == pocket.id, Item.type_key == LUBRICANT)
                )
            )
            .scalars()
            .all()
        )
        assert loose == []


async def test_the_tank_is_exactly_as_big_as_its_vessel(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`market.tank_capacity` bounds the liquid market: a full tank refuses."""
    from src.constants import registry as R

    node = await _city(session)
    unit = catalog.recipes.mass_of(LUBRICANT)
    cap_units = constants[R.MARKET_TANK_CAPACITY] / unit
    _, seller, _ = await _with_canister(session, node, "Нефтяник", fill=cap_units + 100)

    #: The fixture overfills the canister on purpose (`grant_item` does not
    #: judge a vessel's store): the tank's own ceiling is what is under test,
    #: and load takes whatever the vessels actually hold.
    poured = await market.load(session, constants, seller, LUBRICANT, cap_units + 100)
    assert poured <= cap_units + 1e-6

    with pytest.raises(market.TankFull):
        await market.load(session, constants, seller, LUBRICANT, 1)


async def test_a_buyer_without_a_vessel_waits(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """No canister -- the purchase stays in the tank, said plainly (D-255)."""
    node = await _city(session)
    seller_id, seller, _ = await _with_canister(session, node, "Нефтяник", fill=50)
    await market.load(session, constants, seller, LUBRICANT, 30)
    await market.sell(
        session,
        constants,
        catalog,
        seller_id,
        node,
        type_key=LUBRICANT,
        tier=market.tier_of(constants, 55),
        price=money(2),
        quantity=30,
    )
    buyer_id, buyer = await _trader(session, node, "Безтарный", funds=1000)
    fill = await market.buy(
        session,
        constants,
        catalog,
        buyer,
        type_key=LUBRICANT,
        tier=market.tier_of(constants, 55),
        price=money(2),
        quantity=30,
    )
    assert fill.traded == pytest.approx(30)
    with pytest.raises(market.NoRoom):
        await market.take(session, constants, buyer, LUBRICANT, 30)
