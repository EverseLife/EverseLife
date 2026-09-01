# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Positions and deals: what trades, and how money and goods move (D-003,
D-047, D-127).

Goods trade in tiers under the world's names -- no relic, no unknown word,
no foreign tier; the terminal is the door, a deal goes at the resting
order's price, money moves and never appears, the seller pays the tax and
the remainder rests in the book. The floors and the shop window live in
`test_market_book.py`, the liquids in `test_market_tank.py`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from market_kit import ORE, _city, _trader, _with_goods
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import craft, ledger, market, world
from src.models.inventory import Item
from src.models.ledger import AccountKind, PostingReason
from src.models.market import Order, OrderState
from src.units import amount_float, money


async def _authority(session: AsyncSession, catalog: Catalog):
    """An institutional city: it sets the tax rate, not the vault (D-154)."""
    from src.engine import city as town
    from src.models.world import Layer

    stamp = uuid.uuid4().hex[:8]
    delegate = await world.create_node(
        session, f"terra.city.{stamp}", "Город", area_m2=1, layer=Layer.PLANET
    )
    return await town.found(session, catalog, delegate, "Город")


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
