# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Customs: rate, norm and ban at the city border (D-123).

The authority's real task sounds like this: "our farmers cannot withstand
cheap imported bread" and "our workshops stand without ore because it was all
exported". A barrier does not solve this -- one needs a rate, a threshold and
different answers to different troubles. Hence four code-laws, two per
direction:

    import_duty / export_duty   rate % and duty-free norm kg
    import_ban  / export_ban    list of goods that do not pass at all

## The norm makes the duty targeted

A rate without a threshold hits everyone alike, and the first to suffer is
the newcomer who brought a sack of turnips for their dinner. The duty-free
norm separates **household carriage from trade**: carried less than the norm
within `trade.duty_free_window` -- paid nothing; carried ten norms -- paid for
everything above. The norm is counted per person and by the transit journal,
not per trip: otherwise it is dodged by splitting the cargo into ten runs.

## How the value is computed

The duty is a share of the **city book's reference price**: the median of
deals over `trade.reference_price_window`. **No deals -- no valuation, and no
duty is taken.** A city whose market is empty cannot tax what it does not
know the price of itself: first the market, then customs.

## Where the border runs

Between cities and "unowned" land. A transit counts as a crossing if the city
at entry and exit differs: a step inside your own city knows no customs,
going out beyond the wall does. Written off **when setting out**, when both
sides are already known: paying on arrival would let into the city what
cannot be paid for.

**Nothing to pay with -- the goods do not pass, no debt arises** (D-123). The
engine refuses the transit entirely: the decision what to drop is the
person's, not customs' on their behalf.

## Law format

The code-law's value is read in two ways, both honest:

* a number -- a rate on **everything**, no norm: "ten percent on everything
  that crosses the border";
* a map `{goods: {"rate": %, "free": kg}}` -- targeted by goods.

There is no branching on law name in the engine: `import_duty` and
`export_duty` are parsed by one code, only the direction differs.

## What we pay with

The per-person norm gets split among stand-in carriers, and this cannot be
cured fully. The barrier remains the same as everywhere: each mule is a live
person spending their own time on the transit. Smuggling **must** be possible,
otherwise customs stops being politics and becomes physics (D-123).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import events, gear, ledger, panel, world
from src.engine.errors import Refusal
from src.models.city import City
from src.models.event import Event, EventKind
from src.models.identity import Body
from src.models.inventory import Item
from src.models.ledger import AccountKind, PostingReason
from src.models.market import Trade
from src.models.world import Node
from src.telemetry.metrics import median
from src.units import MONEY_SCALE, PERCENT, amount_float, money_str

#: Directions. The law name is assembled from the direction rather than chosen by branching.
IMPORT = "import"
EXPORT = "export"


class CustomsError(Refusal):
    pass


class Banned(CustomsError):
    """Forbidden to carry. The city's extreme measure, and it is absolute."""


class CannotPay(CustomsError):
    """Nothing to pay the duty with. The goods do not pass, no debt arises (D-123)."""


@dataclass(frozen=True, slots=True)
class Charge:
    """What customs computed for one direction."""

    city: City | None
    direction: str
    duty: int = 0
    #: Goods -> how many kilograms above the norm were taxed.
    taxed: dict[str, float] = field(default_factory=dict)
    #: Goods -> how many kilograms passed at all: that is the summary line.
    moved: dict[str, float] = field(default_factory=dict)


def _law(catalog: Catalog, city: City, direction: str, kind: str) -> object:

    return _unpacked(town.law(catalog, city, f"{direction}_{kind}"))


def _unpacked(raw: object) -> object:
    """A law written as a table comes back as the text it was stored as.

    A law is text (`city.set_law`), and interpreting it belongs to the consumer
    -- which is here. The two table laws of customs are entered in the
    interface as a map of goods and a list of goods, travel as JSON and are
    stored as JSON; a number and a word are their own text and pass through
    untouched. Without this step the stored table was neither a dict nor a
    number, and the duty read as "no rates" -- the authority's decision
    silently did nothing.
    """
    if not isinstance(raw, str):
        return raw
    text = raw.strip()
    if not text.startswith(("{", "[")):
        return raw
    try:
        return json.loads(text)
    except ValueError:
        #: Not JSON after all: left as the text it is, and read as before.
        return raw


def banned(catalog: Catalog, city: City, direction: str) -> set[str]:
    """What this city does not let through in this direction."""
    raw = _law(catalog, city, direction, "ban")
    if raw is None:
        return set()
    if isinstance(raw, (list, tuple)):
        return {str(name).strip() for name in raw if str(name).strip()}
    text = str(raw).strip()
    if text.lower() in ("", "пусто", "нет", "-"):
        return set()
    return {piece.strip() for piece in text.split(",") if piece.strip()}


