# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The works fund and the state order board (D-248).

Checked is what the fund exists for:

* the reserve surplus burns under high inflation, feeds the fund under low,
  splits linearly in between, and a silent sensor moves nothing;
* the print tap is closed at cap zero and opens by constant, only under
  deflation, only up to the shortfall;
* a road order is posted with its payout escrowed -- an empty fund posts
  nothing -- and a verified mend collects it exactly once, cap and cooldown
  holding whichever way the race goes;
* the supply invariant "total = accounts + reserve + fund" survives every
  move.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Constants
from src.constants import registry as R
from src.engine import bank, ledger, road, travel, works, world
from src.models.ledger import AccountKind, PostingReason
from src.models.metrics import DailyMetric
from src.models.works import WorkOrder, WorkOrderState
from src.models.world import Edge, Surface
from src.units import PERCENT, money


async def _inflation_rows(session: AsyncSession, *, old: float, new: float) -> None:
    """Two index points: enough for the sensor to speak."""
    today = datetime.now(UTC).date()
    session.add(DailyMetric(day=today - timedelta(days=1), key=bank.PRICE_INDEX, value=old))
    session.add(DailyMetric(day=today, key=bank.PRICE_INDEX, value=new))
    await session.flush()


async def _reserve_surplus(session: AsyncSession, constants: Constants, catalog) -> int:
    """Build a reserve well above the ceiling the way the world does: interest and repayment."""
    who = await world.create_identity(session, f"Заёмщик-{uuid.uuid4().hex[:6]}")
    account = await ledger.account_for(session, AccountKind.IDENTITY, who.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session, PostingReason.GENESIS, debit=genesis.id, credit=account.id, amount=money(100)
    )
    loan = await bank.borrow(session, constants, catalog, who, 200)
    await bank.repay(session, constants, who, loan, 200)
    ceiling = int(await bank.circulating(session) * constants[R.BANK_RESERVE_CAP] / PERCENT)
    surplus = await bank.reserve(session) - ceiling
    assert surplus > 0, "резерв заведомо выше потолка"
    return surplus


async def _total_supply_split(session: AsyncSession) -> tuple[int, int, int, int]:
    from src.telemetry import metrics

    total = await ledger.money_supply(session)
    reserve = await metrics.kind_total(session, AccountKind.BANK_RESERVE)
    fund = await metrics.kind_total(session, AccountKind.WORKS_FUND)
    return total, await bank.circulating(session), reserve, fund


async def _edge_with_worker(session: AsyncSession, *, condition: float, surface_amount: float = 0):
    stamp = uuid.uuid4().hex[:8]
    here = await world.create_node(session, f"terra.wka.{stamp}", "Здесь", area_m2=100)
    there = await world.create_node(session, f"terra.wkb.{stamp}", "Там", area_m2=100)
    edge = await travel.connect(session, here, there, base_seconds=600, surface=Surface.ROAD)
    edge.condition = Decimal(str(condition))
    await session.flush()
    identity = await world.create_identity(session, f"Дорожник-{stamp}")
    body = await world.print_body(session, identity, here)
    if surface_amount:
        pocket = await world.body_container(session, body)
        await world.grant_item(
            session,
            pocket,
            "road_paving",
            amount=surface_amount,
            origin="сценарий теста",
        )
    return identity, body, edge


async def _feed_fund(session: AsyncSession, amount: int) -> None:
    """Top the fund up directly: the recycling path has tests of its own."""
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        PostingReason.WORKS_PRINT,
        debit=genesis.id,
        credit=(await works.fund_account(session)).id,
        amount=amount,
    )


# --- the recycle split (D-248 over D-169) -------------------------------------


def test_recycle_share_is_a_public_function_of_inflation(constants: Constants) -> None:
    goal = constants[R.BANK_TARGET_INFLATION]
    ramp = constants[R.WORKS_RECYCLE_RAMP]

    assert works.recycle_share(constants, None) is None, "немой датчик не двигает рычаг"
    assert works.recycle_share(constants, goal + 5) == 0.0, "перегрев — всё сжигается"
    assert works.recycle_share(constants, goal) == 0.0
    assert works.recycle_share(constants, goal - ramp) == pytest.approx(1.0)
    assert works.recycle_share(constants, goal - ramp * 3) == pytest.approx(1.0)
    assert works.recycle_share(constants, goal - ramp / 2) == pytest.approx(0.5), (
        "между целью и порогом — линейно"
    )


