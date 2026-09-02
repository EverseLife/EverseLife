# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Trust as an asset (D-173): personal turnover, the interest paid on loans
carried, and the reports that cut trust without burying anybody.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import events
from src.engine.bank._base import BankError
from src.models.bank import DefectReport, Loan, LoanState
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.market import Order, Trade
from src.units import PERCENT, amount_float

# --- credit limit from labour (D-173) ----------------------------------------


async def trust(session: AsyncSession, constants: Constants, identity_id: uuid.UUID) -> float:
    """Trust 0..1: each "defective print" report cuts it by
    `credit.report_penalty`, but not below `credit.trust_floor`.

    Reports lower credit, they do not bury the person: only out-of-game support
    does the irreversible (D-173).
    """
    report_count = await session.scalar(
        select(func.count())
        .select_from(DefectReport)
        .where(DefectReport.target_identity_id == identity_id)
    )
    share = (PERCENT - constants[R.CREDIT_REPORT_PENALTY] * int(report_count or 0)) / PERCENT
    return max(constants[R.CREDIT_TRUST_FLOOR] / PERCENT, share)


async def city_trust(
    session: AsyncSession,
    constants: Constants,
    city_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> float:
    """A city's trust 0..1, and it is its own payment record (D-285).

    A citizen's trust is cut by complaints about a defective print; nobody
    complains about a city, and the thing a lender wants to know about one is
    the same thing anyway -- how it pays. So the input is the city's **unpaid**
    overdue: each such loan cuts trust by `credit.city_overdue_penalty` and it
    stops at `credit.city_trust_floor`. The penalty is the harsher of the two
    on purpose (D-285): a complaint about a person is somebody else's word and
    can be withdrawn, an overdue is the city's own doing and cannot.

    Counted from what is owed **now**, not from a history of episodes -- which
    is what makes "recovers by paying" true without anything having to forget:
    the loan is settled, the row leaves the count, the trust is back.
    """
    moment = now or datetime.now(UTC)
    since = moment - timedelta(days=constants[R.DEBT_GRACE_PERIOD])
    late = await session.scalar(
        select(func.count())
        .select_from(Loan)
        .where(
            Loan.city_id == city_id,
            Loan.identity_id.is_(None),
            Loan.state == LoanState.OPEN,
            Loan.serviced_at < since,
        )
    )
    share = (PERCENT - constants[R.CREDIT_CITY_OVERDUE_PENALTY] * int(late or 0)) / PERCENT
    return max(constants[R.CREDIT_CITY_TRUST_FLOOR] / PERCENT, share)


async def personal_turnover(
    session: AsyncSession,
    constants: Constants,
    identity_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> int:
    """The identity's sales turnover over `credit.window`, in minor units.

    Turnover cannot be faked without selling real goods to a real buyer: that
    is why the limit is computed from it, not from time in game (D-173).
    """

    moment = now or datetime.now(UTC)
    window = moment - timedelta(days=constants[R.CREDIT_WINDOW])
    deals = (
        (
            await session.execute(
                select(Trade)
                .join(Order, Order.id == Trade.sell_order_id)
                .where(Order.identity_id == identity_id, Trade.at >= window)
            )
        )
        .scalars()
        .all()
    )
    return sum(int(deal.price * amount_float(deal.amount)) for deal in deals)


async def interest_paid_total(session: AsyncSession, identity_id: uuid.UUID) -> int:
    """Interest paid on this identity's loans, in minor units.

    Credit history is an asset, but the asset is **servicing**, not the round
    trip (D-280 revises D-173): the sum of repaid principal grew on a loan taken
    and given back the same second -- interest for zero time is zero -- and the
    limit lifted itself by its own bootstraps, cycle after cycle, at no cost.
    Interest cannot be run up without carrying the debt through real time and
    paying real money for it.

    **Paid by whoever paid it.** A third party settling for the debtor (D-063),
    a city bailing out its own (D-175), the withholding from an overdue debt
    (D-168) and the treasury paying for a prisoner (D-174) all count: the money
    left somebody's account either way. Discipline lives in the record
    multiplier, not here.

    Open loans count too: money leaves the payer when the payment is made, not
    when the loan closes.
    """
    result = await session.scalar(
        select(func.coalesce(func.sum(Loan.interest_paid), 0)).where(
            Loan.identity_id == identity_id
        )
    )
    return int(result or 0)


async def report_defect(
    session: AsyncSession, reporter: Identity, target: Identity
) -> DefectReport:
    """Point at a defective print. One report per identity per identity."""
    if reporter.id == target.id:
        raise BankError(key="bank-complain-about-self")
    exists = (
        await session.execute(
            select(DefectReport).where(
                DefectReport.reporter_identity_id == reporter.id,
                DefectReport.target_identity_id == target.id,
            )
        )
    ).scalar_one_or_none()
    if exists is not None:
        return exists
    report = DefectReport(reporter_identity_id=reporter.id, target_identity_id=target.id)
    session.add(report)
    await session.flush()
    await events.record(
        session,
        EventKind.REPORT_FILED,
        actor_identity_id=reporter.id,
        target=target.name,
    )
    return report


async def withdraw_report(session: AsyncSession, reporter: Identity, target: Identity) -> bool:
    """Withdraw your report: one may err, and one must be able to correct it."""
    report = (
        await session.execute(
            select(DefectReport).where(
                DefectReport.reporter_identity_id == reporter.id,
                DefectReport.target_identity_id == target.id,
            )
        )
    ).scalar_one_or_none()
    if report is None:
        return False
    await session.delete(report)
    await session.flush()
    await events.record(
        session,
        EventKind.REPORT_WITHDRAWN,
        actor_identity_id=reporter.id,
        target=target.name,
    )
    return True