def rates(catalog: Catalog, city: City, direction: str) -> dict[str, dict[str, float]]:
    """Rate and norm by goods. An empty map -- no duty.

    The key `*` means "on everything": that is how a rate without goods breakdown is written.
    """
    raw = _law(catalog, city, direction, "duty")
    if raw is None:
        return {}
    if isinstance(raw, dict):
        out: dict[str, dict[str, float]] = {}
        for goods, condition in raw.items():
            if isinstance(condition, dict):
                rate = float(condition.get("rate", 0) or 0)
                norm = float(condition.get("free", 0) or 0)
            else:
                rate, norm = float(condition or 0), 0.0
            if rate > 0:
                out[str(goods)] = {"rate": rate, "free": norm}
        return out
    try:
        rate = float(str(raw).strip())
    except ValueError:
        return {}
    return {"*": {"rate": rate, "free": 0.0}} if rate > 0 else {}


async def reference_price(
    session: AsyncSession,
    constants: Constants,
    city: City,
    type_key: str,
    *,
    now: datetime | None = None,
) -> float | None:
    """The city book's reference price: the median of deals over the window (D-123).

    `None` -- there were no deals. That is not zero: no duty is taken off an unknown price.
    """

    moment = now or datetime.now(UTC)
    window = timedelta(hours=constants[R.TRADE_REFERENCE_PRICE_WINDOW])
    nodes = [node.id for node in await panel.city_nodes(session, city)]
    if not nodes:
        return None
    prices = (
        (
            await session.execute(
                select(Trade.price).where(
                    Trade.node_id.in_(nodes),
                    Trade.type_key == type_key,
                    Trade.at >= moment - window,
                )
            )
        )
        .scalars()
        .all()
    )
    if not prices:
        return None
    return median([int(price) for price in prices]) / MONEY_SCALE


async def moved_in_window(
    session: AsyncSession,
    constants: Constants,
    identity_id: uuid.UUID,
    city: City,
    direction: str,
    type_key: str,
    *,
    now: datetime | None = None,
) -> float:
    """How many kilograms of these goods the person has already carried within the norm window.

    Counted by the transit journal, not per trip: a norm that can be reset by
    splitting the cargo into ten runs is no norm (D-123).
    """
    moment = now or datetime.now(UTC)
    window = timedelta(hours=constants[R.TRADE_DUTY_FREE_WINDOW])
    lines = (
        (
            await session.execute(
                select(Event).where(
                    Event.kind == EventKind.CUSTOMS_CROSSED.value,
                    Event.actor_identity_id == identity_id,
                    Event.at >= moment - window,
                )
            )
        )
        .scalars()
        .all()
    )
    in_total = 0.0
    for line in lines:
        cargo = line.payload or {}
        if cargo.get("city") != str(city.id) or cargo.get("direction") != direction:
            continue
        in_total += float((cargo.get("moved") or {}).get(type_key, 0) or 0)
    return in_total


async def assess(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    city: City,
    direction: str,
    *,
    now: datetime | None = None,
) -> Charge:
    """Compute the duty for what the body carries. Writes off nothing.

    Forecast and write-off are computed by one code for the same reason as in
    craft: a diverged forecast is worse than none (D-092).
    """

    moment = now or datetime.now(UTC)
    ban = banned(catalog, city, direction)
    rate_table = rates(catalog, city, direction)

    pocket = await world.body_container(session, body)
    things = (
        (await session.execute(select(Item).where(Item.container_id == pocket.id))).scalars().all()
    )

    duty = 0.0
    taxed: dict[str, float] = {}
    carried_through: dict[str, float] = {}
    for thing in things:
        qty = amount_float(thing.amount)
        kilograms = gear.mass_of(catalog, thing.type_key, qty)
        carried_through[thing.type_key] = carried_through.get(thing.type_key, 0.0) + kilograms
        if thing.type_key in ban:
            raise Banned(
                key="customs-banned",
                goods=thing.type_key,
                city=city.name,
                direction=direction,
            )

        condition = rate_table.get(thing.type_key) or rate_table.get("*")
        if condition is None or kilograms <= 0:
            continue
        price = await reference_price(session, constants, city, thing.type_key, now=moment)
        if price is None:
            #: No deals -- no valuation. First the market, then customs (D-123).
            continue

        already = await moved_in_window(
            session,
            constants,
            body.identity_id,
            city,
            direction,
            thing.type_key,
            now=moment,
        )
        norm = condition["free"]
        over_kg = max(0.0, kilograms - max(0.0, norm - already))
        if over_kg <= 0:
            continue
        #: The taxed value is computed by the price per **unit**, and the norm
        #: in kilograms: the vault sets them exactly so, and converting one into
        #: the other goes through mass rather than swapping units (D-123, D-146).
        per_kilogram = price / (kilograms / qty) if qty > 0 else 0.0
        duty += over_kg * per_kilogram * condition["rate"] / PERCENT
        taxed[thing.type_key] = taxed.get(thing.type_key, 0.0) + over_kg

    return Charge(
        city=city,
        direction=direction,
        duty=int(round(duty * MONEY_SCALE)),
        taxed=taxed,
        moved=carried_through,
    )


