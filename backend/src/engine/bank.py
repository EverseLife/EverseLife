# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Bank: reserve, credit, key rate (D-030, D-087, D-167).

Until now the only source of money was `genesis`, so any issue would have been
pure emission, and "monetary policy" a word.

## The reserve sterilises, it does not hoard

Issuing a loan, the system takes TC from the **reserve** -- already existing
money collected as interest. What is missing it prints through `genesis`.
Repayment and interest return TC **to the reserve**, not into circulation.
Hence the invariant the engine keeps and checks:

    total TC supply = money on accounts + system reserve

Prices depend not on the total supply but on the **circulating** one -- what
is on accounts.

## The key rate is computed by formula, not decided

    rate = bank.base_rate
         + bank.rate_reaction_k     * (inflation - bank.target_inflation)
         + bank.emission_reaction_k * (emission share - bank.emission_share_target)

with floor `bank.rate_floor`, ceiling `bank.rate_cap` and a step of at most
`bank.rate_step_max` per review. The algorithm is public and deterministic: the
same inputs give the same answer, otherwise the bank turns into a hidden NPC
with a will of its own (D-030). **A silent sensor is no reason to move the
lever:** no inflation data -- no reaction to it.

## A loan is a contract

The borrower's rate is fixed at issue and does not change afterwards, whatever
the bank decides later. There is no collateral (D-173): the limit is granted
by **labour** -- sales turnover, repaid loans, a record without overdue payments
and trust -- and it is computed by a public formula, like the rate.

## The bank is two-tier (D-175)

Only the capital prints money. A citizen borrows **from their city** at
"key + city margin" (code-law `bank_margin`, ceiling `bank.city_margin_cap`);
each such loan sits on the city's credit line with the capital --
`bank.debt_to_turnover_cap` of its turnover. Line exhausted or no citizenship
-- a direct loan from the capital at the worse rate: there is always a way out,
but cheap credit is a privilege of citizenship (D-160).

The margin from each interest payment goes to the city treasury, the key part
to the capital's reserve. So the city earns on its borrowers and answers for
them with its line: seigniorage (D-171) is cancelled as unnecessary.

## What is not here

Deposit interest -- that is income without labour, i.e. emission around pillar
P1 (D-087). And processing for reports: a "defective print" report lowers trust
and cuts the limit but does not kill -- only out-of-game support does the
irreversible.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current
from src.constants import registry as R
from src.engine import city as town
from src.engine import events, ledger
from src.engine.errors import Refusal
from src.engine.jobs import enqueue, handler
from src.engine.world import node_container
from src.models.bank import DefectReport, Loan, LoanState, RateDecision
from src.models.city import City, Power
from src.models.event import Event, EventKind
from src.models.identity import Identity
from src.models.inventory import Item
from src.models.job import Job, JobKind
from src.models.ledger import AccountKind, LedgerAccount, PostingReason
from src.models.market import Order, Trade
from src.models.metrics import DailyMetric
from src.models.world import Node
from src.telemetry.metrics import median
from src.units import MONEY_SCALE, PERCENT, amount_float, money, money_str

#: Owner of the reserve account. One reserve per world: the bank is a single
#: system, not a set of enterprises (D-030, D-031).
RESERVE = uuid.UUID("00000000-0000-0000-0000-00000000ba17")


class BankError(Refusal):
    pass


class TooMuch(BankError):
    """That much is not given: without collateral there is a limit, with it -- the collateral
    norm."""


class NothingToRepay(BankError):
    pass


async def reserve_account(session: AsyncSession) -> LedgerAccount:
    """The system reserve account. Created on first need."""
    return await ledger.account_for(session, AccountKind.BANK_RESERVE, RESERVE)


async def reserve(session: AsyncSession) -> int:
    return await ledger.balance(session, (await reserve_account(session)).id)


async def key_rate(session: AsyncSession, constants: Constants) -> float:
    """The key rate in force: the latest decision or the base one."""
    decision = (
        (
            await session.execute(
                select(RateDecision).order_by(RateDecision.decided_at.desc()).limit(1)
            )
        )
        .scalars()
        .first()
    )
    return float(decision.rate) if decision is not None else constants[R.BANK_BASE_RATE]


