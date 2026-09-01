# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The bank's vocabulary and its floor: the reserve account, the key rate as
it stands, and every refusal the bank knows how to make. Asks nobody above
itself.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import ledger
from src.engine.errors import Refusal
from src.models.bank import RateDecision
from src.models.ledger import (
    AccountKind,
    LedgerAccount,
)

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


# --- insolvency (D-063, D-168) -----------------------------------------------


class Restrained(BankError):
    """Debt holds in the node: this is world physics, not a city verdict."""


# --- Council of cities and the rate (D-087, D-172) ---------------------------


class NotCouncilTime(BankError):
    """The algorithm decides the rate: either few cities, or a lockout is in force."""


class OutOfCorridor(BankError):
    """The council argues with the algorithm rather than replacing it: there is a corridor."""
