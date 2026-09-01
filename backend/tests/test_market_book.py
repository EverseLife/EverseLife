# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The floors, the windows and the book (D-239, D-047).

A buy reaches above its own tier and a floor inside one leaves the worse
lot; demand shows in every window it can be met in; the book glues rows
into an honest step and the last price is a deal, not an order. What trades
at all lives in `test_market.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from market_kit import ORE, _city, _trader, _with_goods
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import jobs, ledger, market, world
from src.models.inventory import Item
from src.models.ledger import AccountKind
from src.models.market import Order, OrderSide, OrderState, Trade
from src.units import amount_float, money

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