def compute_rate(
    constants: Constants,
    *,
    previous: float,
    inflation: float | None,
    emission_share: float | None,
) -> tuple[float, str]:
    """The public rate formula. Returns the rate and an explanation in words.

    The explanation is not decoration: the algorithm must be not only
    deterministic but readable, otherwise there is nothing to argue monetary
    policy with (D-030).
    """
    rate_value = constants[R.BANK_BASE_RATE]
    reasons = [f"база {rate_value:g}"]

    if inflation is not None:
        goal = constants[R.BANK_TARGET_INFLATION]
        bonus = constants[R.BANK_RATE_REACTION_K] * (inflation - goal)
        rate_value += bonus
        reasons.append(f"инфляция {inflation:+.1f} против цели {goal:g} → {bonus:+.2f}")
    else:
        reasons.append("инфляция не измерена: реакции нет")

    if emission_share is not None:
        goal = constants[R.BANK_EMISSION_SHARE_TARGET]
        bonus = constants[R.BANK_EMISSION_REACTION_K] * (emission_share - goal)
        rate_value += bonus
        reasons.append(f"эмиссия {emission_share:.0f}% против цели {goal:g} → {bonus:+.2f}")

    #: The step is bounded: monetary policy does not twitch, otherwise it
    #: cannot be predicted, and prediction is half its point.
    step = constants[R.BANK_RATE_STEP_MAX]
    rate_value = max(previous - step, min(previous + step, rate_value))
    rate_value = max(constants[R.BANK_RATE_FLOOR], min(constants[R.BANK_RATE_CAP], rate_value))
    return rate_value, "; ".join(reasons)


async def review_rate(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> RateDecision:
    """Review the rate by sensors. The decision and its reason are stored."""
    moment = now or datetime.now(UTC)
    before = await key_rate(session, constants)
    inflation = await _inflation(session, constants)
    issue_share = await _emission_share(session, constants, now=moment)
    rate_value, reason = compute_rate(
        constants,
        previous=before,
        inflation=inflation,
        emission_share=issue_share,
    )
    #: Inflation past the alarm line returns the rate to the algorithm for
    #: `bank.council_lockout` days: a political decision is good exactly until
    #: the price of a mistake is everybody's money (D-172).
    lock = (
        moment + timedelta(days=constants[R.BANK_COUNCIL_LOCKOUT])
        if inflation is not None and inflation > constants[R.BANK_INFLATION_ALARM]
        else None
    )
    decision = RateDecision(
        rate=rate_value,
        locked_until=lock,
        inflation=inflation or 0,
        emission_share=issue_share or 0,
        why=reason,
        decided_at=moment,
    )
    session.add(decision)
    await session.flush()
    await events.record(
        session,
        EventKind.RATE_DECIDED,
        rate=rate_value,
        was=before,
        why=reason,
    )
    return decision


async def borrow(
    session: AsyncSession,
    constants: Constants,
    catalog,
    who: Identity,
    amount: float,
    *,
    now: datetime | None = None,
) -> Loan:
    """Take a loan. Money comes from the reserve; the shortfall is printed (D-087).

    The loan goes through the city of citizenship (D-175): the rate is key plus
    city margin, and the loan takes up the city's credit line with the capital.
    No citizenship or line exhausted -- a direct loan from the capital at the
    worse rate.
    """

    moment = now or datetime.now(UTC)
    total = money(amount)
    if total <= 0:
        raise BankError("заём должен быть положительным")

    limit_, reason = await credit_limit(session, constants, who.id, now=moment)
    available = limit_ - await debt_of(session, who.id)
    if total > available:
        raise TooMuch(
            f"столько не дают: доступно {money_str(max(0, available))} ₭ "
            f"из лимита {money_str(limit_)} ₭ ({reason})"
        )

    #: The city of citizenship and its line. The line shrinks smoothly: exactly
    #: the remainder is available, and a "take everything before the cutoff"
    #: run hits arithmetic.
    city = None
    margin = 0.0
    entry = await town.citizenship(session, who.id)
    if entry is not None:
        candidate = await town.by_id(session, entry.city_id)
        if candidate is not None:
            _, _, free = await city_line(session, constants, candidate, now=moment)
            if total <= free:
                city = candidate
                margin = city_margin(constants, catalog, candidate)

    if city is not None:
        rate_value = await key_rate(session, constants) + margin
    else:
        #: A direct loan from the capital: the way out for non-citizens and
        #: residents of cut-off cities, but at the top of the risk range (D-175).
        rate_value = await key_rate(session, constants) + constants[R.BANK_RISK_PREMIUM].max

    #: The reserve is a steriliser: first we spend already existing TC, and
    #: print only the shortfall. Printing shows as a separate posting and in telemetry.
    reserve_treasury = await reserve_account(session)
    have = await ledger.balance(session, reserve_treasury.id)
    printed = max(0, total - have)
    if printed > 0:
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=reserve_treasury.id,
            amount=printed,
            memo={"печать под кредит": who.name},
        )

    account = await ledger.account_for(session, AccountKind.IDENTITY, who.id)
    await ledger.transfer(
        session,
        PostingReason.LOAN,
        debit=reserve_treasury.id,
        credit=account.id,
        amount=total,
        memo={"кредит": who.name},
    )

    loan = Loan(
        identity_id=who.id,
        principal=total,
        outstanding=total,
        rate=rate_value,
        city_id=None if city is None else city.id,
        margin=margin,
        printed=printed,
        taken_at=moment,
        accrued_at=moment,
        serviced_at=moment,
    )
    session.add(loan)
    await session.flush()
    await events.record(
        session,
        EventKind.LOAN_TAKEN,
        actor_identity_id=who.id,
        loan_id=str(loan.id),
        amount=total,
        rate=rate_value,
        printed=printed,
        city=None if city is None else city.name,
        margin=margin,
    )
    return loan


