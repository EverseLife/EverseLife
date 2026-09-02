# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The loan for works (D-248): a city borrows against its line for the
order it posts, repays from the treasury, and shows what it owes.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import bank, events, ledger
from src.engine import city as town
from src.engine.works_city._base import WorksCityError
from src.models.bank import Loan, LoanState
from src.models.city import City, Power
from src.models.event import EventKind
from src.models.identity import Body, Identity
from src.models.ledger import AccountKind
from src.models.ledger import PostingReason as Reason
from src.units import money, money_str

# --- the treasury as a borrower (D-248) ---------------------------------------


async def borrow_for_works(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity,
    body: Body,
    amount: float,
    *,
    now: datetime | None = None,
) -> Loan:
    """The city borrows from the CB: key rate, no margin, on its own line.

    No margin and no premium -- a city cannot mark itself up -- and no
    collateral, like every loan here (D-173): the limit is the line, and the
    line is turnover. Money goes reserve-first, the shortfall is printed,
    exactly as a citizen's loan does.
    """
    moment = now or datetime.now(UTC)
    await town.require_at_hall(session, body, city)
    await town.require(session, by.id, city, Power.TREASURY)
    #: The capital is the bank (D-175): it does not borrow from itself, it
    #: prints by the holders' signatures (D-270). The window hides the
    #: button; this is the rule.
    if city.capital:
        raise WorksCityError(key="works-city-capital-prints", city=city.name)
    total = money(amount)
    if total <= 0:
        raise WorksCityError(key="works-city-loan-not-positive")
    #: The line check is read-then-insert, and the line is the treasury
    #: loan's only brake: two rulers borrowing at once must not both see it
    #: free. The city row serialises them; the loser rereads a line that
    #: already carries the winner's loan.
    await session.execute(select(City.id).where(City.id == city.id).with_for_update())
    _, _, free = await bank.city_line(session, constants, city, now=moment)
    if total > free:
        raise WorksCityError(
            key="works-city-line-exhausted",
            money=money_str(free),
            cap=constants[R.BANK_DEBT_TO_TURNOVER_CAP],
        )

    rate_value = await bank.key_rate(session, constants)
    reserve_treasury = await bank.reserve_account(session)
    have = await ledger.balance(session, reserve_treasury.id)
    printed = max(0, total - have)
    if printed > 0:
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            Reason.GENESIS,
            debit=genesis.id,
            credit=reserve_treasury.id,
            amount=printed,
            memo={"печать под кредит казне": city.name},
        )
    await ledger.transfer(
        session,
        Reason.LOAN,
        debit=reserve_treasury.id,
        credit=(await town.treasury(session, city)).id,
        amount=total,
        memo={"кредит казне": city.name},
    )
    loan = Loan(
        identity_id=None,
        principal=total,
        outstanding=total,
        rate=rate_value,
        city_id=city.id,
        margin=0,
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
        actor_identity_id=by.id,
        loan_id=str(loan.id),
        amount=total,
        rate=rate_value,
        printed=printed,
        city=city.name,
        treasury_loan=True,
    )
    return loan


async def repay_for_works(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity,
    body: Body,
    loan: Loan,
    amount: float | None = None,
    *,
    now: datetime | None = None,
) -> int:
    """Repay the treasury's loan from the treasury. Interest goes to the
    reserve whole: there is no margin to keep."""
    await town.require_at_hall(session, body, city)
    await town.require(session, by.id, city, Power.TREASURY)
    if loan.identity_id is not None or loan.city_id != city.id:
        raise WorksCityError(key="works-city-not-treasury-loan")
    return await bank.repay(
        session,
        constants,
        by,
        loan,
        amount,
        from_account=await town.treasury(session, city),
        now=now,
    )


async def treasury_loans(session: AsyncSession, city: City) -> list[Loan]:
    return list(
        (
            await session.execute(
                select(Loan).where(
                    Loan.city_id == city.id,
                    Loan.identity_id.is_(None),
                    Loan.state == LoanState.OPEN,
                )
            )
        )
        .scalars()
        .all()
    )
