# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The city's line and margin (D-175, D-283, D-285): a city lends to its own
at the key rate plus its margin, out of its own treasury -- and what it may
borrow from the capital to fill that treasury is measured the way a person is
measured, by trade, by interest paid and by trust.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import ledger
from src.engine.bank._base import key_rate
from src.engine.bank.trust import city_trust
from src.engine.errors import Says
from src.models.bank import Loan, LoanState
from src.models.event import Event, EventKind
from src.models.identity import Identity
from src.models.market import Trade
from src.models.world import Node
from src.units import PERCENT, amount_float, money, money_str

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
) -> tuple[float | None, list[Says]]:
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
        #: No rate at all rather than a worse one (D-281): nobody lends to a
        #: person belonging nowhere. Empty and not zero -- the window must say
        #: that before the button, and "0%" beside "no loan" reads as free money.
        return None, [Says("bank-why-offer-no-citizenship")]

    city = await town.by_id(session, entry.city_id)
    if city is None:  # pragma: no cover -- citizenship into nowhere is a bug
        return key, [Says("bank-why-offer-key", {"key": key})]

    _, _, free = await city_line(session, constants, city, now=moment)
    margin = city_margin(constants, catalog, city)
    #: The same arithmetic as `borrow`, and since D-283 that means the treasury
    #: first: the city hands over its own money and goes to the capital only
    #: for the shortfall. A window that looked at the line alone told a citizen
    #: of a rich city with a spent line that there was no money for them, and
    #: then the loan went through -- the exact shape this function exists to
    #: prevent.
    own = await ledger.balance(session, (await town.treasury(session, city)).id)
    if amount <= own + free:
        return key + margin, [
            Says(
                "bank-why-offer-city",
                #: A city's name is already a word: it is written by whoever
                #: founded the city, not chosen from a catalogue, so it goes
                #: in plain and not through `NAME()`.
                {
                    "key": key,
                    "margin": margin,
                    "city": city.name,
                    "free": money_str(own + free),
                },
            )
        ]

    #: Neither its own nor borrowed: past that there is nothing at all (D-281,
    #: D-283). The rate answered is still the city's own -- it is the rate of
    #: the loan one may take for what the city can still find.
    return key + margin, [
        Says(
            "bank-why-offer-cannot-fund",
            {
                "key": key,
                "margin": margin,
                "city": city.name,
                "own": money_str(own),
                "free": money_str(free),
            },
        )
    ]


async def city_outstanding(session: AsyncSession, city) -> int:
    """How much this city owes the capital on its line.

    Its **own** borrowing, and only that (D-283): a citizen's loan is paid by
    the city out of its treasury and is the city's asset, not its debt. What
    the line bounds is how much a city may borrow from the capital -- for a
    works order (D-248) or to have something to lend its own people -- and how
    much of that it hands on is a matter for its treasury, not for the line.
    """
    result = await session.scalar(
        select(func.coalesce(func.sum(Loan.outstanding), 0)).where(
            Loan.city_id == city.id,
            Loan.identity_id.is_(None),
            Loan.state == LoanState.OPEN,
        )
    )
    return int(result or 0)


async def city_line(
    session: AsyncSession, constants: Constants, city, *, now: datetime | None = None
) -> tuple[int, int, int]:
    """City line: (permitted, occupied, free), in minor units.

    How much the city may owe the capital, by the same measure a person is
    lent by (D-285): a base, the trade on its land, the interest it has paid,
    and its own trust over all three. The city's debt outlives the authority
    (D-175): a change of ruler repays nothing, otherwise "borrow, hand out to
    your own, get re-elected" is the dominant strategy.
    """
    moment = now or datetime.now(UTC)
    window = moment - timedelta(days=constants[R.CREDIT_WINDOW])
    turnovers = await _turnover_by_city(session, window)
    turnover = turnovers.get(city.id, 0)
    #: The same formula a person is measured by (D-173, D-280, D-285), with the
    #: city put where the person stood: a base to start from, the trade that
    #: happened on its land, the interest it has actually paid -- all of it
    #: taken down by its own trust. Its own numbers, though: a city lends to
    #: others rather than to itself, so it starts from more than a person does.
    served = await city_interest_paid(session, city)
    earned = (
        money(constants[R.CREDIT_CITY_BASE])
        + int(turnover * constants[R.CREDIT_CITY_TURNOVER_SHARE] / PERCENT)
        + int(served * constants[R.CREDIT_CITY_INTEREST_SHARE] / PERCENT)
    )
    permitted = int(earned * await city_trust(session, constants, city.id, now=moment))
    occupied = await city_outstanding(session, city)
    return permitted, occupied, max(0, permitted - occupied)


async def city_interest_paid(session: AsyncSession, city) -> int:
    """Interest the city has paid the capital on its own loans -- ever (D-285).

    Its credit history, and it is written the way a citizen's is (D-280): by
    money that left for good, not by principal that merely went round.
    """
    total = await session.scalar(
        select(func.coalesce(func.sum(Loan.interest_paid), 0)).where(
            Loan.city_id == city.id, Loan.identity_id.is_(None)
        )
    )
    return int(total or 0)
