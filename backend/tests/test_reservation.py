# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A reservation with a deposit and a term (D-047).

Remote buying is impossible: otherwise a player buys everything everywhere,
goods hang in reserve, and the books become a fiction. A reservation is the
reasonable exception, and it is built so that dead reserves do not arise:

* the deposit is paid at once and goes to escrow -- a reserve costs money;
* the goods leave the book but stay with the seller: they go nowhere;
* one can collect only upon arrival -- geography is intact;
* did not collect in time -- the deposit to the seller, the goods back to the book.
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

ORE = "iron_ore"


async def _market(session: AsyncSession, *, price=3, qty=20, quality=64):
    """A node with a terminal, a seller with goods and a buyer with money."""
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.mkt.{stamp}", "Рынок", area_m2=200)
    yard = await world.node_container(session, node)
    await world.grant_item(session, yard, "market_terminal", quality=70, origin="тест")

    seller = await world.create_identity(session, f"Продавец-{stamp}")
    seller_body = await world.print_body(session, seller, node)
    pocket = await world.body_container(session, seller_body)
    await world.grant_item(session, pocket, ORE, amount=qty, quality=quality, origin="тест")
    constants, catalog = current(), current_catalog()
    await market.load(session, constants, seller_body, ORE, qty)
    order = (
        await market.sell(
            session,
            constants,
            catalog,
            seller,
            node,
            type_key=ORE,
            tier=market.tier_of(constants, quality),
            price=money(price),
            quantity=qty,
        )
    ).order

    buyer = await world.create_identity(session, f"Купец-{stamp}")
    merchant_body = await world.print_body(session, buyer, node)
    account = await ledger.account_for(session, AccountKind.IDENTITY, buyer.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        PostingReason.GENESIS,
        debit=genesis.id,
        credit=account.id,
        amount=money(500),
        memo={},
    )
    return node, order, seller, buyer, merchant_body


# --- reservation -------------------------------------------------------------


