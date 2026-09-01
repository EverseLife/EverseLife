# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The rate and the money it governs (D-087, D-169).

The price sensor feeds inflation, inflation and the emission share move the
lever inside its bounds, the review runs on the journal's clock, and the
surplus of the reserve is sterilised. Seigniorage stays cancelled.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src import i18n
from src.constants import Constants, current
from src.constants import registry as R
from src.engine import events, ledger
from src.engine.bank._base import key_rate
from src.engine.errors import Says
from src.engine.jobs import enqueue, handler
from src.models.bank import Loan, RateDecision
from src.models.event import EventKind
from src.models.job import Job, JobKind, JobState
from src.models.ledger import (
    AccountKind,
    LedgerAccount,
    LedgerEntry,
    LedgerTransaction,
    PostingReason,
)
from src.models.market import Trade
from src.models.metrics import DailyMetric
from src.telemetry.metrics import median
from src.units import PERCENT


def _plus(value: float) -> str:
    """Whether this number is written with a leading `+` (D-251 wave IV).

    A flag rather than a sign: `NUMBER()` cannot be asked to show one --
    Fluent's `signDisplay` is not implemented here -- and "+0,50" is how the
    sentence says which way the lever moved. The engine says which way, the
    language writes the sign.
    """
    return "true" if value >= 0 else "false"


def compute_rate(
    constants: Constants,
    *,
    previous: float,
    inflation: float | None,
    emission_share: float | None,
) -> tuple[float, list[Says]]:
    """The public rate formula. Returns the rate and the reasons behind it.

    The explanation is not decoration: the algorithm must be not only
    deterministic but readable, otherwise there is nothing to argue monetary
    policy with (D-030). Named rather than worded (D-251 wave IV): one message
    per clause, said in the language of whoever is reading. A list stays a
    list all the way to the panel -- it is drawn as one, fact under fact.
    """
    rate_value = constants[R.BANK_BASE_RATE]
    reasons = [Says("bank-why-rate-base", {"rate": rate_value})]

    if inflation is not None:
        goal = constants[R.BANK_TARGET_INFLATION]
        bonus = constants[R.BANK_RATE_REACTION_K] * (inflation - goal)
        rate_value += bonus
        reasons.append(
            Says(
                "bank-why-rate-inflation",
                {
                    "inflation": inflation,
                    "inflation_up": _plus(inflation),
                    "goal": goal,
                    "bonus": bonus,
                    "bonus_up": _plus(bonus),
                },
            )
        )
    else:
        reasons.append(Says("bank-why-rate-inflation-unknown"))

    if emission_share is not None:
        goal = constants[R.BANK_EMISSION_SHARE_TARGET]
        bonus = constants[R.BANK_EMISSION_REACTION_K] * (emission_share - goal)
        rate_value += bonus
        reasons.append(
            Says(
                "bank-why-rate-emission",
                {
                    "share": emission_share,
                    "goal": goal,
                    "bonus": bonus,
                    "bonus_up": _plus(bonus),
                },
            )
        )

    #: The step is bounded: monetary policy does not twitch, otherwise it
    #: cannot be predicted, and prediction is half its point.
    step = constants[R.BANK_RATE_STEP_MAX]
    rate_value = max(previous - step, min(previous + step, rate_value))
    rate_value = max(constants[R.BANK_RATE_FLOOR], min(constants[R.BANK_RATE_CAP], rate_value))
    return rate_value, reasons


