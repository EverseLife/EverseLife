# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The Council of cities over the rate (D-087, D-172): who sits in it, when
it decides instead of the algorithm, and the corridor it cannot leave.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import i18n
from src.constants import Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import events
from src.engine.bank._base import NotCouncilTime, OutOfCorridor, key_rate
from src.engine.bank.rate import _emission_share, compute_rate, inflation
from src.engine.errors import Says
from src.engine.world import thing_kinds
from src.models.bank import RateDecision
from src.models.city import City, Power
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.world import Node


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

    #: What stands (D-278): the council sits in a hall that is put up.
    return town.HALL in await thing_kinds(session, node)


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
            key="bank-council-not-yet", cities=constants[R.BANK_COUNCIL_HANDOVER_CITIES]
        )
    #: The rate is a matter of law, not of the treasury.
    await town.require(session, by.id, city, Power.LAWS)

    recommendation, reasons = compute_rate(
        constants,
        previous=await key_rate(session, constants),
        inflation=await inflation(session, constants),
        emission_share=await _emission_share(session, constants, now=moment),
    )
    corridor = constants[R.BANK_COUNCIL_RATE_DEVIATION]
    if abs(rate - recommendation) > corridor:
        raise OutOfCorridor(
            key="bank-out-of-corridor",
            recommendation=recommendation,
            corridor=corridor,
            rate=rate,
        )
    rate_value = max(constants[R.BANK_RATE_FLOOR], min(constants[R.BANK_RATE_CAP], rate))

    #: The Council's explanation is its own line followed by the algorithm's:
    #: the vote is argued with what the formula advised, so the formula's
    #: clauses stand under it rather than inside it. Flat rather than nested
    #: because the reader is shown a list, and a clause that quotes a rendered
    #: sentence would freeze that sentence in one language (D-251 wave IV).
    said = [
        Says("bank-why-council", {"city": city.name, "advised": recommendation}),
        *reasons,
    ]
    why = i18n.written(said)
    decision = RateDecision(rate=rate_value, why_said=why, decided_at=moment)
    session.add(decision)
    await session.flush()
    await events.record(
        session,
        EventKind.RATE_DECIDED,
        rate=rate_value,
        advised=recommendation,
        by_council=True,
        city=city.name,
        why_said=why,
    )
    return decision
