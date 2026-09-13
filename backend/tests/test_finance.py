# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The statement is read by pages, and any row of it opens (D-190).

A statement is the history behind a balance, and a history has two ways of
being useless: cut off at fifty lines, or fifty lines that say «сделка» and
nothing more. The pages are turned by the row's own id, and a row opened
shows every side of its operation and what stood behind it -- the deal, or
the order the money was frozen under.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from city_kit import _capital
from market_kit import ORE, _city, _trader, _with_goods
from src.constants import Catalog, Constants
from src.engine import finance, ledger, market, travel, works, world
from src.models.ledger import AccountKind, PostingReason
from src.models.market import Order
from src.models.world import Surface
from src.runtime import STATEMENT_PAGE
from src.units import money, money_str


async def _person(session: AsyncSession, name: str, *, funds: float = 0):
    identity = await world.create_identity(session, f"{name}-{uuid.uuid4().hex[:6]}")
    if funds:
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        wallet = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        await ledger.transfer(
            session, PostingReason.GENESIS, debit=genesis.id, credit=wallet.id, amount=money(funds)
        )
    return identity


async def test_the_statement_turns_by_the_last_row_read(session: AsyncSession) -> None:
    """A page is what stands under the id the reader stopped at.

    Newest first, one page at a time, and the second page begins exactly
    where the first ended: no row twice, no row skipped, even though the
    journal only grows at the top while the reader is turning.
    """
    payer = await _person(session, "Плательщик", funds=1000)
    payee = await _person(session, "Получатель")
    for step in range(1, 26):
        await finance.transfer(session, payer, payee.name, money(1), memo=f"#{step}")

    first, more = await finance.statement(session, payer.id)
    assert len(first) == STATEMENT_PAGE and more, "первая страница полна, и за ней есть ещё"
    assert [row["memo"]["ground"] for row in first] == [f"#{n}" for n in range(25, 5, -1)]

    second, more = await finance.statement(session, payer.id, before=first[-1]["id"])
    #: The five transfers left, and the issue that funded them under all of it.
    assert [row["reason"] for row in second] == ["transfer"] * 5 + ["genesis"]
    assert [row["memo"]["ground"] for row in second[:5]] == ["#5", "#4", "#3", "#2", "#1"]
    assert not more, "журнал кончился: третьей страницы нет"

    ids = [row["id"] for row in first + second]
    assert ids == sorted(ids, reverse=True) and len(set(ids)) == len(ids)

    #: Under the very last row there is nothing, and the answer says so
    #: plainly rather than starting over from the top.
    empty, more = await finance.statement(session, payer.id, before=second[-1]["id"])
    assert empty == [] and not more


async def test_a_transfer_opens_into_its_two_sides(session: AsyncSession) -> None:
    """Who paid and who was paid. The reader's own leg carries the reader's
    name like any other: the client knows that name from the session, and
    the wire does not say "mine" twice (D-225)."""
    payer = await _person(session, "Хём", funds=100)
    payee = await _person(session, "Тэрн")
    await finance.transfer(session, payer, payee.name, money(30), memo="за руду")

    rows, _ = await finance.statement(session, payee.id)
    received = rows[0]
    assert received["reason"] == "transfer" and received["incoming"]

    opened = await finance.posting(session, payee.id, received["id"])
    assert {(side["with"], side["incoming"]) for side in opened["sides"]} == {
        (payer.name, False),
        (payee.name, True),
    }
    assert all("mine" not in side for side in opened["sides"])
    assert all(side["money"] == "30" for side in opened["sides"])
    assert opened["deal"] is None and opened["order"] is None, "перевод — не сделка и не ордер"


async def test_a_row_opens_for_its_owner_only(session: AsyncSession) -> None:
    """A statement is the owner's (D-190), and a row number is not a key to
    somebody else's: the payer's own leg has a different id, and the payee's
    id is not theirs to open."""
    payer = await _person(session, "Хём", funds=100)
    payee = await _person(session, "Тэрн")
    stranger = await _person(session, "Прохожий", funds=1)
    await finance.transfer(session, payer, payee.name, money(30))

    rows, _ = await finance.statement(session, payee.id)
    theirs = rows[0]["id"]
    with pytest.raises(finance.NoSuchPosting):
        await finance.posting(session, payer.id, theirs)
    with pytest.raises(finance.NoSuchPosting):
        await finance.posting(session, stranger.id, theirs)
    with pytest.raises(finance.NoSuchPosting):
        #: No account at all: nothing to open, and nothing is created by asking.
        await finance.posting(session, uuid.uuid4(), theirs)
    with pytest.raises(finance.NoSuchPosting):
        await finance.posting(session, payee.id, 0)