def accruable(constants: Constants, loan: Loan, *, now: datetime | None = None) -> int:
    """Interest run up since the last accrual, without writing it: what a
    view shows as owed right now. `accrue` writes it on repayment and on the
    daily collection; a read must not (review 2026-08-23)."""
    moment = now or datetime.now(UTC)
    if loan.state is not LoanState.OPEN:
        return 0
    elapsed = (moment - loan.accrued_at).total_seconds() / timedelta(days=1).total_seconds()
    if elapsed <= 0:
        return 0
    per_day = float(loan.rate) / PERCENT / constants[R.BANK_YEAR_DAYS]
    return int(loan.outstanding * per_day * elapsed)


async def accrue(
    session: AsyncSession, constants: Constants, loan: Loan, *, now: datetime | None = None
) -> int:
    """Accrue interest for the past day. Returns the accrued amount.

    There is no year in the world -- Terra's day is thirty hours -- so the
    accounting year is set by the vault (`bank.year_days`, D-167): a banking
    number, not an astronomical one.
    """
    moment = now or datetime.now(UTC)
    accrued = accruable(constants, loan, now=moment)
    if accrued <= 0:
        return 0
    loan.outstanding += accrued
    loan.interest_accrued += accrued
    loan.accrued_at = moment
    await session.flush()
    return accrued


async def repay(
    session: AsyncSession,
    constants: Constants,
    who: Identity,
    loan: Loan,
    amount: float | None = None,
    *,
    from_account=None,
    now: datetime | None = None,
) -> int:
    """Repay debt. Money goes **to the reserve**, not into circulation (D-087).

    **Anyone** may pay (D-063, D-168): a third party may settle for the debtor
    -- and a city from its treasury too (`from_account`). The engine does not
    ask why: money accepted, debt reduced.
    """
    moment = now or datetime.now(UTC)
    if loan.state is not LoanState.OPEN:
        raise NothingToRepay("этот заём уже закрыт")
    await accrue(session, constants, loan, now=moment)

    account = from_account or await ledger.account_for(session, AccountKind.IDENTITY, who.id)
    have = await ledger.balance(session, account.id)
    wants = loan.outstanding if amount is None else money(amount)
    payment = min(wants, loan.outstanding, have)
    if payment <= 0:
        raise NothingToRepay("платить нечем")

    await _settle(session, loan, account, payment)
    loan.serviced_at = moment
    if loan.outstanding <= 0:
        loan.state = LoanState.REPAID
        loan.repaid_at = moment
    await session.flush()
    await events.record(
        session,
        EventKind.LOAN_REPAID,
        actor_identity_id=who.id,
        loan_id=str(loan.id),
        amount=payment,
        left=loan.outstanding,
        closed=loan.state is LoanState.REPAID,
    )
    return payment


