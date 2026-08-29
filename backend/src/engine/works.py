# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The works fund and the state order board (D-248).

Interest was a pump: the city margin returns to circulation through the
treasury, but the key part sank into the reserve for ever, and in a steady
state the players as a whole could not settle their debts. As a real central
bank remits its profit to the budget and the budget spends it, here the
interest income returns to the world -- as **pay for verifiable labour**,
never as a handout.

## The fund

An impersonal system account, like the reserve (the CB is nobody's, D-031).
It fills from the reserve surplus above `bank.reserve_cap`: the share going to
the fund is a public function of inflation -- above target everything burns
(the original role of D-169), `works.recycle_ramp` points below target and
deeper everything goes to the fund, linear in between. A silent sensor moves
no lever (D-030): with no inflation data the surplus stays in the reserve.

Under persistent deflation the CB may print into the fund -- interest-free,
like all base money -- under the daily ceiling `works.print_cap`. The ceiling
starts at zero: the tap is built in but closed, and opens by constant, not by
release. What is printed enters the emission share, so the rate formula sees
it by itself.

## The board

The fund buys no goods (a bottomless counterparty is banned, D-002): it pays
a labour tariff for orders whose completion the engine verifies in its own
data. Posting an order escrows its payout, so the board never promises what
the fund does not hold; an empty fund posts nothing -- as an empty treasury
pays no settlement grant.

Road orders are posted by the engine itself: an edge whose surface condition
fell below `works.road_threshold` is need the world can see, no will
required. The order is claimless: whoever finishes the mend first collects.
Against farming: a cooldown per object and a daily payout cap per player.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import bank, events, ledger
from src.models.event import EventKind
from src.models.ledger import AccountKind, LedgerAccount, LedgerEntry, LedgerTransaction
from src.models.ledger import PostingReason as Reason
from src.models.works import WorkOrder, WorkOrderKind, WorkOrderState
from src.models.world import Edge, Node, Surface
from src.units import PERCENT, SCALE_MAX, money, money_str

#: Owner of the fund account. One fund per world, impersonal like the reserve.
FUND = uuid.UUID("00000000-0000-0000-0000-00000000d248")


async def fund_account(session: AsyncSession) -> LedgerAccount:
    return await ledger.account_for(session, AccountKind.WORKS_FUND, FUND)


async def fund_balance(session: AsyncSession) -> int:
    """The fund's balance. Read-only: a view must not create the account."""
    account = await ledger.find_account(session, AccountKind.WORKS_FUND, FUND)
    return 0 if account is None else await ledger.balance(session, account.id)


def recycle_share(constants: Constants, inflation: float | None) -> float | None:
    """The share of the reserve surplus that goes to the fund, 0..1.

    A public function of one sensor (D-030): above target inflation nothing
    (the surplus burns, D-169), `works.recycle_ramp` points below target and
    deeper -- everything, linear in between. No data -- `None`: burning and
    recycling are both reactions, and a silent sensor justifies neither.
    """
    if inflation is None:
        return None
    goal = constants[R.BANK_TARGET_INFLATION]
    ramp = constants[R.WORKS_RECYCLE_RAMP]
    if inflation >= goal:
        return 0.0
    if ramp <= 0:
        return 1.0
    return min(1.0, (goal - inflation) / ramp)


async def recycle(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> tuple[int, int]:
    """Split the reserve surplus above the ceiling: burn or feed the fund (D-248).

    Returns (burned, recycled). The ceiling is a share of the circulating
    supply, not an absolute sum (D-169): the world grows, and what is a huge
    reserve today is pocket change in a hundred days.
    """
    in_reserve = await bank.reserve(session)
    in_circulation = await bank.circulating(session)
    ceiling = int(in_circulation * constants[R.BANK_RESERVE_CAP] / PERCENT)
    surplus = in_reserve - ceiling
    if surplus <= 0:
        return 0, 0

    share = recycle_share(constants, await bank.inflation(session, constants))
    if share is None:
        return 0, 0

    to_fund = int(surplus * share)
    to_burn = surplus - to_fund
    reserve_treasury = await bank.reserve_account(session)

    if to_burn > 0:
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            Reason.GENESIS,
            debit=reserve_treasury.id,
            credit=genesis.id,
            amount=to_burn,
            memo={"сжигание излишка резерва": money_str(to_burn)},
        )
        await events.record(
            session,
            EventKind.RESERVE_BURNED,
            amount=to_burn,
            reserve=in_reserve - surplus,
            circulating=in_circulation,
        )
    if to_fund > 0:
        await ledger.transfer(
            session,
            Reason.WORKS_RECYCLE,
            debit=reserve_treasury.id,
            credit=(await fund_account(session)).id,
            amount=to_fund,
            memo={"возврат в фонд работ": money_str(to_fund)},
        )
        await events.record(
            session,
            EventKind.WORKS_FUNDED,
            recycled=to_fund,
            burned=to_burn,
            fund=await fund_balance(session),
        )
    return to_burn, to_fund


async def print_into_fund(
    session: AsyncSession, constants: Constants, need: int, *, now: datetime | None = None
) -> int:
    """Print the shortfall for pending orders under `works.print_cap` (D-248).

    Only the CB prints, and printing is interest-free -- base money has no
    creditor. The tap opens only on the deflation side of the target, and the
    daily ceiling is a share of the circulating supply; at the start it is
    zero: the mechanism is in, the tap is closed.
    """
    shortfall = need - await fund_balance(session)
    if shortfall <= 0:
        return 0
    inflation = await bank.inflation(session, constants)
    if inflation is None or inflation >= constants[R.BANK_TARGET_INFLATION]:
        return 0
    ceiling = int(await bank.circulating(session) * constants[R.WORKS_PRINT_CAP] / PERCENT)
    printed = min(shortfall, ceiling)
    if printed <= 0:
        return 0

    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        Reason.WORKS_PRINT,
        debit=genesis.id,
        credit=(await fund_account(session)).id,
        amount=printed,
        memo={"печать в фонд работ": money_str(printed)},
    )
    await events.record(
        session,
        EventKind.WORKS_FUNDED,
        printed=printed,
        fund=await fund_balance(session),
    )
    return printed