async def review_rate(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> RateDecision:
    """Review the rate by sensors. The decision and its reason are stored."""
    moment = now or datetime.now(UTC)
    before = await key_rate(session, constants)
    inflation_ = await inflation(session, constants)
    issue_share = await _emission_share(session, constants, now=moment)
    rate_value, reasons = compute_rate(
        constants,
        previous=before,
        inflation=inflation_,
        emission_share=issue_share,
    )
    why = i18n.written(reasons)
    #: Inflation past the alarm line returns the rate to the algorithm for
    #: `bank.council_lockout` days: a political decision is good exactly until
    #: the price of a mistake is everybody's money (D-172).
    lock = (
        moment + timedelta(days=constants[R.BANK_COUNCIL_LOCKOUT])
        if inflation_ is not None and inflation_ > constants[R.BANK_INFLATION_ALARM]
        else None
    )
    decision = RateDecision(
        rate=rate_value,
        locked_until=lock,
        inflation=inflation_ or 0,
        emission_share=issue_share or 0,
        why_said=why,
        decided_at=moment,
    )
    session.add(decision)
    await session.flush()
    await events.record(
        session,
        EventKind.RATE_DECIDED,
        rate=rate_value,
        was=before,
        #: The journal keeps the reasons the same way the decision does: keys
        #: and numbers, said by whoever reads the event (D-251 wave IV).
        why_said=why,
    )
    return decision


async def circulating(session: AsyncSession) -> int:
    """Circulating supply: money on identity and treasury accounts, without the reserve.

    Prices depend on it, not on the total supply: what lies in the reserve has
    left circulation and waits for the next borrower (D-087).
    """
    result = 0
    for kind in (AccountKind.IDENTITY, AccountKind.CITY_TREASURY, AccountKind.ESCROW):
        accounts = (
            (await session.execute(select(LedgerAccount.id).where(LedgerAccount.kind == kind)))
            .scalars()
            .all()
        )
        for account in accounts:
            result += await ledger.balance(session, account)
    return result


@handler(JobKind.RATE_REVIEW)
async def rate_review(session: AsyncSession, job: Job) -> None:
    """Scheduled rate review: once every `bank.rate_review_period` days."""

    constants = current()
    await review_rate(session, constants, now=job.run_at)
    await schedule_review(session, constants, after=job.run_at)


async def schedule_review(
    session: AsyncSession, constants: Constants, *, after: datetime | None = None
) -> None:
    """Queue the next rate review. Called at process start and after each one.

    Two guards, and both are needed, because this is called on **every** start
    of **every** process. A pending review means the chain is already running,
    and there is nothing to add: without that check each restart and each
    deploy laid down another review, all of them fired, and the chronicle filled
    with rate decisions -- several in one digest, for a rate the vault says
    changes once every `bank.rate_review_period` days.

    And the moment is counted from the **start of the day**, not from the
    second somebody happened to call: two processes of one deploy start seconds
    apart, and an offset added to each of their clocks gives two different
    keys, so the dedup key would let both through.
    """
    moment = after or datetime.now(UTC)
    running = await session.scalar(
        select(Job.id)
        .where(Job.kind == JobKind.RATE_REVIEW.value, Job.state == JobState.PENDING)
        .limit(1)
    )
    if running is not None:
        return
    day = datetime.combine(moment.date(), time.min, tzinfo=UTC)
    term = day + timedelta(days=constants[R.BANK_RATE_REVIEW_PERIOD])
    queued = await enqueue(
        session,
        JobKind.RATE_REVIEW,
        term,
        dedup_key=f"bank.rate:{int(term.timestamp())}",
    )
    #: Refused by the key with nothing pending means the second is held by a
    #: review that has already run: a chain that came back round to a day it
    #: had used. A minute later is a second nobody holds -- swallowing the
    #: refusal would stop monetary policy until somebody restarted the world.
    if queued is None:
        later = term + timedelta(minutes=1)
        await enqueue(
            session,
            JobKind.RATE_REVIEW,
            later,
            dedup_key=f"bank.rate:{int(later.timestamp())}",
        )


async def inflation(session: AsyncSession, constants: Constants) -> float | None:
    """Inflation from daily metrics. No data -- we stay silent rather than invent.

    Public: the works fund reads the same sensor (D-248) -- there must be no
    second copy of the formula.
    """

    window = int(constants[R.BANK_PRICE_INDEX_WINDOW])
    lines = (
        (
            await session.execute(
                select(DailyMetric)
                .where(DailyMetric.key == PRICE_INDEX)
                .order_by(DailyMetric.day.desc())
                .limit(window)
            )
        )
        .scalars()
        .all()
    )
    #: One point is not enough for a change: the sensor is silent until there is something to
    #: compare.
    if len(lines) <= 1:
        return None
    new, old = float(lines[0].value), float(lines[-1].value)
    if old <= 0:
        return None
    return (new - old) / old * PERCENT


async def _emission_share(
    session: AsyncSession, constants: Constants, *, now: datetime
) -> float | None:
    """Share of printed in issued over the window. A fast sensor: visible before prices."""
    window = now - timedelta(days=constants[R.BANK_PRICE_INDEX_WINDOW])
    line = (
        await session.execute(
            select(func.sum(Loan.principal), func.sum(Loan.printed)).where(Loan.taken_at >= window)
        )
    ).one()
    issued_, printed = line[0] or 0, line[1] or 0
    #: What was printed into the works fund (D-248) is emission like any
    #: other: it enters both the printed and the issued side, so the tap
    #: cannot hide from the rate formula.
    works_printed = int(
        await session.scalar(
            select(func.coalesce(func.sum(LedgerEntry.amount), 0))
            .join(LedgerTransaction, LedgerTransaction.id == LedgerEntry.transaction_id)
            .where(
                LedgerEntry.amount > 0,
                LedgerTransaction.reason == PostingReason.WORKS_PRINT,
                LedgerTransaction.at >= window,
            )
        )
        or 0
    )
    issued_all = float(issued_) + works_printed
    if issued_all <= 0:
        return None
    #: Money comes out of the database as `Decimal` and the scale is a float:
    #: multiplying the two raises, and the raise lands in a scheduled job that
    #: retries for ever. The share is a number, not a sum, so it leaves as one.
    return (float(printed) + works_printed) / issued_all * PERCENT


# --- price sensor and sterilisation (D-087, D-169) ---------------------------

#: Measurement name under which the index lands in daily metrics.
PRICE_INDEX = "price_index"


async def price_index(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> float | None:
    """Price index from player deals. Empty -- there were no deals, nothing to measure.

    Median per goods, weighted by its share of turnover: one deal at an absurd
    price must not move monetary policy, and bread matters more than a rare
    alloy exactly as much as more of it is bought (D-087, D-169).
    """

    moment = now or datetime.now(UTC)
    day = timedelta(hours=constants[R.TIME_DAY_TERRA])
    deals = (await session.execute(select(Trade).where(Trade.at >= moment - day))).scalars().all()
    if not deals:
        return None

    by_goods: dict[str, list[int]] = {}
    turnover: dict[str, int] = {}
    for deal in deals:
        by_goods.setdefault(deal.type_key, []).append(deal.price)
        turnover[deal.type_key] = turnover.get(deal.type_key, 0) + deal.price * deal.amount
    total_turnover = sum(turnover.values())
    if total_turnover <= 0:
        return None

    #: The median is the shared one: the same that telemetry computes. There
    #: must be no second copy of the formula -- it would diverge from the first (D-139).

    index = 0.0
    for goods, prices in by_goods.items():
        index += median(prices) * turnover[goods] / total_turnover
    return index


@handler(JobKind.SEIGNIORAGE)
async def seigniorage_cancelled(session: AsyncSession, job: Job) -> None:
    """Seigniorage is cancelled (D-175): the city earns by margin, not by handouts.

    The job kind stays in the enumeration forever -- the journal is eternal --
    and an old job that outlived the mechanic's cancellation closes without effect.
    """

    return
