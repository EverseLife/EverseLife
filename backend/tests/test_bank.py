# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Bank: reserve, credit, key rate (D-030, D-087, D-167).

Checked is what the bank is built this way for:

* money comes **from the reserve**, and only the shortfall is printed;
* repayment returns TC to the reserve, not into circulation -- the reserve is
  a steriliser;
* the invariant "total supply = accounts + reserve" holds both after issue
  and after repayment;
* the rate is computed by a public formula, does not jump more than a step
  and does not leave the floor and ceiling.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import bank, ledger, world
from src.models.bank import LoanState
from src.models.ledger import AccountKind, LedgerAccount, PostingReason
from src.units import PERCENT, money


async def _borrower(session: AsyncSession, *, funds: float = 0):
    identity = await world.create_identity(session, f"Заёмщик-{uuid.uuid4().hex[:6]}")
    if funds:
        account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=account.id,
            amount=money(funds),
            memo={},
        )
    return identity


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
    assert "инфляция" in first[1], "решение объясняется словами"


async def test_silent_sensor_does_not_move_lever(constants: Constants) -> None:
    rate, reason = bank.compute_rate(
        constants,
        previous=constants[R.BANK_BASE_RATE],
        inflation=None,
        emission_share=None,
    )
    assert rate == pytest.approx(constants[R.BANK_BASE_RATE])
    assert "не измерена" in reason


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
    assert decision.why, "почему получилось столько, видно всем"
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
    await _deal(session, "Железная руда", 4000, 1, seller=who)
    await bank.borrow(session, constants, catalog, who, 1000)

    share = await bank._emission_share(session, constants, now=datetime.now(UTC))  # noqa: SLF001
    assert isinstance(share, float), "доля — число, а не сумма"

    decision = await bank.review_rate(session, constants)
    assert decision.why


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
    await _deal(session, "Железная руда", 4000, 1, seller=who)
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


# --- price sensor and sterilisation (D-087, D-169) ---------------------------