# --- the board: road orders (auto, D-152/D-158) -------------------------------


def road_tariff(constants: Constants) -> int:
    """The payout of one road order, minor units: hours of work at the public rate.

    Labour only: the surface goods are the worker's, and whether the pay
    covers them is what the tariff constant is tuned by on the live game
    (D-065). The tariff must stay below market earnings, or the state order
    would vacuum people off the market.
    """
    return money(constants[R.ROAD_BUILD_HOURS] * constants[R.WORKS_HOUR_RATE])


async def open_road_order(session: AsyncSession, edge: Edge) -> WorkOrder | None:
    return (
        (
            await session.execute(
                select(WorkOrder).where(
                    WorkOrder.kind == WorkOrderKind.ROAD_MEND,
                    WorkOrder.state == WorkOrderState.OPEN,
                    WorkOrder.edge_id == edge.id,
                )
            )
        )
        .scalars()
        .first()
    )


async def _on_cooldown(
    session: AsyncSession, constants: Constants, edge: Edge, moment: datetime
) -> bool:
    """A paid order on this edge within `works.object_cooldown` days.

    Belt and braces against break-and-fix farming: decay alone already keeps
    a freshly mended edge above the threshold for many days.
    """
    since = moment - timedelta(days=constants[R.WORKS_OBJECT_COOLDOWN])
    last = await session.scalar(
        select(WorkOrder.id)
        .where(
            WorkOrder.kind == WorkOrderKind.ROAD_MEND,
            WorkOrder.state == WorkOrderState.DONE,
            WorkOrder.edge_id == edge.id,
            WorkOrder.done_at >= since,
        )
        .limit(1)
    )
    return last is not None


