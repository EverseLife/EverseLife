# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The loan, the rate and the reserve (D-030, D-248).

Money is printed only when the reserve runs dry and returns to it on
repayment; the rate is a public formula reviewed on schedule and fixed for
the borrower at issue; interest accrues, overdue is counted from the last
payment, and the index of prices steers the reserve. The council and the
city's own credit live in `test_bank_city.py`.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bank_kit import _borrower, _deal
from src import i18n
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import bank, ledger, world
from src.engine.errors import Says
from src.models.bank import LoanState
from src.models.ledger import AccountKind, LedgerAccount, PostingReason
from src.units import PERCENT, money


async def _mass(session: AsyncSession) -> tuple[int, int]:
    """Circulating supply and reserve: their sum is the whole TC supply (D-087)."""
    turnover = await bank.circulating(session)
    return turnover, await bank.reserve(session)


async def _account(session: AsyncSession, who) -> int:
    account = await ledger.account_for(session, AccountKind.IDENTITY, who.id)
    return await ledger.balance(session, account.id)


# --- reserve and emission ----------------------------------------------------


async def test_every_restart_does_not_add_a_rate_review(
    session: AsyncSession, constants: Constants
) -> None:
    """The key rate is reviewed once every `bank.rate_review_period` days.

    `schedule_review` is called at the start of **every** process: two
    processes per deploy, and a deploy a day. Counted from the second of the
    call, each of those laid down a review of its own -- a different second, a
    different dedup key, a new job -- and every one of them fired. The
    chronicle filled with rate decisions, several in one digest, for a number
    the vault says moves once in three days.
    """
    from src.models.job import Job, JobKind, JobState

    morning = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
    for minute in (0, 7, 41):
        await bank.schedule_review(session, constants, after=morning + timedelta(minutes=minute))
    #: And again the next day, when the arithmetic alone stops covering us: a
    #: fresh day gives a fresh key, and only the pending chain holds it back.
    await bank.schedule_review(session, constants, after=morning + timedelta(days=1))

    queued = (
        (
            await session.execute(
                select(Job).where(
                    Job.kind == JobKind.RATE_REVIEW.value, Job.state == JobState.PENDING
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(queued) == 1, f"на каждый запуск завелось по пересмотру: {len(queued)}"
    assert queued[0].run_at > morning

    #: A chain that ran and finished does not stop the policy: the second it
    #: sat on is taken for ever, and the next review moves past it.
    queued[0].state = JobState.DONE
    await session.flush()
    await bank.schedule_review(session, constants, after=morning)
    alive = (
        (
            await session.execute(
                select(Job).where(
                    Job.kind == JobKind.RATE_REVIEW.value, Job.state == JobState.PENDING
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(alive) == 1, "погибшая цепочка выключила денежную политику"


async def test_empty_reserve_prints_exactly_what_is_needed(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    who = await _borrower(session)
    loan = await bank.borrow(session, constants, catalog, who, 100)

    assert loan.printed == money(100), "резерв был пуст — напечатано всё"
    assert await _account(session, who) == money(100)
    assert await bank.reserve(session) == 0, "выданное ушло из резерва"


async def test_repayment_returns_money_to_reserve_not_circulation(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The reserve is a steriliser: money leaves circulation and waits for a borrower."""
    who = await _borrower(session)
    loan = await bank.borrow(session, constants, catalog, who, 100)
    turnover_before, reserve_before = await _mass(session)

    gave_back = await bank.repay(session, constants, who, loan, 40)

    turnover_after, reserve_after = await _mass(session)
    assert gave_back == money(40)
    assert reserve_after - reserve_before == money(40)
    assert turnover_before - turnover_after == money(40)
    assert turnover_before + reserve_before == turnover_after + reserve_after, (
        "вся масса ТК не изменилась: погашение не сжигает деньги"
    )


async def test_second_loan_takes_from_reserve_and_does_not_print(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    first = await _borrower(session)
    loan = await bank.borrow(session, constants, catalog, first, 100)
    await bank.repay(session, constants, first, loan, 100)

    second = await _borrower(session)
    new = await bank.borrow(session, constants, catalog, second, 60)
    assert new.printed == 0, "в резерве было — печатать незачем"


async def test_loan_closes_with_full_repayment(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    who = await _borrower(session, funds=50)
    loan = await bank.borrow(session, constants, catalog, who, 100)
    await bank.repay(session, constants, who, loan)
    assert loan.state is LoanState.REPAID
    assert loan.outstanding == 0
    assert not await bank.loans_of(session, who.id)


# --- bounds ------------------------------------------------------------------


async def test_without_collateral_no_more_than_limit(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    who = await _borrower(session)
    limit = constants[R.BANK_UNSECURED_LIMIT]
    with pytest.raises(bank.TooMuch):
        await bank.borrow(session, constants, catalog, who, limit + 1)
    loan = await bank.borrow(session, constants, catalog, who, limit)
    assert loan.principal == money(limit)


async def test_rate_formula_public_and_deterministic(
    constants: Constants,
) -> None:
    """The same inputs give the same answer -- otherwise the bank is a hidden NPC."""
    first = bank.compute_rate(
        constants, previous=constants[R.BANK_BASE_RATE], inflation=5, emission_share=20
    )
    second = bank.compute_rate(
        constants, previous=constants[R.BANK_BASE_RATE], inflation=5, emission_share=20
    )
    assert first == second
    #: The keys, not the sentence: the words are the locale's (D-251 wave IV).
    assert [one.key for one in first[1]] == [
        "bank-why-rate-base",
        "bank-why-rate-inflation",
        "bank-why-rate-emission",
    ], "решение объясняется по частям"


async def test_the_explanation_is_a_list_of_clauses(constants: Constants) -> None:
    """What the player is shown, said out of the keys (D-251 wave IV).

    Two things the key test upstairs cannot see. The clauses go over as a list
    and stay one -- the panel draws a line per fact, and nothing anywhere
    takes a rendered sentence apart to get them back. And the sign is a flag,
    so a misspelt one would quietly take the `*[false]` branch: the plus would
    simply disappear from "+0,50" and nobody would fail.
    """
    _, reasons = bank.compute_rate(
        constants, previous=constants[R.BANK_BASE_RATE], inflation=5, emission_share=20
    )
    said = i18n.clauses(reasons, locale="ru")
    assert len(said) == len(reasons), "оговорка на строку, ничего не склеено"
    assert all(one and "bank-why" not in one for one in said), "сказано словами, а не ключами"
    grew = next(one for one in reasons if one.key == "bank-why-rate-inflation")
    assert "+" in i18n.clauses([grew], locale="ru")[0], "знак роста ставит сам язык"
    fell = Says("bank-why-rate-inflation", {**grew.params, "inflation_up": "false"})
    assert "+" not in i18n.clauses([fell], locale="ru")[0].split("против")[0]


async def test_a_decision_keeps_its_reasons_not_a_sentence(constants: Constants) -> None:
    """The archive stores keys, so one row can be said in either language.

    The point of the column: a decision is written once and read back by
    whoever audits the rate afterwards, and their language is not known at the
    moment of writing. Rendered on the way in, the row would have been Russian
    forever -- which is what `why` was, and why it stopped being written.
    """
    _, reasons = bank.compute_rate(
        constants, previous=constants[R.BANK_BASE_RATE], inflation=5, emission_share=20
    )
    rows = i18n.written(reasons)
    assert [row["say"] for row in rows] == [one.key for one in reasons]
    assert i18n.retold(rows, locale="ru") == i18n.clauses(reasons, locale="ru")
    #: A stored row survives the trip through JSON: what goes into the column
    #: is what comes back out of it, keys and numbers alike.
    assert i18n.retold(json.loads(json.dumps(rows)), locale="ru") == i18n.retold(rows, locale="ru")


async def test_silent_sensor_does_not_move_lever(constants: Constants) -> None:
    rate, reason = bank.compute_rate(
        constants,
        previous=constants[R.BANK_BASE_RATE],
        inflation=None,
        emission_share=None,
    )
    assert rate == pytest.approx(constants[R.BANK_BASE_RATE])
    assert "bank-why-rate-inflation-unknown" in [one.key for one in reason]


async def test_rate_step_is_bounded(constants: Constants) -> None:
    """Monetary policy does not twitch: prediction is half its point."""
    before = constants[R.BANK_BASE_RATE]
    rate, _ = bank.compute_rate(constants, previous=before, inflation=100, emission_share=100)
    assert rate <= before + constants[R.BANK_RATE_STEP_MAX] + 1e-9


async def test_rate_stays_within_floor_and_ceiling(constants: Constants) -> None:
    low, _ = bank.compute_rate(
        constants,
        previous=constants[R.BANK_RATE_FLOOR],
        inflation=-100,
        emission_share=-100,
    )
    assert low >= constants[R.BANK_RATE_FLOOR]
    high, _ = bank.compute_rate(
        constants, previous=constants[R.BANK_RATE_CAP], inflation=100, emission_share=100
    )
    assert high <= constants[R.BANK_RATE_CAP]


async def test_review_stores_decision_and_applies(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    before = await bank.key_rate(session, constants)
    assert before == pytest.approx(constants[R.BANK_BASE_RATE]), "до решений — базовая"

    decision = await bank.review_rate(session, constants)
    assert decision.why_said, "почему получилось столько, видно всем"
    assert await bank.key_rate(session, constants) == pytest.approx(float(decision.rate))


async def test_review_survives_a_world_that_has_borrowed(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The rate is reviewed in a world with loans in it -- the only interesting kind.

    Reviewing an empty world takes the early exit and touches nothing: the
    emission sensor returns before it counts. In a world where somebody has
    actually borrowed, the sensor divides money by money and scales the result
    -- and money comes out of the database as `Decimal` while the scale is a
    float. That raise lived in a scheduled job, so nothing failed loudly: the
    live world's worker simply retried the rate review every two minutes and
    the key rate stopped moving.
    """
    who = await _borrower(session)
    await _deal(session, "iron_ore", 4000, 1, seller=who)
    await bank.borrow(session, constants, catalog, who, 1000)

    share = await bank._emission_share(session, constants, now=datetime.now(UTC))  # noqa: SLF001
    assert isinstance(share, float), "доля — число, а не сумма"

    decision = await bank.review_rate(session, constants)
    assert decision.why_said


async def test_borrower_rate_fixed_at_issue(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A loan is a contract, not a subscription to the bank's decisions (D-167)."""
    who = await _borrower(session)
    loan = await bank.borrow(session, constants, catalog, who, 50)
    was = float(loan.rate)

    from src.models.bank import RateDecision

    session.add(
        RateDecision(rate=constants[R.BANK_RATE_CAP], why="проверка", decided_at=datetime.now(UTC))
    )
    await session.flush()
    assert float(loan.rate) == was


# --- interest ----------------------------------------------------------------


async def test_interest_accrues_over_time(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    who = await _borrower(session)
    await _deal(session, "iron_ore", 4000, 1, seller=who)
    loan = await bank.borrow(session, constants, catalog, who, 1000)
    before = loan.outstanding

    in_a_year = loan.taken_at + timedelta(days=constants[R.BANK_YEAR_DAYS])
    accrued = await bank.accrue(session, constants, loan, now=in_a_year)

    expected_ = before * float(loan.rate) / PERCENT
    assert accrued == pytest.approx(expected_, rel=0.01)
    assert loan.outstanding == before + accrued


async def test_no_deposit_interest(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Money on the account does not grow: that is income without labour, i.e. emission (P1)."""
    who = await _borrower(session, funds=100)
    before = await _account(session, who)
    #: There is and must be no accrual on the balance in the engine.
    accounts = (
        (
            await session.execute(
                select(LedgerAccount).where(LedgerAccount.kind == AccountKind.IDENTITY)
            )
        )
        .scalars()
        .all()
    )
    assert accounts, "счёт есть"
    assert await _account(session, who) == before


# --- insolvency (D-063, D-168) -----------------------------------------------


async def test_overdue_counted_from_last_payment(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """An unserviced debt is an unpaid one, not an old one."""
    who = await _borrower(session)
    loan = await bank.borrow(session, constants, catalog, who, 100)
    relief = constants[R.DEBT_GRACE_PERIOD]

    on_time = loan.taken_at + timedelta(days=relief - 1)
    assert not bank.overdue(constants, loan, on_time)
    late = loan.taken_at + timedelta(days=relief + 1)
    assert bank.overdue(constants, loan, late)

    #: A payment moves the count: the loan is serviced again.
    await bank.repay(session, constants, who, loan, 10, now=late)
    assert not bank.overdue(constants, loan, late)


async def test_withholding_takes_share_of_remainder(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    who = await _borrower(session)
    loan = await bank.borrow(session, constants, catalog, who, 100)
    before = await _account(session, who)
    late = loan.taken_at + timedelta(days=constants[R.DEBT_GRACE_PERIOD] + 1)

    withheld = await bank.collect(session, constants, now=late)

    share = constants[R.DEBT_WORKOFF_RATE] / PERCENT
    assert withheld == pytest.approx(before * share, rel=0.02)
    assert await _account(session, who) == before - withheld
    assert await bank.reserve(session) == withheld, "удержанное ушло в резерв"


async def test_serviced_debt_left_alone(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    who = await _borrower(session)
    await bank.borrow(session, constants, catalog, who, 100)
    before = await _account(session, who)
    assert await bank.collect(session, constants) == 0
    assert await _account(session, who) == before


async def test_debt_holds_in_node_and_releases_on_payoff(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The system imposes the restriction, and anyone may pay (D-063)."""
    from_node = await world.create_node(
        session, f"terra.debt.{uuid.uuid4().hex[:6]}", "Узел", area_m2=100
    )
    dest = await world.create_node(
        session, f"terra.free.{uuid.uuid4().hex[:6]}", "Прочь", area_m2=100
    )
    from src.engine import travel

    await travel.connect(session, from_node, dest, base_seconds=60)

    debtor = await world.create_identity(session, f"Должник-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, debtor, from_node)
    loan = await bank.borrow(session, constants, catalog, debtor, 100)
    #: The money is spent, the debt remains, and it is not serviced.
    account = await ledger.account_for(session, AccountKind.IDENTITY, debtor.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        PostingReason.TRADE,
        debit=account.id,
        credit=genesis.id,
        amount=await ledger.balance(session, account.id),
        memo={"прожито": "всё"},
    )
    late = loan.taken_at + timedelta(days=constants[R.DEBT_PRISON_THRESHOLD] + 1)

    holds = await bank.restrained(session, constants, debtor.id, now=late)
    assert holds is not None
    with pytest.raises(travel.Imprisoned):
        await travel.depart(session, constants, body, dest, now=late)

    #: Payoff: a third party pays for the debtor, and the restriction lifts by itself.
    volunteer = await _borrower(session, funds=500)
    await bank.repay(session, constants, volunteer, loan, now=late)
    assert await bank.restrained(session, constants, debtor.id, now=late) is None
    assert await travel.depart(session, constants, body, dest, now=late) is not None


async def test_paying_debtor_is_free(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A loan honestly repaid does not take freedom."""
    who = await _borrower(session, funds=1000)
    loan = await bank.borrow(session, constants, catalog, who, 100)
    late = loan.taken_at + timedelta(days=constants[R.DEBT_PRISON_THRESHOLD] + 1)
    #: More money on the account than debt: nothing to restrict for.
    assert await bank.restrained(session, constants, who.id, now=late) is None


async def test_index_is_median_of_deals(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One deal at an absurd price does not move monetary policy."""
    assert await bank.price_index(session, constants) is None, "сделок нет — молчим"

    for price in (10, 10, 1000):
        await _deal(session, "iron_ore", price, 1)
    index = await bank.price_index(session, constants)
    assert index == pytest.approx(money(10)), "медиана, а не среднее"


async def test_index_weighted_by_turnover(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Bread matters more than a rare alloy exactly as much as more of it is bought."""
    await _deal(session, "bread", 10, 100)
    await _deal(session, "Сплав", 1000, 1)

    index = await bank.price_index(session, constants)
    #: Bread turnover 1000, alloy 1000 -- equal weights, the index in the middle.
    assert index == pytest.approx(money(505), rel=0.01)


async def test_reserve_surplus_burned_under_high_inflation(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The second lever: above-target inflation burns the unneeded surplus (D-169, D-248)."""
    from src.engine import works
    from src.models.metrics import DailyMetric

    who = await _borrower(session, funds=100)
    loan = await bank.borrow(session, constants, catalog, who, 200)
    await bank.repay(session, constants, who, loan, 200)

    #: The price index ran up: inflation well above target, everything burns.
    today = datetime.now(UTC).date()
    session.add(DailyMetric(day=today - timedelta(days=1), key=bank.PRICE_INDEX, value=100))
    session.add(DailyMetric(day=today, key=bank.PRICE_INDEX, value=120))
    await session.flush()

    in_circulation = await bank.circulating(session)
    ceiling = int(in_circulation * constants[R.BANK_RESERVE_CAP] / PERCENT)
    before = await bank.reserve(session)
    assert before > ceiling, "резерв заведомо выше потолка"

    burned, recycled = await works.recycle(session, constants)
    assert burned == before - ceiling
    assert recycled == 0, "при перегреве фонд не кормят"
    assert await bank.reserve(session) == ceiling
    assert await bank.circulating(session) == in_circulation, "оборот не тронут"


async def test_reserve_within_ceiling_not_touched(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    from src.engine import works

    who = await _borrower(session, funds=10_000)
    loan = await bank.borrow(session, constants, catalog, who, 100)
    await bank.repay(session, constants, who, loan, 100)
    assert await works.recycle(session, constants) == (0, 0)


async def test_payment_covers_interest_first(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Without this "system income" is unmeasurable, and hence not returned (D-171)."""
    who = await _borrower(session, funds=1000)
    #: A limit above the base is given by labour: sales turnover over the window (D-173).
    await _deal(session, "iron_ore", 4000, 1, seller=who)
    loan = await bank.borrow(session, constants, catalog, who, 1000)
    in_a_year = loan.taken_at + timedelta(days=constants[R.BANK_YEAR_DAYS])
    accrued = await bank.accrue(session, constants, loan, now=in_a_year)
    assert accrued > 0

    paid = await bank.repay(session, constants, who, loan, 10, now=in_a_year)
    assert loan.interest_paid == paid, "платёж ушёл в проценты целиком"