async def _settle(session: AsyncSession, loan: Loan, account, payment: int) -> None:
    """Post a payment: interest ahead of principal, city margin to its treasury.

    The usual banking order, and it also makes "system income" measurable
    (D-171): without separate accounting the city margin cannot be separated
    from the key part that is sterilised in the capital's reserve (D-175).
    """

    interest = min(payment, max(0, loan.interest_accrued - loan.interest_paid))
    city_margin = 0
    if interest > 0 and loan.city_id is not None and float(loan.rate) > 0:
        city_margin = int(interest * float(loan.margin) / float(loan.rate))
    city = None if loan.city_id is None else await town.by_id(session, loan.city_id)

    if city_margin > 0 and city is not None:
        await ledger.transfer(
            session,
            PostingReason.BANK_MARGIN,
            debit=account.id,
            credit=(await town.treasury(session, city)).id,
            amount=city_margin,
            memo={"маржа города": city.name, "заём": str(loan.id)},
        )
    to_reserve = payment - city_margin
    if to_reserve > 0:
        await ledger.transfer(
            session,
            PostingReason.LOAN_REPAYMENT,
            debit=account.id,
            credit=(await reserve_account(session)).id,
            amount=to_reserve,
            memo={"погашение": str(loan.id)},
        )
    loan.interest_paid += interest
    loan.outstanding -= payment


async def loans_of(session: AsyncSession, identity_id: uuid.UUID) -> list[Loan]:
    return list(
        (
            await session.execute(
                select(Loan).where(Loan.identity_id == identity_id, Loan.state == LoanState.OPEN)
            )
        )
        .scalars()
        .all()
    )


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
    moment = after or datetime.now(UTC)
    term = moment + timedelta(days=constants[R.BANK_RATE_REVIEW_PERIOD])
    await enqueue(
        session,
        JobKind.RATE_REVIEW,
        term,
        dedup_key=f"bank.rate:{int(term.timestamp())}",
    )


async def _inflation(session: AsyncSession, constants: Constants) -> float | None:
    """Inflation from daily metrics. No data -- we stay silent rather than invent."""

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
    if issued_ <= 0:
        return None
    #: Money comes out of the database as `Decimal` and the scale is a float:
    #: multiplying the two raises, and the raise lands in a scheduled job that
    #: retries for ever. The share is a number, not a sum, so it leaves as one.
    return float(printed) / float(issued_) * PERCENT


# --- insolvency (D-063, D-168) -----------------------------------------------


class Restrained(BankError):
    """Debt holds in the node: this is world physics, not a city verdict."""


def overdue_days(loan: Loan, now: datetime) -> float:
    """How many days the loan went unpaid. Overdue means non-payment, not age."""
    return (now - loan.serviced_at).total_seconds() / timedelta(days=1).total_seconds()


def overdue(constants: Constants, loan: Loan, now: datetime) -> bool:
    return overdue_days(loan, now) > constants[R.DEBT_GRACE_PERIOD]


async def debt_of(session: AsyncSession, identity_id: uuid.UUID) -> int:
    """The identity's whole outstanding debt, in minor units."""
    return sum(loan.outstanding for loan in await loans_of(session, identity_id))