async def test_reservation_takes_deposit_and_removes_goods_from_book(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A reserve costs money and is visible in the book: what is promised is not shown to others."""
    node, order, _, buyer, _ = await _market(session, price=3, qty=20)
    before = await ledger.balance(
        session, (await ledger.account_for(session, AccountKind.IDENTITY, buyer.id)).id
    )

    reservation = await market.reserve(session, constants, buyer, order, 10)

    total = money(3) * 10
    expected_deposit = int(total * constants[R.MARKET_RESERVATION_DEPOSIT] / PERCENT)
    assert reservation.deposit == expected_deposit
    assert amount_float(order.amount_left) == 10, "забронированное ушло из книги"

    after = await ledger.balance(
        session, (await ledger.account_for(session, AccountKind.IDENTITY, buyer.id)).id
    )
    assert before - after == expected_deposit


async def test_own_goods_not_reservable(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, order, seller, _, _ = await _market(session)
    #: `NotYours` is raised by three different checks: the key says which.
    with pytest.raises(market.NotYours) as refused:
        await market.reserve(session, constants, seller, order, 1)
    assert refused.value.key == "market-reserve-own"


async def test_cannot_reserve_more_than_available(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, order, _, buyer, _ = await _market(session, qty=5)
    with pytest.raises(market.NoGoods) as refused:
        await market.reserve(session, constants, buyer, order, 50)
    assert refused.value.key == "market-reserve-too-much"


# --- redemption --------------------------------------------------------------


async def test_redemption_pays_remainder_and_hands_goods(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Coming is mandatory: a reservation does not cancel geography, it plans it."""
    node, order, seller, buyer, body = await _market(session, price=3, qty=20)
    reservation = await market.reserve(session, constants, buyer, order, 10)

    merchant_account = await ledger.account_for(session, AccountKind.IDENTITY, buyer.id)
    seller_account = await ledger.account_for(session, AccountKind.IDENTITY, seller.id)
    merchant_before = await ledger.balance(session, merchant_account.id)
    seller_before = await ledger.balance(session, seller_account.id)

    deal = await market.redeem(session, constants, catalog, body, reservation)

    total = money(3) * 10
    assert reservation.state is ReservationState.REDEEMED
    #: The merchant paid exactly the remainder: the deposit was already in escrow.
    assert merchant_before - await ledger.balance(session, merchant_account.id) == (
        total - reservation.deposit
    )
    #: The seller got everything, minus city withholdings (none in an unowned node).
    assert await ledger.balance(session, seller_account.id) - seller_before == (
        total - deal.tax - deal.fee
    )

    cell = await market.stall(session, node, buyer.id)
    from sqlalchemy import select

    from src.models.inventory import Item

    goods = (
        (
            await session.execute(
                select(Item).where(Item.container_id == cell.id, Item.type_key == ORE)
            )
        )
        .scalars()
        .all()
    )
    assert sum(amount_float(i_.amount) for i_ in goods) == pytest.approx(10)


async def test_redeem_only_upon_arrival(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Matter requires presence -- a reservation changes nothing here (D-047)."""
    node, order, _, buyer, body = await _market(session)
    reservation = await market.reserve(session, constants, buyer, order, 5)

    away = await world.create_node(
        session, f"terra.away.{uuid.uuid4().hex[:6]}", "Прочь", area_m2=50
    )
    await travel.connect(session, node, away, base_seconds=30)
    await travel.depart(session, constants, body, away)

    with pytest.raises(travel.InTransit):
        await market.redeem(session, constants, catalog, body, reservation)


async def test_foreign_reservation_not_redeemable(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    node, order, _, buyer, _ = await _market(session)
    reservation = await market.reserve(session, constants, buyer, order, 5)

    foreign = await world.create_identity(session, f"Чужой-{uuid.uuid4().hex[:6]}")
    foreign_body = await world.print_body(session, foreign, node)
    with pytest.raises(market.NotYours) as refused:
        await market.redeem(session, constants, catalog, foreign_body, reservation)
    assert refused.value.key == "market-reservation-not-yours"


# --- term --------------------------------------------------------------------


async def test_reservation_term_from_vault(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`market.reservation_period` days, and the day is planetary (D-008)."""
    _, order, _, buyer, _ = await _market(session)
    moment = datetime.now(UTC)
    reservation = await market.reserve(session, constants, buyer, order, 5, now=moment)
    term = timedelta(hours=constants[R.MARKET_RESERVATION_PERIOD] * constants[R.TIME_DAY_TERRA])
    assert reservation.expires_at == moment + term


async def test_expired_reservation_gives_deposit_to_seller_and_goods_to_book(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Did not collect -- you pay for the goods having waited. A reserve is never dead."""
    async with factory() as session, session.begin():
        _, order, seller, buyer, _ = await _market(session, price=3, qty=20)
        reservation = await market.reserve(session, constants, buyer, order, 10)
        term, reservation_id, order_id = reservation.expires_at, reservation.id, order.id
        deposit = reservation.deposit
        seller_id = seller.id

    job = await jobs.run_one(factory, now=term)
    assert job is not None and job.kind == "market.reservation_expiry"

    async with factory() as session:
        reservation = await session.get(type(reservation), reservation_id)
        order = await session.get(Order, order_id)
        assert reservation.state is ReservationState.LAPSED
        assert amount_float(order.amount_left) == 20, "товар вернулся в книгу"

        account = await ledger.account_for(session, AccountKind.IDENTITY, seller_id)
        #: The seller got the deposit -- payment for waiting.
        assert await ledger.balance(session, account.id) >= deposit


async def test_cannot_redeem_after_term(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, order, _, buyer, body = await _market(session)
    moment = datetime.now(UTC)
    reservation = await market.reserve(session, constants, buyer, order, 5, now=moment)
    with pytest.raises(market.BadOrder) as refused:
        await market.redeem(
            session,
            constants,
            catalog,
            body,
            reservation,
            now=reservation.expires_at + timedelta(minutes=1),
        )
    assert refused.value.key == "market-reservation-expired"