async def _sagged_edges(session: AsyncSession, constants: Constants) -> list[Edge]:
    threshold = constants[R.WORKS_ROAD_THRESHOLD]
    return list(
        (
            await session.execute(
                select(Edge).where(
                    Edge.surface != Surface.TRAIL,
                    Edge.condition < threshold,
                )
            )
        )
        .scalars()
        .all()
    )


async def road_orders_wanted(
    session: AsyncSession, constants: Constants, *, now: datetime
) -> list[Edge]:
    """Edges that deserve an order right now: sagged, not yet posted, off cooldown."""
    wanted = []
    for edge in await _sagged_edges(session, constants):
        if await open_road_order(session, edge) is not None:
            continue
        if await _on_cooldown(session, constants, edge, now):
            continue
        wanted.append(edge)
    return wanted


async def post_road_orders(session: AsyncSession, constants: Constants, *, now: datetime) -> int:
    """Post orders for sagged edges, each with its payout escrowed. Returns the count.

    The fund is debited under the lock `ledger.post` takes, so two processes
    posting at once cannot promise the same coin twice; whichever finds the
    fund short simply stops -- an empty fund posts nothing.
    """
    tariff = road_tariff(constants)
    if tariff <= 0:
        return 0
    posted = 0
    for edge in await road_orders_wanted(session, constants, now=now):
        if await fund_balance(session) < tariff:
            break
        order = WorkOrder(
            kind=WorkOrderKind.ROAD_MEND,
            edge_id=edge.id,
            payload={"surface": edge.surface.value},
            tariff=tariff,
        )
        session.add(order)
        await session.flush()
        try:
            await ledger.transfer(
                session,
                Reason.ESCROW_HOLD,
                debit=(await fund_account(session)).id,
                credit=(await ledger.account_for(session, AccountKind.ESCROW, order.id)).id,
                amount=tariff,
                memo={"госзаказ": str(order.id), "ребро": str(edge.id)},
            )
        except ledger.InsufficientFunds:
            #: Lost the race for the last coins to a parallel poster: the
            #: order is withdrawn, the board stays honest -- and the journal
            #: keeps the trace, a row appearing CANCELLED out of nowhere would
            #: read as a bug.
            order.state = WorkOrderState.CANCELLED
            order.cancelled_at = now
            await events.record(
                session,
                EventKind.WORKS_ORDER_CANCELLED,
                order_id=str(order.id),
                order_kind=order.kind.value,
                returned=0,
            )
            break
        posted += 1
        await events.record(
            session,
            EventKind.WORKS_ORDER_POSTED,
            order_id=str(order.id),
            order_kind=order.kind.value,
            edge_id=str(edge.id),
            tariff=tariff,
        )
    return posted


async def cancel_stale_road_orders(session: AsyncSession, *, now: datetime) -> int:
    """Withdraw orders whose target changed under them. The escrow returns to the fund.

    An edge that decayed a tier is a different project (laying, not mending);
    one somehow back at full condition needs nothing.
    """
    candidates = (
        (
            await session.execute(
                select(WorkOrder.id).where(
                    WorkOrder.kind == WorkOrderKind.ROAD_MEND,
                    WorkOrder.state == WorkOrderState.OPEN,
                )
            )
        )
        .scalars()
        .all()
    )
    cancelled = 0
    for order_id in candidates:
        #: Reread under the lock and check the state again: a payout landing
        #: between the listing and this step turns the order DONE, and a sweep
        #: without the recheck would overwrite DONE with CANCELLED -- wiping
        #: `done_by` and the cooldown with it. Same lock order as the payout:
        #: the order row first, the escrow after.
        order = (
            (
                await session.execute(
                    select(WorkOrder).where(WorkOrder.id == order_id).with_for_update()
                )
            )
            .scalars()
            .one()
        )
        if order.state is not WorkOrderState.OPEN:
            continue
        edge = await session.get(Edge, order.edge_id)
        stale = (
            edge is None
            or edge.surface.value != order.payload.get("surface")
            or float(edge.condition) >= SCALE_MAX
        )
        if not stale:
            continue
        await _withdraw(session, order, now=now)
        cancelled += 1
    return cancelled