async def restrained(
    session: AsyncSession,
    constants: Constants,
    identity_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> Loan | None:
    """Whether debt holds this person in the node (D-063).

    Two conditions at once: the debt exceeds everything on the account, and it
    has not been serviced for longer than `debt.prison_threshold`. One is not
    enough: a loan a person honestly repays does not take freedom, and poverty
    by itself is not a crime.
    """
    moment = now or datetime.now(UTC)
    loans = await loans_of(session, identity_id)
    if not loans:
        return None
    debt = sum(loan.outstanding for loan in loans)
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity_id)
    if debt <= await ledger.balance(session, account.id):
        return None
    threshold = constants[R.DEBT_PRISON_THRESHOLD]
    overdue = [loan for loan in loans if overdue_days(loan, moment) > threshold]
    if not overdue:
        return None
    return max(overdue, key=lambda loan: overdue_days(loan, moment))


async def collect(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> int:
    """Forced withholding from overdue debts. Returns what was withheld.

    The vault says "percent of income", but income as a measurable quantity
    does not exist: money comes to the account from sales, from the treasury
    and from gifts, and they cannot be separated. Withholding from the balance
    is the nearest honest approximation, and the behaviour is the same: a
    working debtor's debt melts, an idle one's does not (D-168).
    """
    moment = now or datetime.now(UTC)
    share = constants[R.DEBT_WORKOFF_RATE] / PERCENT
    withheld = 0

    loans = (
        (await session.execute(select(Loan).where(Loan.state == LoanState.OPEN))).scalars().all()
    )
    for loan in loans:
        if not overdue(constants, loan, moment):
            continue
        await accrue(session, constants, loan, now=moment)
        account = await ledger.account_for(session, AccountKind.IDENTITY, loan.identity_id)
        have = await ledger.balance(session, account.id)
        payment = min(int(have * share), loan.outstanding)
        if payment <= 0:
            continue

        await _settle(session, loan, account, payment)
        #: Withholding is not a payment by the debtor: it does not reset the
        #: overdue, otherwise the insolvent would hang in the grace period forever.
        if loan.outstanding <= 0:
            loan.state = LoanState.REPAID
            loan.repaid_at = moment
        withheld += payment
        await events.record(
            session,
            EventKind.DEBT_WITHHELD,
            actor_identity_id=loan.identity_id,
            loan_id=str(loan.id),
            amount=payment,
            left=loan.outstanding,
        )
    await session.flush()
    return withheld


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


async def sterilize(session: AsyncSession, constants: Constants) -> int:
    """Burn the reserve surplus above `bank.reserve_cap` of circulation (D-169).

    The ceiling is a share of the circulating supply, not an absolute sum: the
    world grows, and what is a huge reserve today is pocket change in a hundred days.
    """
    in_reserve = await reserve(session)
    in_circulation = await circulating(session)
    ceiling = int(in_circulation * constants[R.BANK_RESERVE_CAP] / PERCENT)
    surplus = in_reserve - ceiling
    if surplus <= 0:
        return 0

    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        PostingReason.GENESIS,
        debit=(await reserve_account(session)).id,
        credit=genesis.id,
        amount=surplus,
        memo={"сжигание излишка резерва": money_str(surplus)},
    )
    await events.record(
        session,
        EventKind.RESERVE_BURNED,
        amount=surplus,
        reserve=in_reserve - surplus,
        circulating=in_circulation,
    )
    return surplus


# --- city credit line (D-175) ------------------------------------------------


async def _turnover_by_city(session: AsyncSession, since: datetime) -> dict[uuid.UUID, int]:
    """City turnover for the period: by deals on their territory.

    Turnover is the one quantity that cannot be faked without making real deals
    with real goods (D-171).
    """

    by_city: dict[uuid.UUID, int] = {}
    whose: dict[uuid.UUID, uuid.UUID | None] = {}

    async def city_of(node_id: uuid.UUID | None) -> uuid.UUID | None:
        if node_id is None:
            return None
        if node_id not in whose:
            node = await session.get(Node, node_id)
            city = None if node is None else await town.of_node(session, node)
            whose[node_id] = None if city is None else city.id
        return whose[node_id]

    deals = (await session.execute(select(Trade).where(Trade.at >= since))).scalars().all()
    for deal in deals:
        city_id = await city_of(deal.node_id)
        if city_id is None:
            continue
        by_city[city_id] = by_city.get(city_id, 0) + int(deal.price * amount_float(deal.amount))

    #: Land is turnover too (D-193): buying a plot from the city and selling a
    #: deed between people are real money for real property, and for the city's
    #: line they count the same as a stall on the market.
    land = (
        (
            await session.execute(
                select(Event).where(
                    Event.at >= since,
                    Event.kind.in_((EventKind.LAND_BOUGHT.value, EventKind.DEED_SOLD.value)),
                )
            )
        )
        .scalars()
        .all()
    )
    for record in land:
        city_id = await city_of(record.node_id)
        if city_id is None:
            continue
        paid = record.payload.get("price") or record.payload.get("paid") or 0
        by_city[city_id] = by_city.get(city_id, 0) + int(paid)
    return by_city