async def test_surplus_feeds_fund_under_deflation(
    session: AsyncSession, constants: Constants, catalog
) -> None:
    """The promise of D-248: interest income goes back to the world, not to the fire."""
    surplus = await _reserve_surplus(session, constants, catalog)
    await _inflation_rows(session, old=100, new=95)

    total_before, *_ = await _total_supply_split(session)
    burned, recycled = await works.recycle(session, constants)

    assert burned == 0
    assert recycled == surplus
    assert await works.fund_balance(session) == surplus
    total, circulating, reserve, fund = await _total_supply_split(session)
    assert total == total_before, "возврат в фонд ничего не печатает и не жжёт"
    assert total == circulating + reserve + fund, "масса = счета + резерв + фонд"


async def test_silent_sensor_holds_the_surplus(
    session: AsyncSession, constants: Constants, catalog
) -> None:
    """No inflation data: burning and recycling are both reactions, neither fires."""
    await _reserve_surplus(session, constants, catalog)
    before = await bank.reserve(session)
    assert await works.recycle(session, constants) == (0, 0)
    assert await bank.reserve(session) == before


async def test_mid_ramp_splits_linearly(
    session: AsyncSession, constants: Constants, catalog
) -> None:
    surplus = await _reserve_surplus(session, constants, catalog)
    goal = constants[R.BANK_TARGET_INFLATION]
    ramp = constants[R.WORKS_RECYCLE_RAMP]
    #: Inflation exactly half a ramp below target: half burns, half returns.
    await _inflation_rows(session, old=100, new=100 * (1 + (goal - ramp / 2) / PERCENT))

    burned, recycled = await works.recycle(session, constants)
    assert recycled == pytest.approx(surplus / 2, abs=1)
    assert burned + recycled == surplus


# --- the print tap ------------------------------------------------------------


async def test_print_tap_starts_closed(
    session: AsyncSession, constants: Constants, catalog
) -> None:
    """`works.print_cap` is zero at the start: the mechanism is in, the tap is shut."""
    await _inflation_rows(session, old=100, new=95)
    assert await works.print_into_fund(session, constants, money(100)) == 0
    assert await works.fund_balance(session) == 0


async def test_print_opens_by_constant_and_only_under_deflation(
    session: AsyncSession, constants: Constants, catalog
) -> None:
    opened = constants.with_overrides({"works.print_cap": 50})
    #: Somebody must hold money: the ceiling is a share of circulation.
    who = await world.create_identity(session, f"Богач-{uuid.uuid4().hex[:6]}")
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        PostingReason.GENESIS,
        debit=genesis.id,
        credit=(await ledger.account_for(session, AccountKind.IDENTITY, who.id)).id,
        amount=money(1000),
    )

    await _inflation_rows(session, old=100, new=95)
    need = money(100)
    printed = await works.print_into_fund(session, opened, need)
    assert printed == need, "печатается ровно нехватка, не потолок"
    assert await works.fund_balance(session) == need

    #: And the second call prints nothing: the need is covered.
    assert await works.print_into_fund(session, opened, need) == 0


async def test_no_print_when_inflation_at_or_above_target(
    session: AsyncSession, constants: Constants, catalog
) -> None:
    opened = constants.with_overrides({"works.print_cap": 50})
    await _inflation_rows(session, old=100, new=110)
    assert await works.print_into_fund(session, opened, money(100)) == 0


async def test_printed_money_enters_emission_share(
    session: AsyncSession, constants: Constants, catalog
) -> None:
    """The tap cannot hide from the rate formula (D-248)."""
    await _feed_fund(session, money(50))
    share = await bank._emission_share(session, constants, now=datetime.now(UTC))
    assert share == pytest.approx(100.0), "вся выдача окна — печать в фонд"


# --- the board: road orders ----------------------------------------------------