async def _withdraw(session: AsyncSession, order: WorkOrder, *, now: datetime) -> None:
    escrow = await ledger.account_for(session, AccountKind.ESCROW, order.id)
    held = await ledger.balance(session, escrow.id)
    if held > 0:
        await ledger.transfer(
            session,
            Reason.ESCROW_RELEASE,
            debit=escrow.id,
            credit=(await fund_account(session)).id,
            amount=held,
            memo={"возврат эскроу": str(order.id)},
        )
    order.state = WorkOrderState.CANCELLED
    order.cancelled_at = now
    await session.flush()
    await events.record(
        session,
        EventKind.WORKS_ORDER_CANCELLED,
        order_id=str(order.id),
        order_kind=order.kind.value,
        returned=held,
    )


async def paid_today(session: AsyncSession, identity_id: uuid.UUID, *, now: datetime) -> int:
    """What the fund paid this identity over the last day -- the cap's counter."""
    account = await ledger.find_account(session, AccountKind.IDENTITY, identity_id)
    if account is None:
        return 0
    total = await session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(LedgerTransaction, LedgerTransaction.id == LedgerEntry.transaction_id)
        .where(
            LedgerEntry.account_id == account.id,
            LedgerEntry.amount > 0,
            LedgerTransaction.reason == Reason.WORKS_PAYOUT,
            LedgerTransaction.at >= now - timedelta(days=1),
        )
    )
    return int(total or 0)


async def pay_road_order(
    session: AsyncSession,
    constants: Constants,
    edge: Edge,
    identity_id: uuid.UUID | None,
    *,
    now: datetime | None = None,
) -> int:
    """Pay the open order on this edge after a verified mend. Returns what was paid.

    Called by the road-work handler once the mend actually landed -- the
    condition is back at full, the engine saw it in its own data. The order
    row is locked first: the state check and the payout must be one step, or
    two completions pay twice.
    """
    moment = now or datetime.now(UTC)
    order = (
        (
            await session.execute(
                select(WorkOrder)
                .where(
                    WorkOrder.kind == WorkOrderKind.ROAD_MEND,
                    WorkOrder.state == WorkOrderState.OPEN,
                    WorkOrder.edge_id == edge.id,
                )
                .with_for_update()
            )
        )
        .scalars()
        .first()
    )
    if order is None or identity_id is None:
        return 0
    if edge.surface.value != order.payload.get("surface"):
        #: The mend landed on a different tier than the order was posted
        #: against: not the work that was bought. The stale sweep will withdraw it.
        return 0
    if float(edge.condition) < SCALE_MAX:
        #: Pay is for the verified delta, and the delta is the condition back
        #: at full -- not for whoever calls this function.
        return 0

    #: The daily cap is a read-then-pay over *all* the worker's orders, and
    #: the order-row lock guards only this one: two mends landing on the same
    #: `run_at` would each read the cap before the other's commit and both
    #: pay in full. The recipient's account row serialises the payouts; the
    #: lock order stays one-way everywhere -- order, then identity, then escrow.
    recipient = await ledger.account_for(session, AccountKind.IDENTITY, identity_id)
    await session.execute(
        select(LedgerAccount.id).where(LedgerAccount.id == recipient.id).with_for_update()
    )
    escrow = await ledger.account_for(session, AccountKind.ESCROW, order.id)
    held = await ledger.balance(session, escrow.id)
    cap = money(constants[R.WORKS_PLAYER_DAILY_CAP])
    allowance = max(0, cap - await paid_today(session, identity_id, now=moment))
    payment = min(held, allowance)

    if payment > 0:
        await ledger.transfer(
            session,
            Reason.WORKS_PAYOUT,
            debit=escrow.id,
            credit=recipient.id,
            amount=payment,
            memo={"госзаказ": str(order.id), "ребро": str(edge.id)},
        )
    leftover = held - payment
    if leftover > 0:
        #: The daily cap clipped the payout: the unclaimable rest is not the
        #: worker's and goes back to the fund, not into limbo.
        await ledger.transfer(
            session,
            Reason.ESCROW_RELEASE,
            debit=escrow.id,
            credit=(await fund_account(session)).id,
            amount=leftover,
            memo={"остаток сверх дневного потолка": str(order.id)},
        )
    order.state = WorkOrderState.DONE
    order.done_by = identity_id
    order.done_at = moment
    await session.flush()
    await events.record(
        session,
        EventKind.WORKS_PAID,
        actor_identity_id=identity_id,
        order_id=str(order.id),
        order_kind=order.kind.value,
        amount=payment,
        clipped=leftover,
    )
    return payment


