# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The council over the rate, and the city as a lender (D-030, D-160, D-174).

The algorithm decides while cities are few and hands the rate to a council
at the threshold, inside a corridor and with an emergency way back; turnover
and history raise a line of credit, a report cuts trust without burying,
and a city lends to its citizens with a margin -- down to the prison face
that repays a debt. The loan itself lives in `test_bank.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bank_kit import _borrower, _deal
from src import i18n
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import bank, world
from src.models.ledger import AccountKind
from src.units import PERCENT, money

# --- collateral ratio as a lever (D-170) -------------------------------------


async def _city_with_turnover(
    session: AsyncSession, catalog, turnover: float, goods: str = "bread"
):
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
        tier="common",
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
            tier="common",
            price=money(turnover),
            amount=_amount(1),
        )
    )
    await session.flush()
    return city


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
    #: The Council's own line first, the algorithm's clauses under it: the
    #: vote is argued with the formula, and both are said to the reader.
    said = i18n.retold(decision.why_said, locale="ru")
    assert "Совета городов" in said[0]
    assert len(said) > 1, "под решением совета стоят оговорки самого алгоритма"
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

    await _deal(session, "iron_ore", 1000, 1, seller=who)
    raised, reason = await bank.credit_limit(session, constants, who.id)
    increment = money(1000 * constants[R.CREDIT_TURNOVER_SHARE] / PERCENT)
    assert raised == base + increment
    assert "bank-why-limit-turnover" in [one.key for one in reason], (
        "формула объясняется по частям, как ставка"
    )


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
    assert "bank-why-limit-no-overdue" in [one.key for one in reason]


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
    assert "bank-why-limit-trust" in [one.key for one in reason]


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
    await _deal(session, "iron_ore", 4000, 1, seller=who)
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
    city = await _city_with_turnover(session, catalog, 4000, goods="iron_ore")
    delegate = await session.get(
        __import__("src.models.world", fromlist=["Node"]).Node, city.node_id
    )
    prison = await world.create_node(
        session,
        f"terra.jail.{uuid.uuid4().hex[:6]}",
        "prison",
        area_m2=100,
        parent=delegate,
        properties={justice.PRISON_NODE: True},
    )
    prison.owner_city_id = city.id
    vein = await world.create_vein(session, prison, "iron_ore", richness=60, remaining=10_000)
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
    await world.grant_item(session, pocket, "stone_pickaxe", quality=50, origin="сценарий теста")
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
