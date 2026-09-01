# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The city's line and margin (D-175): a city lends to its own at the key
rate plus its margin, inside a line measured by the city's turnover.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine.bank._base import key_rate
from src.engine.errors import Says
from src.models.bank import Loan, LoanState
from src.models.event import Event, EventKind
from src.models.identity import Identity
from src.models.market import Trade
from src.models.world import Node
from src.units import PERCENT, amount_float, money_str

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
) -> tuple[float, list[Says]]:
    """The rate this borrower would actually get, and why (D-193).

    The same arithmetic as `borrow`, only without taking the money: a rate that
    turns up after the fact reads as a swindle even when it is computed right.
    The "why" is a message, not a sentence (D-251 wave IV) -- the reader's
    language decides how it reads.
    """

    moment = now or datetime.now(UTC)
    key = await key_rate(session, constants)
    entry = await town.citizenship(session, who.id)
    if entry is None:
        premium = constants[R.BANK_RISK_PREMIUM].max
        return key + premium, [
            Says("bank-why-offer-no-citizenship", {"key": key, "premium": premium})
        ]

    city = await town.by_id(session, entry.city_id)
    if city is None:  # pragma: no cover -- citizenship into nowhere is a bug
        return key, [Says("bank-why-offer-key", {"key": key})]

    permitted, _, free = await city_line(session, constants, city, now=moment)
    if amount <= free:
        margin = city_margin(constants, catalog, city)
        return key + margin, [
            Says(
                "bank-why-offer-city",
                #: A city's name is already a word: it is written by whoever
                #: founded the city, not chosen from a catalogue, so it goes
                #: in plain and not through `NAME()`.
                {"key": key, "margin": margin, "city": city.name, "free": money_str(free)},
            )
        ]

    premium = constants[R.BANK_RISK_PREMIUM].max
    return key + premium, [
        Says(
            "bank-why-offer-line-exhausted",
            {
                "key": key,
                "premium": premium,
                "city": city.name,
                "permitted": money_str(permitted),
                "free": money_str(free),
            },
        )
    ]


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
