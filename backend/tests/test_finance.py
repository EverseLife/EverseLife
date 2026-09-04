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

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from market_kit import ORE, _city, _trader, _with_goods
from src.constants import Catalog, Constants
from src.engine import finance, ledger, market, world
from src.models.ledger import AccountKind, PostingReason
from src.models.market import Order
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