async def cross(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    from_node: Node,
    to_node: Node,
    *,
    now: datetime | None = None,
) -> list[Charge]:
    """Take the body across the border: write off duties and record the carriage.

    Returns charges by direction. A refusal is an exception: customs does not
    decide for the person what to drop, it only does not let through.
    """

    moment = now or datetime.now(UTC)
    origin = await town.of_node(session, from_node)
    dest = await town.of_node(session, to_node)
    if (origin is None and dest is None) or (
        origin is not None and dest is not None and origin.id == dest.id
    ):
        #: A step inside your own city knows no customs.
        return []

    charges: list[Charge] = []
    if origin is not None:
        charges.append(await assess(session, constants, catalog, body, origin, EXPORT, now=moment))
    if dest is not None:
        charges.append(await assess(session, constants, catalog, body, dest, IMPORT, now=moment))

    in_total = sum(charge.duty for charge in charges)
    if in_total > 0:
        account = await ledger.account_for(session, AccountKind.IDENTITY, body.identity_id)
        remainder = await ledger.balance(session, account.id)
        if remainder < in_total:
            await events.record(
                session,
                EventKind.CUSTOMS_REFUSED,
                actor_identity_id=body.identity_id,
                node_id=from_node.id,
                duty=in_total,
                short=in_total - remainder,
            )
            raise CannotPay(
                key="customs-cannot-pay",
                duty=money_str(in_total),
                have=money_str(remainder),
            )

    for charge in charges:
        if charge.city is None:  # pragma: no cover -- the city exists by construction
            continue
        if charge.duty > 0:
            account = await ledger.account_for(session, AccountKind.IDENTITY, body.identity_id)
            treasury = await town.treasury(session, charge.city)
            await ledger.transfer(
                session,
                PostingReason.DUTY,
                debit=account.id,
                credit=treasury.id,
                amount=charge.duty,
                memo={
                    "таможня": charge.city.name,
                    "направление": charge.direction,
                    "обложено": charge.taxed,
                },
            )
        await events.record(
            session,
            EventKind.CUSTOMS_CROSSED,
            actor_identity_id=body.identity_id,
            node_id=from_node.id,
            city=str(charge.city.id),
            direction=charge.direction,
            duty=charge.duty,
            moved=charge.moved,
            taxed=charge.taxed,
        )
    return charges


async def traffic(
    session: AsyncSession,
    constants: Constants,
    city: City,
    *,
    since: datetime,
) -> dict:
    """Imports, exports and collected duty for the period -- the summary line (D-124).

    "Imported and exported by goods, in weight and trips" is the direct basis
    for a rate: one sees that bread is flowing in from outside.
    """

    lines = (
        (
            await session.execute(
                select(Event).where(
                    Event.kind == EventKind.CUSTOMS_CROSSED.value, Event.at >= since
                )
            )
        )
        .scalars()
        .all()
    )

    imports: dict[str, float] = {}
    exports: dict[str, float] = {}
    walker = {IMPORT: 0, EXPORT: 0}
    collected = 0
    for line in lines:
        cargo = line.payload or {}
        if cargo.get("city") != str(city.id):
            continue
        dest = imports if cargo.get("direction") == IMPORT else exports
        walker[str(cargo.get("direction"))] = walker.get(str(cargo.get("direction")), 0) + 1
        for goods, kilograms in (cargo.get("moved") or {}).items():
            dest[goods] = dest.get(goods, 0.0) + float(kilograms or 0)
        collected += int(cargo.get("duty", 0) or 0)
    return {
        "imported": imports,
        "exported": exports,
        "trips_in": walker.get(IMPORT, 0),
        "trips_out": walker.get(EXPORT, 0),
        "duty_collected": collected / MONEY_SCALE,
    }