# --- Council of cities and the rate (D-087, D-172) ---------------------------


class NotCouncilTime(BankError):
    """The algorithm decides the rate: either few cities, or a lockout is in force."""


class OutOfCorridor(BankError):
    """The council argues with the algorithm rather than replacing it: there is a corridor."""


async def cities_with_hall(session: AsyncSession) -> int:
    """How many cities **with an administration** are on the planet.

    A city without a town hall is not an organ of power but a dot on the map:
    counting it when handing over the rate would give money to signboards (D-172).
    """

    cities = (await session.execute(select(City))).scalars().all()
    qty = 0
    for city in cities:
        node = await session.get(Node, city.node_id)
        if node is None:  # pragma: no cover -- a city without a node is a bug
            continue
        for own in (node, *await _children(session, node)):
            if await _has_hall(session, town, own):
                qty += 1
                break
    return qty


async def _children(session: AsyncSession, node) -> list:

    return list(
        (await session.execute(select(Node).where(Node.parent_id == node.id))).scalars().all()
    )


async def _has_hall(session: AsyncSession, town, node) -> bool:

    yard = await node_container(session, node)
    names = (
        (
            await session.execute(
                select(Item.type_key).where(Item.container_id == yard.id).distinct()
            )
        )
        .scalars()
        .all()
    )
    return town.HALL in names


async def locked_until(session: AsyncSession) -> datetime | None:
    """Until when the rate is returned to the algorithm in emergency (D-172)."""
    decision = (
        (
            await session.execute(
                select(RateDecision).order_by(RateDecision.decided_at.desc()).limit(1)
            )
        )
        .scalars()
        .first()
    )
    if decision is None or not decision.locked_until:
        return None
    return decision.locked_until


