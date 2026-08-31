# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""city: settlement grant for newcomers (D-153).

Split out of `engine/city.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import events, ledger
from src.engine.city.law import law_number
from src.engine.city.treasury import treasury
from src.models.city import (
    City,
    CityGrant,
)
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.ledger import AccountKind, PostingReason
from src.units import money


async def welcome(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    city: City,
    who: Identity,
) -> int:
    """Pay the settlement grant to a newcomer. Returns what was paid; zero is normal.

    This is **a transfer, not an emission**: not a coin appears in the world.
    The city pays from its treasury because a new resident is GDP: they buy,
    sell and pay taxes. Whether the investment pays off is the city's decision,
    not the engine's.

    Once per identity in one city. Moved -- entitled to receive it in the new
    one: that is how cities compete for people.
    """
    qty = money(law_number(constants, catalog, city, "newcomer_grant"))
    if qty <= 0:
        return 0

    before = (
        await session.execute(
            select(CityGrant).where(CityGrant.city_id == city.id, CityGrant.identity_id == who.id)
        )
    ).scalar_one_or_none()
    if before is not None:
        return 0

    treasury_account = await treasury(session, city)
    remainder = await ledger.balance(session, treasury_account.id)
    if remainder < qty:
        #: An empty treasury does not pay. This is not the newcomer's fault and
        #: not a reason to print money: the city is simply poor, and that shows.
        return 0

    to_whom = await ledger.account_for(session, AccountKind.IDENTITY, who.id)
    await ledger.transfer(
        session,
        PostingReason.SALARY,
        debit=treasury_account.id,
        credit=to_whom.id,
        amount=qty,
        memo={"подъёмные": city.name, "кому": who.name},
    )
    session.add(CityGrant(city_id=city.id, identity_id=who.id, amount=qty))
    await session.flush()

    await events.record(
        session,
        EventKind.CITY_GRANT_PAID,
        actor_identity_id=who.id,
        node_id=city.node_id,
        city_id=str(city.id),
        amount=qty,
    )
    return qty