async def test_sagged_edge_gets_an_order_with_escrow(
    session: AsyncSession, constants: Constants, catalog
) -> None:
    _, _, edge = await _edge_with_worker(session, condition=50)
    tariff = works.road_tariff(constants)
    await _feed_fund(session, tariff)

    posted = await works.post_road_orders(session, constants, now=datetime.now(UTC))
    assert posted == 1

    order = await works.open_road_order(session, edge)
    assert order is not None and order.tariff == tariff
    escrow = await ledger.account_for(session, AccountKind.ESCROW, order.id)
    assert await ledger.balance(session, escrow.id) == tariff, "оплата отложена при вывеске"
    assert await works.fund_balance(session) == 0

    #: The board never promises what the fund does not hold: the next sagged
    #: edge stays without an order until money returns.
    await _edge_with_worker(session, condition=40)
    assert await works.post_road_orders(session, constants, now=datetime.now(UTC)) == 0


async def test_intact_edge_gets_no_order(
    session: AsyncSession, constants: Constants, catalog
) -> None:
    await _edge_with_worker(session, condition=90)
    await _feed_fund(session, works.road_tariff(constants))
    assert await works.post_road_orders(session, constants, now=datetime.now(UTC)) == 0


async def test_mend_collects_the_order(
    session: AsyncSession, constants: Constants, catalog
) -> None:
    """The whole loop: the world sees the need, the worker mends, the fund pays."""
    norm = constants[R.ROAD_SURFACE_PER_EDGE]
    identity, body, edge = await _edge_with_worker(session, condition=50, surface_amount=norm)
    tariff = works.road_tariff(constants)
    await _feed_fund(session, tariff)
    assert await works.post_road_orders(session, constants, now=datetime.now(UTC)) == 1

    job = await road.lay(session, constants, catalog, body, edge, mend=True)
    await road.finished(session, job)

    expected = min(tariff, money(constants[R.WORKS_PLAYER_DAILY_CAP]))
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    assert await ledger.balance(session, account.id) == expected
    order = (
        (await session.execute(select(WorkOrder).where(WorkOrder.edge_id == edge.id)))
        .scalars()
        .one()
    )
    assert order.state is WorkOrderState.DONE
    assert order.done_by == identity.id
    escrow = await ledger.account_for(session, AccountKind.ESCROW, order.id)
    assert await ledger.balance(session, escrow.id) == 0, "эскроу опустело: оплата и остаток ушли"

    total, circulating, reserve, fund = await _total_supply_split(session)
    assert total == circulating + reserve + fund


async def test_payout_clipped_by_daily_cap(
    session: AsyncSession, constants: Constants, catalog
) -> None:
    """The rest above the cap is not the worker's and returns to the fund."""
    clipped = constants.with_overrides({"works.player_daily_cap": 0.05})
    norm = clipped[R.ROAD_SURFACE_PER_EDGE]
    identity, body, edge = await _edge_with_worker(session, condition=50, surface_amount=norm)
    tariff = works.road_tariff(clipped)
    await _feed_fund(session, tariff)
    assert await works.post_road_orders(session, clipped, now=datetime.now(UTC)) == 1

    order = await works.open_road_order(session, edge)
    edge.condition = Decimal("100")
    await session.flush()
    paid = await works.pay_road_order(session, clipped, edge, identity.id)

    assert paid == money(0.05)
    assert await works.fund_balance(session) == tariff - paid, "остаток вернулся в фонд"
    assert order is not None and order.state is WorkOrderState.DONE


async def test_cooldown_blocks_a_repeat_order(
    session: AsyncSession, constants: Constants, catalog
) -> None:
    """Belt and braces against break-and-fix farming."""
    identity, _, edge = await _edge_with_worker(session, condition=50)
    tariff = works.road_tariff(constants)
    await _feed_fund(session, tariff * 2)
    moment = datetime.now(UTC)
    assert await works.post_road_orders(session, constants, now=moment) == 1
    edge.condition = Decimal("100")
    await session.flush()
    await works.pay_road_order(session, constants, edge, identity.id, now=moment)

    #: Sagged again at once -- the object is on cooldown, the board is silent.
    edge.condition = Decimal("50")
    await session.flush()
    assert await works.post_road_orders(session, constants, now=moment) == 0

    #: The cooldown passed -- the order returns.
    later = moment + timedelta(days=constants[R.WORKS_OBJECT_COOLDOWN] + 1)
    assert await works.post_road_orders(session, constants, now=later) == 1