async def test_a_sale_opens_into_the_deal_and_a_deposit_into_its_order(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The seller's `trade` row says what was sold, to whom and where; the
    buyer's `escrow_hold` row says what the money was frozen for and what it
    bought.

    The buyer never gets a `trade` row of their own: the money went to the
    escrow first, and the settlement is written against the escrow. So the
    deposit row is where the buyer reads their purchase, fills and all.
    """
    node = await _city(session)
    seller, _ = await _with_goods(session, constants, node, "Рудокоп", qty=10, quality=65)
    buyer, body = await _trader(session, node, "Кузнец", funds=100)
    tier = market.tier_of(constants, 65)
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
    fill = await market.buy(
        session, constants, catalog, body, type_key=ORE, tier=tier, price=money(5), quantity=4
    )
    trade = fill.trades[0]

    #: The seller's side: the deal, from the event that grounds the settlement.
    rows, _ = await finance.statement(session, seller.id)
    sale = next(row for row in rows if row["reason"] == "trade")
    assert sale["incoming"] and sale["side"] == "escrow", "продавцу платит залог покупателя"
    opened = await finance.posting(session, seller.id, sale["id"])
    deal = opened["deal"]
    assert deal is not None and opened["order"] is None
    assert deal["goods"] == ORE and deal["tier"] == tier
    assert deal["amount"] == pytest.approx(4)
    assert deal["price"] == "5"
    assert deal["tax"] == money_str(trade.tax) and deal["fee"] == money_str(trade.fee)
    assert deal["buyer"] == buyer.name
    assert deal["market"] == "Торг" and deal["reserved"] is False
    #: The seller is not named and the cost is not repeated (D-225): the
    #: reader is the seller, and the cost is the escrow's leg among the
    #: sides -- the one that gave, named by the reader's own name beside it.
    assert "seller" not in deal and "cost" not in deal
    gave = [side for side in opened["sides"] if not side["incoming"]]
    assert [(side["side"], side["money"]) for side in gave] == [("escrow", "20")]
    assert any(side["with"] == seller.name and side["incoming"] for side in opened["sides"])

    #: The buyer's side: no deal row at all, and the deposit opens into the order.
    rows, _ = await finance.statement(session, buyer.id)
    assert all(row["reason"] != "trade" for row in rows), "покупатель видит задаток, не сделку"
    hold = next(row for row in rows if row["reason"] == "escrow_hold")
    opened = await finance.posting(session, buyer.id, hold["id"])
    held = opened["order"]
    assert held is not None and opened["deal"] is None
    assert held["goods"] == ORE and held["tier"] == tier and held["price"] == "5"
    assert held["amount"] == pytest.approx(4) and held["market"] == "Торг"
    assert "left" not in held, "остаток ордера — это ордер минус исполнения (D-225)"
    assert [(one["with"], one["price"]) for one in held["fills"]] == [(seller.name, "5")]
    assert held["fills"][0]["amount"] == pytest.approx(4)


async def test_a_released_deposit_opens_into_the_same_order(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A buy bigger than the book is filled in part; cancelling it returns the
    rest of the deposit, and that `escrow_release` row opens into the order
    too -- the same one, with the fill it did get."""
    node = await _city(session)
    seller, _ = await _with_goods(session, constants, node, "Рудокоп", qty=10, quality=65)
    buyer, body = await _trader(session, node, "Оптовик", funds=100)
    tier = market.tier_of(constants, 65)
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
    fill = await market.buy(
        session, constants, catalog, body, type_key=ORE, tier=tier, price=money(5), quantity=6
    )
    assert fill.traded == pytest.approx(4)
    order = await session.get(Order, fill.order.id)
    await market.cancel(session, order, by=buyer.id)

    rows, _ = await finance.statement(session, buyer.id)
    released = next(row for row in rows if row["reason"] == "escrow_release")
    assert released["incoming"] and released["money"] == "10", "за две руды по пять вернулось"
    opened = await finance.posting(session, buyer.id, released["id"])
    held = opened["order"]
    assert held is not None
    assert held["amount"] == pytest.approx(6) and held["price"] == "5"
    assert [(one["with"], one["amount"]) for one in held["fills"]] == [(seller.name, 4)]


async def test_a_work_order_payout_faces_the_order_rather_than_a_deal(
    session: AsyncSession, constants: Constants
) -> None:
    """A work order's pay waits in an escrow, as a buyer's deposit does, and
    one account kind holds both (D-248). The worker's row used to read
    "trade escrow" where no deal ever happened.

    The escrow an order owns is sent as a side of its own; a buyer's deposit
    stays `escrow` -- the sale above pins that half. A road order stands in
    for every kind: the side is told by the owner, not by what the order is.
    """
    stamp = uuid.uuid4().hex[:8]
    here = await world.create_node(session, f"terra.fin.{stamp}", "Здесь", area_m2=100)
    there = await world.create_node(session, f"terra.fio.{stamp}", "Там", area_m2=100)
    edge = await travel.connect(session, here, there, base_seconds=600, surface=Surface.ROAD)
    edge.condition = Decimal("50")
    await session.flush()
    worker = await _person(session, "Дорожник")
    tariff = works.road_tariff(constants)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        PostingReason.WORKS_PRINT,
        debit=genesis.id,
        credit=(await works.fund_account(session)).id,
        amount=tariff,
    )
    assert await works.post_road_orders(session, constants, now=datetime.now(UTC)) == 1
    edge.condition = Decimal("100")
    await session.flush()
    paid = await works.pay_road_order(session, constants, edge, worker.id)
    assert paid > 0

    rows, _ = await finance.statement(session, worker.id)
    payout = next(row for row in rows if row["reason"] == "works_payout")
    assert payout["incoming"] and payout["money"] == money_str(paid)
    assert (payout["with"], payout["side"]) == (None, finance.WORK_ORDER), "платит госзаказ"

    opened = await finance.posting(session, worker.id, payout["id"])
    assert [(side["with"], side["side"], side["incoming"]) for side in opened["sides"]] == [
        (None, finance.WORK_ORDER, False),
        (worker.name, None, True),
    ]
    assert opened["deal"] is None and opened["order"] is None


