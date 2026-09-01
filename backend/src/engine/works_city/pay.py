# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The settlement (D-248): the treasury pays the worker's share when the
repair, the building or the delivery is accepted.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import events, ledger, works
from src.engine.estate.building import buildings_of
from src.engine.works_city._base import _EPS, open_city_order
from src.models.event import EventKind
from src.models.ledger import AccountKind, LedgerAccount
from src.models.ledger import PostingReason as Reason
from src.models.works import WorkOrder, WorkOrderKind, WorkOrderState
from src.models.world import Node
from src.units import SCALE_MAX, money

# --- payouts ------------------------------------------------------------------


async def _pay_share(
    session: AsyncSession,
    constants: Constants,
    order: WorkOrder,
    identity_id: uuid.UUID,
    fraction: float,
    *,
    now: datetime,
) -> int:
    """Pay `fraction` of the order to the worker. Returns what reached them.

    The city's share goes in full -- it is the price of goods, not a subsidy.
    The fund's share is clipped by the worker's daily cap; the clipped rest is
    not the worker's and returns to the fund at once. Cumulative sums are kept
    against rounding drift: each step pays `int(part * done) - paid so far`.
    """
    done_before = float(order.payload.get("done", 0.0))
    done_now = min(1.0, done_before + fraction)

    city_part = int(order.payload["city_part"])
    fund_part = int(order.payload["fund_part"])
    city_paid = int(order.payload["city_paid"])
    fund_used = int(order.payload["fund_used"])
    city_due = int(city_part * done_now) - city_paid
    fund_due = int(fund_part * done_now) - fund_used
    if done_now >= 1.0:
        city_due = city_part - city_paid
        fund_due = fund_part - fund_used

    #: The recipient's row serialises the cap read across the worker's orders
    #: -- the same lock, in the same place, as the road payout takes.
    recipient = await ledger.account_for(session, AccountKind.IDENTITY, identity_id)
    await session.execute(
        select(LedgerAccount.id).where(LedgerAccount.id == recipient.id).with_for_update()
    )
    cap = money(constants[R.WORKS_PLAYER_DAILY_CAP])
    allowance = max(0, cap - await works.paid_today(session, identity_id, now=now))
    fund_pay = max(0, min(fund_due, allowance))

    escrow = await ledger.account_for(session, AccountKind.ESCROW, order.id)
    payment = max(0, city_due) + fund_pay
    if payment > 0:
        await ledger.transfer(
            session,
            Reason.WORKS_PAYOUT,
            debit=escrow.id,
            credit=recipient.id,
            amount=payment,
            memo={"госзаказ": str(order.id)},
        )
    clipped = fund_due - fund_pay
    if clipped > 0:
        await ledger.transfer(
            session,
            Reason.ESCROW_RELEASE,
            debit=escrow.id,
            credit=(await works.fund_account(session)).id,
            amount=clipped,
            memo={"остаток сверх дневного потолка": str(order.id)},
        )
    order.payload = {
        **order.payload,
        "done": done_now,
        "city_paid": city_paid + max(0, city_due),
        "fund_used": fund_used + fund_due,
    }
    await session.flush()
    await events.record(
        session,
        EventKind.WORKS_PAID,
        actor_identity_id=identity_id,
        order_id=str(order.id),
        order_kind=order.kind.value,
        amount=payment,
        clipped=clipped,
    )
    return payment


async def _finish(session: AsyncSession, order: WorkOrder, identity_id: uuid.UUID, now: datetime):
    order.state = WorkOrderState.DONE
    order.done_by = identity_id
    order.done_at = now
    await session.flush()


async def pay_repair_order(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    identity_id: uuid.UUID | None,
    *,
    now: datetime | None = None,
) -> int:
    """Pay the open repair order after the engine saw the houses whole again."""
    moment = now or datetime.now(UTC)
    order = await open_city_order(session, WorkOrderKind.BUILDING_REPAIR, node, for_update=True)
    if order is None or identity_id is None:
        return 0
    houses = await buildings_of(session, node)
    if not houses or any(float(house.condition) < SCALE_MAX for house in houses):
        return 0
    paid = await _pay_share(session, constants, order, identity_id, 1.0, now=moment)
    await _finish(session, order, identity_id, moment)
    return paid


async def pay_build_order(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    building,
    identity_id: uuid.UUID | None,
    *,
    now: datetime | None = None,
) -> int:
    """Pay the open construction order if the finished house is the one ordered."""
    moment = now or datetime.now(UTC)
    order = await open_city_order(session, WorkOrderKind.BUILDING_BUILD, node, for_update=True)
    if order is None or identity_id is None:
        return 0
    wanted = order.payload
    fits = (
        building.kind == wanted.get("building_kind")
        and building.floors == int(wanted.get("floors", 0))
        and float(building.footprint_m2) >= float(wanted.get("footprint", 0))
    )
    if not fits:
        #: A different house went up on the ordered plot: not the work that
        #: was bought. The order stays for the one that was.
        return 0
    paid = await _pay_share(session, constants, order, identity_id, 1.0, now=moment)
    await _finish(session, order, identity_id, moment)
    return paid


async def pay_fuel_delivery(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    type_key: str,
    poured: float,
    identity_id: uuid.UUID | None,
    *,
    now: datetime | None = None,
) -> int:
    """Pay per unit landed in the station. The order closes when filled."""
    moment = now or datetime.now(UTC)
    order = await open_city_order(session, WorkOrderKind.FUEL_DELIVERY, node, for_update=True)
    if order is None or identity_id is None:
        return 0
    if order.payload.get("type_key") != type_key:
        return 0
    left = float(order.payload.get("left", 0.0))
    take = min(poured, left)
    if take <= 0:
        return 0
    total = float(order.payload["amount"])
    #: The pour that empties the order pays the whole remainder, not its
    #: float-drifted fraction: three thirds must sum to one, and a tail of
    #: minor units must not stay on the escrow of a DONE order for ever.
    closing = (left - take) <= _EPS
    fraction = 1.0 if closing else take / total
    paid = await _pay_share(session, constants, order, identity_id, fraction, now=moment)
    order.payload = {**order.payload, "left": 0.0 if closing else left - take}
    await session.flush()
    if closing:
        await _finish(session, order, identity_id, moment)
    return paid