async def _deal(session: AsyncSession, goods: str, price: float, qty: float, seller=None):
    """A concluded deal: the price index is computed from them."""
    from src.models.market import Trade
    from src.units import amount as _amount

    node = await world.create_node(
        session, f"terra.mkt.{uuid.uuid4().hex[:8]}", "Рынок", area_m2=10
    )
    if seller is None:
        seller = await world.create_identity(session, f"П-{uuid.uuid4().hex[:6]}")
    from src.models.market import Order, OrderSide

    order_ = Order(
        node_id=node.id,
        identity_id=seller.id,
        side=OrderSide.SELL,
        type_key=goods,
        tier="обычное",
        price=money(price),
        amount_total=_amount(qty),
        amount_left=0,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(order_)
    await session.flush()
    session.add(
        Trade(
            node_id=node.id,
            sell_order_id=order_.id,
            type_key=goods,
            tier="обычное",
            price=money(price),
            amount=_amount(qty),
        )
    )
    await session.flush()


async def test_index_is_median_of_deals(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One deal at an absurd price does not move monetary policy."""
    assert await bank.price_index(session, constants) is None, "сделок нет — молчим"

    for price in (10, 10, 1000):
        await _deal(session, "Железная руда", price, 1)
    index = await bank.price_index(session, constants)
    assert index == pytest.approx(money(10)), "медиана, а не среднее"


async def test_index_weighted_by_turnover(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Bread matters more than a rare alloy exactly as much as more of it is bought."""
    await _deal(session, "Хлеб", 10, 100)
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


# --- collateral ratio as a lever (D-170) -------------------------------------


async def _city_with_turnover(session: AsyncSession, catalog, turnover: float, goods: str = "Хлеб"):
    """The city on whose territory the deals happened: the share is computed by them."""
    from src.engine import city as town
    from src.models.market import Order, OrderSide, Trade
    from src.models.world import Layer
    from src.units import amount as _amount

    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session,
        f"terra.city.{stamp}",
        f"Город-{stamp}",
        area_m2=1,
        layer=Layer.PLANET,
        parent=planet,
    )
    marketplace = await world.create_node(
        session,
        f"terra.city.{stamp}.market",
        "Рынок",
        area_m2=50,
        parent=delegate,
    )
    city = await town.found(session, catalog, delegate, f"Город-{stamp}")
    marketplace.owner_city_id = city.id
    seller = await world.create_identity(session, f"Купец-{stamp}")
    order_ = Order(
        node_id=marketplace.id,
        identity_id=seller.id,
        side=OrderSide.SELL,
        type_key=goods,
        tier="обычное",
        price=money(turnover),
        amount_total=_amount(1),
        amount_left=0,
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    session.add(order_)
    await session.flush()
    session.add(
        Trade(
            node_id=marketplace.id,
            sell_order_id=order_.id,
            type_key=goods,
            tier="обычное",
            price=money(turnover),
            amount=_amount(1),
        )
    )
    await session.flush()
    return city


async def test_payment_covers_interest_first(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Without this "system income" is unmeasurable, and hence not returned (D-171)."""
    who = await _borrower(session, funds=1000)
    #: A limit above the base is given by labour: sales turnover over the window (D-173).
    await _deal(session, "Железная руда", 4000, 1, seller=who)
    loan = await bank.borrow(session, constants, catalog, who, 1000)
    in_a_year = loan.taken_at + timedelta(days=constants[R.BANK_YEAR_DAYS])
    accrued = await bank.accrue(session, constants, loan, now=in_a_year)
    assert accrued > 0

    paid = await bank.repay(session, constants, who, loan, 10, now=in_a_year)
    assert loan.interest_paid == paid, "платёж ушёл в проценты целиком"


async def _city_with_townhall(session: AsyncSession, catalog: Catalog):
    """A city with an administration: only such counts when handing over the rate."""
    from src.engine import city as town
    from src.models.world import Layer

    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session,
        f"terra.town.{stamp}",
        f"Город-{stamp}",
        area_m2=1,
        layer=Layer.PLANET,
        parent=planet,
    )
    core = await world.create_node(
        session,
        f"terra.town.{stamp}.core",
        "Ядро",
        area_m2=100,
        parent=delegate,
    )
    city = await town.found(session, catalog, delegate, f"Город-{stamp}")
    core.owner_city_id = city.id
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, town.HALL, quality=60, origin="тест")
    ruler = await world.create_identity(session, f"Глава-{stamp}")
    await town.install_founder(session, city, ruler)
    await session.flush()
    return city, ruler


async def test_algorithm_decides_while_few_cities(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, ruler = await _city_with_townhall(session, catalog)
    assert not await bank.council_decides(session, constants)
    with pytest.raises(bank.NotCouncilTime):
        await bank.council_set_rate(session, constants, city, ruler, 6)


async def test_council_gets_rate_at_threshold(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The threshold is counted by cities with an administration: a signboard is not an organ of
    power."""
    threshold = int(constants[R.BANK_COUNCIL_HANDOVER_CITIES])
    cities = [await _city_with_townhall(session, catalog) for _ in range(threshold)]
    assert await bank.cities_with_hall(session) == threshold
    assert await bank.council_decides(session, constants)

    city, ruler = cities[0]
    decision = await bank.council_set_rate(session, constants, city, ruler, 6)
    assert float(decision.rate) == pytest.approx(6)
    assert "Совета городов" in decision.why
    assert await bank.key_rate(session, constants) == pytest.approx(6)


async def test_corridor_bounds_council(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The council argues with the algorithm rather than replacing it (D-172)."""
    threshold = int(constants[R.BANK_COUNCIL_HANDOVER_CITIES])
    cities = [await _city_with_townhall(session, catalog) for _ in range(threshold)]
    city, ruler = cities[0]
    far_away = constants[R.BANK_BASE_RATE] + constants[R.BANK_COUNCIL_RATE_DEVIATION] + 1
    with pytest.raises(bank.OutOfCorridor):
        await bank.council_set_rate(session, constants, city, ruler, far_away)


async def test_vote_cast_by_holder_of_laws_right(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    threshold = int(constants[R.BANK_COUNCIL_HANDOVER_CITIES])
    cities = [await _city_with_townhall(session, catalog) for _ in range(threshold)]
    city, _ = cities[0]
    from src.engine import city as town

    stranger = await world.create_identity(session, f"Никто-{uuid.uuid4().hex[:6]}")
    with pytest.raises(town.NotAllowed):
        await bank.council_set_rate(session, constants, city, stranger, 6)


async def test_emergency_returns_rate_to_algorithm(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A political decision is good until the price of a mistake is everybody's money."""
    from src.models.bank import RateDecision

    threshold = int(constants[R.BANK_COUNCIL_HANDOVER_CITIES])
    cities = [await _city_with_townhall(session, catalog) for _ in range(threshold)]
    now_ = datetime.now(UTC)
    session.add(
        RateDecision(
            rate=constants[R.BANK_BASE_RATE],
            why="авария",
            decided_at=now_,
            locked_until=now_ + timedelta(days=constants[R.BANK_COUNCIL_LOCKOUT]),
        )
    )
    await session.flush()

    assert not await bank.council_decides(session, constants, now=now_)
    city, ruler = cities[0]
    with pytest.raises(bank.NotCouncilTime):
        await bank.council_set_rate(session, constants, city, ruler, 6, now=now_)

    #: The lockout ended -- the rate is with the Council again.
    later = now_ + timedelta(days=constants[R.BANK_COUNCIL_LOCKOUT] + 1)
    assert await bank.council_decides(session, constants, now=later)


# --- credit from labour (D-173) ----------------------------------------------


async def test_turnover_raises_limit(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Labour grants the limit: time in game is the cheapest thing to farm."""
    who = await _borrower(session)
    base, _ = await bank.credit_limit(session, constants, who.id)
    assert base == money(constants[R.BANK_UNSECURED_LIMIT])

    await _deal(session, "Железная руда", 1000, 1, seller=who)
    raised, reason = await bank.credit_limit(session, constants, who.id)
    increment = money(1000 * constants[R.CREDIT_TURNOVER_SHARE] / PERCENT)
    assert raised == base + increment
    assert "оборот" in reason, "формула объясняется словами, как ставка"


async def test_credit_history_is_asset(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """What was repaid earlier raises the limit -- and gives a record without overdue."""
    who = await _borrower(session, funds=100)
    loan = await bank.borrow(session, constants, catalog, who, 100)
    await bank.repay(session, constants, who, loan)

    limit_, reason = await bank.credit_limit(session, constants, who.id)
    base_ = money(constants[R.BANK_UNSECURED_LIMIT])
    core = base_ + money(100 * constants[R.CREDIT_REPAID_SHARE] / PERCENT)
    assert limit_ == int(core * (1 + constants[R.CREDIT_NO_OVERDUE_BONUS] / PERCENT))
    assert "стаж" in reason


async def test_report_cuts_trust_but_does_not_bury(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A defective print lowers credit; only support does the processing."""
    who = await _borrower(session)
    limit_before, _ = await bank.credit_limit(session, constants, who.id)

    #: A dozen ill-wishers -- and trust hits the floor, not zero.
    for number in range(12):
        foe = await world.create_identity(session, f"Недруг-{number}-{uuid.uuid4().hex[:4]}")
        await bank.report_defect(session, foe, who)

    faith = await bank.trust(session, constants, who.id)
    assert faith == pytest.approx(constants[R.CREDIT_TRUST_FLOOR] / PERCENT)
    limit_after, reason = await bank.credit_limit(session, constants, who.id)
    assert limit_after == int(limit_before * faith)
    assert "доверие" in reason


async def test_report_one_per_pair_and_revocable(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    who = await _borrower(session)
    foe = await world.create_identity(session, f"Недруг-{uuid.uuid4().hex[:6]}")
    await bank.report_defect(session, foe, who)
    await bank.report_defect(session, foe, who)
    assert await bank.trust(session, constants, who.id) == pytest.approx(
        1 - constants[R.CREDIT_REPORT_PENALTY] / PERCENT
    ), "второй репорт той же пары не считается"

    assert await bank.withdraw_report(session, foe, who)
    assert await bank.trust(session, constants, who.id) == pytest.approx(1.0)


# --- loan through the city (D-175) -------------------------------------------


async def _citizen_with_city(session: AsyncSession, catalog: Catalog, *, turnover: float = 4000):
    """A city with turnover and its citizen: the line is open, the margin is default."""
    from src.models.city import Citizen

    city = await _city_with_turnover(session, catalog, turnover)
    who = await _borrower(session)
    session.add(Citizen(identity_id=who.id, city_id=city.id))
    await session.flush()
    return city, who


async def test_citizen_borrows_from_city_with_margin(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The rate is key plus city margin; the loan sits on the city's line."""
    city, who = await _citizen_with_city(session, catalog)
    loan = await bank.borrow(session, constants, catalog, who, 100)

    margin = bank.city_margin(constants, catalog, city)
    assert loan.city_id == city.id
    assert float(loan.margin) == pytest.approx(margin)
    assert float(loan.rate) == pytest.approx(constants[R.BANK_BASE_RATE] + margin)
    _, occupied, _ = await bank.city_line(session, constants, city)
    assert occupied == loan.outstanding, "заём висит на линии города"


async def test_city_margin_goes_to_its_treasury(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The city earns on its borrowers -- seigniorage is unnecessary (D-175)."""
    from src.engine import city as town

    city, who = await _citizen_with_city(session, catalog)
    loan = await bank.borrow(session, constants, catalog, who, 100)
    in_a_year = loan.taken_at + timedelta(days=constants[R.BANK_YEAR_DAYS])
    await bank.accrue(session, constants, loan, now=in_a_year)

    treasury_before = await town.treasury_balance(session, city)
    reserve_before = await bank.reserve(session)
    #: We repay exactly the interest: that is what is split between city and capital.
    interest = loan.interest_accrued
    payer = await _borrower(session, funds=1000)
    await bank.repay(session, constants, payer, loan, interest / 10_000, now=in_a_year)

    city_share = int(interest * float(loan.margin) / float(loan.rate))
    assert await town.treasury_balance(session, city) - treasury_before == city_share
    assert await bank.reserve(session) - reserve_before == interest - city_share


async def test_exhausted_line_gives_pricier_direct_loan(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """There is always a way out, but at the top of the risk range: the city's line is not
    elastic."""
    city, who = await _citizen_with_city(session, catalog, turnover=100)
    #: Line = cap% of turnover 100: the very first big loan overflows it.
    await _deal(session, "Железная руда", 4000, 1, seller=who)
    loan = await bank.borrow(session, constants, catalog, who, 900)

    assert loan.city_id is None, "линии не хватило — заём прямой"
    assert float(loan.rate) == pytest.approx(
        constants[R.BANK_BASE_RATE] + constants[R.BANK_RISK_PREMIUM].max
    )


async def test_non_citizen_borrows_directly(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    who = await _borrower(session)
    loan = await bank.borrow(session, constants, catalog, who, 50)
    assert loan.city_id is None
    assert float(loan.rate) == pytest.approx(
        constants[R.BANK_BASE_RATE] + constants[R.BANK_RISK_PREMIUM].max
    )


# --- prison credit (D-174) ---------------------------------------------------


async def test_treasury_pays_for_ore_toward_repayment(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The circle closes: ore to the city, treasury money to the capital's reserve."""
    from src.engine import city as town
    from src.engine import ledger as l
    from src.models.ledger import PostingReason as PR

    city, who = await _citizen_with_city(session, catalog)
    loan = await bank.borrow(session, constants, catalog, who, 100)
    #: We fund the treasury: a prison is a solvent city's investment.
    treasury = await town.treasury(session, city)
    genesis = await l.account_for(session, AccountKind.GENESIS, None)
    await l.transfer(
        session,
        PR.GENESIS,
        debit=genesis.id,
        credit=treasury.id,
        amount=money(500),
        memo={},
    )

    before = loan.outstanding
    credited = await bank.prison_credit(session, constants, city, who.id, money(60))
    assert credited == money(60)
    assert loan.outstanding == before - money(60)


async def test_empty_treasury_gives_no_credit(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """No money -- no penal labour: the ore stays with the prisoner (D-174)."""
    city, who = await _citizen_with_city(session, catalog)
    await bank.borrow(session, constants, catalog, who, 100)
    assert await bank.prison_credit(session, constants, city, who.id, money(60)) == 0


async def test_labour_in_prison_face_repays_debt(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The end-to-end case: a vein in prison, yield to the city, debt melts (D-174)."""
    from src.engine import city as town
    from src.engine import justice, mining
    from src.engine import ledger as l
    from src.models.city import Citizen
    from src.models.ledger import PostingReason as PR

    #: City turnover -- by ore deals: the reference price is taken from them.
    city = await _city_with_turnover(session, catalog, 4000, goods="Железная руда")
    delegate = await session.get(
        __import__("src.models.world", fromlist=["Node"]).Node, city.node_id
    )
    prison = await world.create_node(
        session,
        f"terra.jail.{uuid.uuid4().hex[:6]}",
        "Каторга",
        area_m2=100,
        parent=delegate,
        properties={justice.PRISON_NODE: True},
    )
    prison.owner_city_id = city.id
    vein = await world.create_vein(session, prison, "Железная руда", richness=60, remaining=10_000)
    debtor = await world.create_identity(session, f"Должник-{uuid.uuid4().hex[:6]}")
    session.add(Citizen(identity_id=debtor.id, city_id=city.id))
    body = await world.print_body(session, debtor, prison)

    loan = await bank.borrow(session, constants, catalog, debtor, 100)
    #: The money is spent, the debt is overdue -- the node holds (D-168).
    account = await l.account_for(session, AccountKind.IDENTITY, debtor.id)
    genesis = await l.account_for(session, AccountKind.GENESIS, None)
    await l.transfer(
        session,
        PR.TRADE,
        debit=account.id,
        credit=genesis.id,
        amount=await l.balance(session, account.id),
        memo={"прожито": "всё"},
    )
    loan.serviced_at = loan.taken_at - timedelta(days=constants[R.DEBT_PRISON_THRESHOLD] + 1)
    treasury = await town.treasury(session, city)
    await l.transfer(
        session,
        PR.GENESIS,
        debit=genesis.id,
        credit=treasury.id,
        amount=money(1000),
        memo={},
    )
    await session.flush()

    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "Каменная кирка", quality=50, origin="сценарий теста")
    sess = await mining.start(session, constants, body, vein)
    await mining.swing(session, constants, sess)
    before = loan.outstanding
    mined = await mining.leave(session, constants, sess)

    assert mined > 0
    assert loan.outstanding < before, "добыча зачлась в долг"
    from_yard = await world.node_container(session, prison)
    ore_ = (
        (
            await session.execute(
                select(__import__("src.models.inventory", fromlist=["Item"]).Item).where(
                    __import__("src.models.inventory", fromlist=["Item"]).Item.container_id
                    == from_yard.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert ore_, "добытое досталось городу, а не заключённому"