async def test_each_kind_of_side_is_named_on_one_page(
    session: AsyncSession, catalog: Catalog
) -> None:
    """A person by name, a treasury by its city, the issue by its kind -- all
    resolved for the page at once, and the row opens into the same words."""
    city, _ = await _capital(session, catalog, funds=100)
    clerk = await _person(session, "Писарь", funds=5)
    treasury = await ledger.account_for(session, AccountKind.CITY_TREASURY, city.node_id)
    wallet = await ledger.account_for(session, AccountKind.IDENTITY, clerk.id)
    await ledger.transfer(
        session, PostingReason.SALARY, debit=treasury.id, credit=wallet.id, amount=money(7)
    )
    payee = await _person(session, "Счетовод")
    await finance.transfer(session, clerk, payee.name, money(2))

    rows, _ = await finance.statement(session, clerk.id)
    assert [(row["reason"], row["with"], row["side"]) for row in rows] == [
        ("transfer", payee.name, None),
        ("salary", city.name, "city_treasury"),
        ("genesis", None, "genesis"),
    ]
    opened = await finance.posting(session, clerk.id, rows[1]["id"])
    assert [(side["with"], side["side"]) for side in opened["sides"]] == [
        (city.name, "city_treasury"),
        (clerk.name, None),
    ]


async def test_a_page_of_deposits_costs_the_same_whatever_its_length(
    session: AsyncSession, constants: Constants, catalog: Catalog, counted
) -> None:
    """Which escrows belong to a work order is asked once for the page.

    A buyer's escrow is owned by the buyer, so looking its owner up among
    work orders row by row misses on every deposit -- and the session keeps
    no memory of a miss, so each row of the page would cost one more query.
    """
    node = await _city(session)
    buyer, body = await _trader(session, node, "Скупщик", funds=100)
    tier = market.tier_of(constants, 65)

    async def bid(times: int) -> None:
        for _ in range(times):
            await market.buy(
                session,
                constants,
                catalog,
                body,
                type_key=ORE,
                tier=tier,
                price=money(1),
                quantity=1,
            )

    async def read() -> tuple[int, list[str | None]]:
        before = counted.count
        rows, _ = await finance.statement(session, buyer.id)
        held = [row["side"] for row in rows if row["reason"] == "escrow_hold"]
        return counted.count - before, held

    await bid(2)
    few, held = await read()
    assert held == ["escrow"] * 2, "задаток покупателя остаётся залогом сделки"
    await bid(4)
    many, held = await read()
    assert held == ["escrow"] * 6
    assert many == few, "страница задатков не дороже от длины"
