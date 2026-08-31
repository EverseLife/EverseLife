# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""city: treasury.

Split out of `engine/city.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import events, ledger
from src.engine.city._base import CityError, NotEnoughTreasury
from src.engine.city.polity import require, require_at_hall
from src.models.city import (
    City,
    Power,
)
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.ledger import AccountKind, PostingReason
from src.units import money_str


async def treasury(session: AsyncSession, city: City):
    return await ledger.account_for(session, AccountKind.CITY_TREASURY, city.node_id)


async def treasury_balance(session: AsyncSession, city: City) -> int:
    account = await treasury(session, city)
    return await ledger.balance(session, account.id)


async def spend(
    session: AsyncSession,
    by: Identity,
    city: City,
    to: Identity,
    amount: int,
    *,
    memo: str = "",
    body=None,
) -> int:
    """Pay an identity from the treasury. Returns what was paid in minor units.

    Neither salary, nor reward, nor contract are separate mechanics: all of
    them are a transfer from the treasury with a named ground. People invent
    the names; the engine only needs the posting.
    """
    await require_at_hall(session, body, city)
    await require(session, by.id, city, Power.TREASURY)
    if amount <= 0:
        raise CityError(key="city-treasury-zero")

    treasury_account = await treasury(session, city)
    remainder = await ledger.balance(session, treasury_account.id)
    if remainder < amount:
        raise NotEnoughTreasury(
            key="city-treasury-short",
            have=money_str(remainder),
            need=money_str(amount),
        )

    to_whom = await ledger.account_for(session, AccountKind.IDENTITY, to.id)
    await ledger.transfer(
        session,
        PostingReason.SALARY,
        debit=treasury_account.id,
        credit=to_whom.id,
        amount=amount,
        memo={"city": city.name, "to": to.name, "ground": memo},
    )
    await events.record(
        session,
        EventKind.CITY_TREASURY_SPENT,
        actor_identity_id=by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        to=to.name,
        amount=amount,
        memo=memo,
    )
    return amount