async def test_stale_order_returns_its_escrow(
    session: AsyncSession, constants: Constants, catalog
) -> None:
    """The edge decayed a tier under the order: a different project, the money comes home."""
    _, _, edge = await _edge_with_worker(session, condition=50)
    tariff = works.road_tariff(constants)
    await _feed_fund(session, tariff)
    assert await works.post_road_orders(session, constants, now=datetime.now(UTC)) == 1

    edge.surface = Surface.TRAIL
    edge.condition = Decimal("100")
    await session.flush()

    assert await works.cancel_stale_road_orders(session, now=datetime.now(UTC)) == 1
    assert await works.fund_balance(session) == tariff
    order = (
        (await session.execute(select(WorkOrder).where(WorkOrder.edge_id == edge.id)))
        .scalars()
        .one()
    )
    assert order.state is WorkOrderState.CANCELLED


async def test_two_completions_pay_once(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The race the money rule demands: two sessions collect one order.

    The order row is locked before its state is read; the loser waits at the
    lock, rereads and finds the order DONE. Without the lock both read OPEN
    and the fund pays twice for one mend.
    """
    identity, _, edge = await _edge_with_worker(session, condition=50)
    tariff = works.road_tariff(constants)
    await _feed_fund(session, tariff)
    assert await works.post_road_orders(session, constants, now=datetime.now(UTC)) == 1
    edge.condition = Decimal("100")
    await session.flush()
    edge_id, identity_id = edge.id, identity.id
    await session.commit()

    original = works.paid_today

    async def held(*args, **kwargs):
        result = await original(*args, **kwargs)
        await asyncio.sleep(0.2)
        return result

    monkeypatch.setattr(works, "paid_today", held)

    async def collect() -> int:
        async with factory() as db, db.begin():
            target = await db.get(Edge, edge_id)
            assert target is not None
            return await works.pay_road_order(db, constants, target, identity_id)

    first, second = await asyncio.gather(collect(), collect())
    assert sorted((first, second)) == [0, tariff], "оплата ровно одна на заказ"

    async with factory() as db:
        account = await ledger.account_for(db, AccountKind.IDENTITY, identity_id)
        assert await ledger.balance(db, account.id) == tariff


async def test_parallel_orders_cannot_exceed_daily_cap(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cap is one counter over *all* the worker's orders, and the worker
    runs several lanes: two mends landing together must not each read the cap
    before the other pays. The recipient's account row serialises them --
    without that lock both read zero paid and the cap is worth nothing.
    """
    tariff = works.road_tariff(constants)
    #: The cap admits exactly one full tariff: the second payout must clip to zero.
    capped = constants.with_overrides(
        {"works.player_daily_cap": constants[R.ROAD_BUILD_HOURS] * constants[R.WORKS_HOUR_RATE]}
    )
    identity, _, edge_a = await _edge_with_worker(session, condition=50)
    _, _, edge_b = await _edge_with_worker(session, condition=50)
    await _feed_fund(session, tariff * 2)
    assert await works.post_road_orders(session, capped, now=datetime.now(UTC)) == 2
    edge_a.condition = Decimal("100")
    edge_b.condition = Decimal("100")
    #: The recipient's account exists up front: the race under test is the cap
    #: read, not two inserts of one account row.
    await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    await session.flush()
    edge_ids, identity_id = (edge_a.id, edge_b.id), identity.id
    await session.commit()

    original = works.paid_today

    async def held(*args, **kwargs):
        result = await original(*args, **kwargs)
        await asyncio.sleep(0.2)
        return result

    monkeypatch.setattr(works, "paid_today", held)

    async def collect(edge_id) -> int:
        async with factory() as db, db.begin():
            target = await db.get(Edge, edge_id)
            assert target is not None
            return await works.pay_road_order(db, capped, target, identity_id)

    payments = await asyncio.gather(*(collect(edge_id) for edge_id in edge_ids))
    assert sorted(payments) == [0, tariff], "потолок один на игрока, а не на заказ"

    async with factory() as db:
        account = await ledger.account_for(db, AccountKind.IDENTITY, identity_id)
        assert await ledger.balance(db, account.id) == tariff
        assert await works.fund_balance(db) == tariff, "срезанный остаток вернулся в фонд"