async def council_decides(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> bool:
    """Whether the Council of cities decides the rate right now."""
    moment = now or datetime.now(UTC)
    until = await locked_until(session)
    if until is not None and until > moment:
        return False
    threshold = constants[R.BANK_COUNCIL_HANDOVER_CITIES]
    return await cities_with_hall(session) >= threshold


async def council_set_rate(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity,
    rate: float,
    *,
    now: datetime | None = None,
) -> RateDecision:
    """The Council's rate decision. A city casts the vote, not a person (D-172).

    One city -- one vote: an assembly of cities is not a shareholders' meeting,
    and the capital with its head start must not lock in control forever. Here
    the decision itself is executed; how it is reached is the Council's business.
    """

    moment = now or datetime.now(UTC)
    if not await council_decides(session, constants, now=moment):
        raise NotCouncilTime(
            "ставку решает алгоритм: городов с администрацией меньше "
            f"{constants[R.BANK_COUNCIL_HANDOVER_CITIES]:g} либо действует блокировка"
        )
    #: The rate is a matter of law, not of the treasury.
    await town.require(session, by.id, city, Power.LAWS)

    recommendation, reason = compute_rate(
        constants,
        previous=await key_rate(session, constants),
        inflation=await _inflation(session, constants),
        emission_share=await _emission_share(session, constants, now=moment),
    )
    corridor = constants[R.BANK_COUNCIL_RATE_DEVIATION]
    if abs(rate - recommendation) > corridor:
        raise OutOfCorridor(
            f"алгоритм рекомендует {recommendation:.2f}%, отклониться можно на "
            f"{corridor:g} п.п. — просят {rate:.2f}%"
        )
    rate_value = max(constants[R.BANK_RATE_FLOOR], min(constants[R.BANK_RATE_CAP], rate))

    decision = RateDecision(
        rate=rate_value,
        why=(
            f"решение Совета городов ({city.name}); "
            f"алгоритм советовал {recommendation:.2f}: {reason}"
        ),
        decided_at=moment,
    )
    session.add(decision)
    await session.flush()
    await events.record(
        session,
        EventKind.RATE_DECIDED,
        rate=rate_value,
        advised=recommendation,
        by_council=True,
        city=city.name,
    )
    return decision


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


async def repaid_total(session: AsyncSession, identity_id: uuid.UUID) -> int:
    """Sum of previously repaid loans: credit history is an asset (D-173)."""
    result = await session.scalar(
        select(func.coalesce(func.sum(Loan.principal), 0)).where(
            Loan.identity_id == identity_id, Loan.state == LoanState.REPAID
        )
    )
    return int(result or 0)


async def credit_limit(
    session: AsyncSession,
    constants: Constants,
    identity_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> tuple[int, str]:
    """Credit limit and an explanation in words -- public, like the rate (D-030).

    Base from the vault, plus a share of sales turnover, plus a share of what
    was repaid, times trust and record. Labour, not the calendar: time in game
    is the cheapest thing to farm (D-173).
    """
    moment = now or datetime.now(UTC)
    base_ = money(constants[R.BANK_UNSECURED_LIMIT])
    turnover = await personal_turnover(session, constants, identity_id, now=moment)
    returned_ = await repaid_total(session, identity_id)
    limit_ = (
        base_
        + int(turnover * constants[R.CREDIT_TURNOVER_SHARE] / PERCENT)
        + int(returned_ * constants[R.CREDIT_REPAID_SHARE] / PERCENT)
    )
    reasons = [
        f"база {money_str(base_)}",
        f"оборот {money_str(turnover)} за {constants[R.CREDIT_WINDOW]:g} суток",
        f"возвращено ранее {money_str(returned_)}",
    ]

    #: The record is a multiplier, not a base: a bonus for a history without overdue.
    loans = await loans_of(session, identity_id)
    no_overdue = not any(overdue(constants, loan, moment) for loan in loans)
    if returned_ > 0 and no_overdue:
        limit_ = int(limit_ * (1 + constants[R.CREDIT_NO_OVERDUE_BONUS] / PERCENT))
        reasons.append("стаж без просрочек")

    faith = await trust(session, constants, identity_id)
    if faith < 1:
        limit_ = int(limit_ * faith)
        reasons.append(f"доверие {faith * PERCENT:.0f}% по репортам")
    return limit_, "; ".join(reasons)


async def report_defect(
    session: AsyncSession, reporter: Identity, target: Identity
) -> DefectReport:
    """Point at a defective print. One report per identity per identity."""
    if reporter.id == target.id:
        raise BankError("на себя не жалуются даже по лору")
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


# --- city line and margin (D-175) --------------------------------------------


def city_margin(constants: Constants, catalog, city) -> float:
    """City margin: code-law `bank_margin` with ceiling `bank.city_margin_cap`."""

    raw_item = town.law(catalog, city, "bank_margin")
    try:
        margin = float(raw_item)
    except (TypeError, ValueError):
        margin = 0.0
    return max(0.0, min(constants[R.BANK_CITY_MARGIN_CAP], margin))


async def offered_rate(
    session: AsyncSession,
    constants: Constants,
    catalog,
    who: Identity,
    *,
    amount: int = 0,
    now: datetime | None = None,
) -> tuple[float, str]:
    """The rate this borrower would actually get, and why (D-193).

    The same arithmetic as `borrow`, only without taking the money: a rate that
    turns up after the fact reads as a swindle even when it is computed right.
    """

    moment = now or datetime.now(UTC)
    key = await key_rate(session, constants)
    entry = await town.citizenship(session, who.id)
    if entry is None:
        premium = constants[R.BANK_RISK_PREMIUM].max
        return key + premium, (
            f"ключевая {key:.2f}% + {premium:.2f}% за риск: без гражданства "
            "занимают напрямую у столицы (D-175)"
        )

    city = await town.by_id(session, entry.city_id)
    if city is None:  # pragma: no cover -- citizenship into nowhere is a bug
        return key, f"ключевая {key:.2f}%"

    permitted, _, free = await city_line(session, constants, city, now=moment)
    if amount <= free:
        margin = city_margin(constants, catalog, city)
        return key + margin, (
            f"ключевая {key:.2f}% + маржа города {margin:.2f}% "
            f"({city.name}); линии свободно {money_str(free)} ₭"
        )

    premium = constants[R.BANK_RISK_PREMIUM].max
    return key + premium, (
        f"ключевая {key:.2f}% + {premium:.2f}% за риск: линия города "
        f"{city.name} исчерпана — разрешено {money_str(permitted)} ₭ от оборота, "
        f"свободно {money_str(free)} ₭. Линию поднимают сделки на его земле (D-193)"
    )


async def city_outstanding(session: AsyncSession, city) -> int:
    """How much citizen debt sits on this city's line with the capital."""
    result = await session.scalar(
        select(func.coalesce(func.sum(Loan.outstanding), 0)).where(
            Loan.city_id == city.id, Loan.state == LoanState.OPEN
        )
    )
    return int(result or 0)


async def city_line(
    session: AsyncSession, constants: Constants, city, *, now: datetime | None = None
) -> tuple[int, int, int]:
    """City line: (permitted, occupied, free), in minor units.

    Permitted is `bank.debt_to_turnover_cap` of the city's turnover over
    `credit.window`. The city's debt outlives the authority (D-175): a change of
    ruler repays nothing, otherwise "borrow, hand out to your own, get
    re-elected" is the dominant strategy.
    """
    moment = now or datetime.now(UTC)
    window = moment - timedelta(days=constants[R.CREDIT_WINDOW])
    turnovers = await _turnover_by_city(session, window)
    turnover = turnovers.get(city.id, 0)
    permitted = int(turnover * constants[R.BANK_DEBT_TO_TURNOVER_CAP] / PERCENT)
    occupied = await city_outstanding(session, city)
    return permitted, occupied, max(0, permitted - occupied)


# --- prison credit (D-174) ---------------------------------------------------


async def prison_credit(
    session: AsyncSession,
    constants: Constants,
    city,
    debtor_identity_id: uuid.UUID,
    cost: int,
    *,
    now: datetime | None = None,
) -> int:
    """The treasury pays the reference value of what a prisoner mined toward their debt.

    The circle closes (D-174, D-175): ore to the city, treasury money toward
    repayment, repayment to the capital's reserve. Returns how much could be
    credited; zero -- the treasury is empty, and the ore stays with the prisoner.
    """

    moment = now or datetime.now(UTC)
    treasury = await town.treasury(session, city)
    if await ledger.balance(session, treasury.id) < cost:
        return 0

    debtor = await session.get(Identity, debtor_identity_id)
    loans = sorted(await loans_of(session, debtor_identity_id), key=lambda loan: loan.taken_at)
    credited = 0
    remainder = cost
    for loan in loans:
        if remainder <= 0:
            break
        payment = min(remainder, loan.outstanding)
        if payment <= 0:
            continue
        await repay(
            session,
            constants,
            debtor,
            loan,
            payment / MONEY_SCALE,
            from_account=treasury,
            now=moment,
        )
        credited += payment
        remainder -= payment
    if credited > 0:
        await events.record(
            session,
            EventKind.PRISON_WORKOFF,
            actor_identity_id=debtor_identity_id,
            city_id=str(city.id),
            amount=credited,
        )
    return credited


@handler(JobKind.SEIGNIORAGE)
async def seigniorage_cancelled(session: AsyncSession, job: Job) -> None:
    """Seigniorage is cancelled (D-175): the city earns by margin, not by handouts.

    The job kind stays in the enumeration forever -- the journal is eternal --
    and an old job that outlived the mechanic's cancellation closes without effect.
    """

    return