async def board(session: AsyncSession) -> list[dict]:
    """The open orders through the client's eyes.

    Details ride in `about`: what a road order and a fuel order need to say
    differ, and a fixed set of mostly-null keys would be noise (D-225). The
    money split stays out -- the tariff is what the worker sees promised.
    """
    open_ones = (
        (
            await session.execute(
                select(WorkOrder)
                .where(WorkOrder.state == WorkOrderState.OPEN)
                .order_by(WorkOrder.posted_at)
            )
        )
        .scalars()
        .all()
    )
    #: The board is read by every open bank panel: names come in two bulk
    #: selects, not a query per order (review 2026-08-29).
    edge_ids = [order.edge_id for order in open_ones if order.edge_id is not None]
    edges = (
        {
            edge.id: edge
            for edge in (await session.execute(select(Edge).where(Edge.id.in_(edge_ids)))).scalars()
        }
        if edge_ids
        else {}
    )
    node_ids = {order.node_id for order in open_ones if order.node_id is not None}
    for edge in edges.values():
        node_ids.update((edge.node_a_id, edge.node_b_id))
    nodes = (
        {
            node.id: node
            for node in (await session.execute(select(Node).where(Node.id.in_(node_ids)))).scalars()
        }
        if node_ids
        else {}
    )

    result = []
    for order in open_ones:
        about = {
            key: order.payload[key]
            for key in ("surface", "building_kind", "footprint", "floors", "type_key", "left")
            if key in order.payload
        }
        row = {
            "id": str(order.id),
            "kind": order.kind.value,
            "tariff": order.tariff,
            "posted_at": order.posted_at.isoformat(),
            "about": about,
        }
        if order.edge_id is not None:
            row["edge"] = str(order.edge_id)
            edge = edges.get(order.edge_id)
            if edge is not None:
                ends = (nodes.get(edge.node_a_id), nodes.get(edge.node_b_id))
                row["between"] = [None if end is None else end.name for end in ends]
        if order.node_id is not None:
            place = nodes.get(order.node_id)
            row["node"] = None if place is None else place.key
        result.append(row)
    return result


async def daily(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> dict[str, int]:
    """The fund's daily step: sweep, recycle, print the shortfall, post orders."""
    moment = now or datetime.now(UTC)
    stale = await cancel_stale_road_orders(session, now=moment)
    burned, recycled = await recycle(session, constants, now=moment)
    wanted = await road_orders_wanted(session, constants, now=moment)
    need = len(wanted) * road_tariff(constants)
    printed = await print_into_fund(session, constants, need, now=moment)
    posted = await post_road_orders(session, constants, now=moment)
    return {
        "reserve_burned": burned,
        "fund_recycled": recycled,
        "fund_printed": printed,
        "orders_posted": posted,
        "orders_stale": stale,
    }
