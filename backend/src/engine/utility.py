# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Meter: a node's household and the bill for it (D-135, D-149).

Energy stopped being only machine fuel: housing, warehouse and workshop spend
it simply by existing. The bill comes once every `energy.meter_period` hours,
and it is computed in one line:

    energy = area * energy.home_draw_per_m2 * hours
    money  = energy / 100 * city tariff

**Who pays is decided by the node's owner, and there are no other rules:**

| Node | Who pays |
|---|---|
| taken by a player | the holder |
| belongs to the city | the treasury: energy leaves the pool and is not sold |
| unowned | nobody: there is nobody to bill, and money has nowhere to vanish (I2) |

A city building exists for the city's GDP and brings it taxes; charging a
random visitor for it would mean charging twice. An authority that places a
workshop must understand that its treasury maintains it -- that is the
decision (D-149).

**Did not pay -- disconnected.** The debt stays on the node, its machines do
not work until payment. The engine may not take the node for debt: that is a
court decision, not arithmetic.

Outside a city there is no meter at all: there is no grid, and one works from
a battery.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import energy, events, ledger
from src.engine.jobs import handler
from src.models.city import UtilityMeter
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.job import Job, JobKind
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Node
from src.units import ENERGY_PER_TARIFF_UNIT, SECONDS_PER_HOUR, money, money_str


class UtilityError(Exception):
    pass


class NothingDue(UtilityError):
    """Nothing to pay. Not an error, but not an action either."""


class NotEnoughMoney(UtilityError):
    """The account has less than the debt. Partial payment is payment too, but there is no zero."""


async def meter_of(
    session: AsyncSession, node: Node, *, create: bool = True
) -> UtilityMeter | None:
    """The node's meter. Created only where there is somebody to pay and something to pay from.

    Two conditions, both necessary: the node has an owner (identity or city)
    and the node is in the city grid. An unowned node produces no bill, and
    outside the grid there is no household in this sense -- there it is a battery.
    """
    if node.owner_identity_id is None and node.owner_city_id is None:
        return None
    if await energy.grid_node(session, node) is None:
        return None

    found = (
        await session.execute(select(UtilityMeter).where(UtilityMeter.node_id == node.id))
    ).scalar_one_or_none()
    if found is not None or not create:
        return found

    meter = UtilityMeter(node_id=node.id)
    session.add(meter)
    await session.flush()
    return meter


#: Who the meter for a node is charged to. One value, three answers plus a
#: fourth: outside the city grid there is no meter at all.
PAYER_OWNER = "owner"
PAYER_CITY = "city"
PAYER_NOBODY = "nobody"


async def payer_of(session: AsyncSession, node: Node) -> str | None:
    """Who pays for this node: the holder, the city, or nobody. `None` -- no grid.

    The same three lines `bill` decides by, read from the outside. They are
    gathered here on purpose: "whose bill is this" is a question the player
    asks standing in the node, and the answer must not be reassembled from
    ownership fields in the client -- there it would drift away from the engine
    on the first change.
    """
    if await energy.grid_node(session, node) is None:
        return None
    if node.owner_identity_id is not None:
        return PAYER_OWNER
    if node.owner_city_id is not None:
        return PAYER_CITY
    return PAYER_NOBODY


async def cut_off(session: AsyncSession, node: Node) -> bool:
    """Whether the node is disconnected for non-payment. Checked before machine work."""
    meter = await meter_of(session, node, create=False)
    return meter is not None and meter.cut_off


def draw_for(constants: Constants, node: Node, hours: float) -> float:
    """How much energy the node's household eats in this many hours.

    Taken from area (D-135): light, heat and ventilation are counted in
    metres, not by the number of machines inside.
    """
    return float(node.area_m2) * constants[R.ENERGY_HOME_DRAW_PER_M2] * max(0.0, hours)


async def bill(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    *,
    now: datetime | None = None,
) -> int:
    """Issue the bill for the elapsed time. Returns the accrued money.

    Energy really leaves the pool: the meter does not invent the spend, it
    writes it off. The pool is empty -- what was in it is written off: a city
    without fuel cannot release what it does not have.
    """
    moment = now or datetime.now(UTC)
    meter = await meter_of(session, node)
    if meter is None:
        return 0

    hours = (moment - meter.counted_at).total_seconds() / SECONDS_PER_HOUR
    if hours <= 0:
        return 0

    pool = await energy.pool_of(session, constants, node)
    if pool is None:  # pragma: no cover -- the grid is checked in meter_of
        return 0
    await energy.produce(session, constants, pool, now=moment)

    need = draw_for(constants, node, hours)
    released = min(need, float(pool.stored))
    pool.stored = Decimal(str(float(pool.stored) - released))
    meter.counted_at = moment
    meter.last_energy = Decimal(str(released))

    price = money(released / ENERGY_PER_TARIFF_UNIT * float(pool.tariff))
    await session.flush()

    #: A city node is maintained by the treasury: it does not pay itself in
    #: money, but pays with energy it could have sold (D-149).
    if node.owner_identity_id is None:
        await events.record(
            session,
            EventKind.UTILITY_METERED,
            node_id=node.id,
            energy=released,
            hours=hours,
            at_city_expense=True,
            worth=price,
        )
        return 0

    if price <= 0:
        await session.flush()
        return 0

    account = await ledger.account_for(
        session, AccountKind.IDENTITY, node.owner_identity_id
    )
    treasury = await ledger.account_for(session, AccountKind.CITY_TREASURY, pool.node_id)
    remainder = await ledger.balance(session, account.id)

    if remainder >= price:
        await ledger.transfer(
            session,
            PostingReason.ENERGY_BILL,
            debit=account.id,
            credit=treasury.id,
            amount=price,
            memo={"счётчик": node.key, "энергии": released},
        )
        paid_for = price
        accrued = 0
    else:
        #: Nothing to pay with -- the debt lands on the node, and the node is
        #: disconnected. Writing off "what there is" is not allowed: a half
        #: measure would leave the node working for free.
        paid_for = 0
        accrued = price
        meter.debt += price
        if not meter.cut_off:
            meter.cut_off = True
            await events.record(
                session,
                EventKind.UTILITY_CUT_OFF,
                actor_identity_id=node.owner_identity_id,
                node_id=node.id,
                debt=meter.debt,
            )
    await session.flush()

    await events.record(
        session,
        EventKind.UTILITY_METERED,
        actor_identity_id=node.owner_identity_id,
        node_id=node.id,
        energy=released,
        hours=hours,
        paid=paid_for,
        debt=accrued,
    )
    return price


async def pay(
    session: AsyncSession,
    constants: Constants,
    identity: Identity,
    node: Node,
) -> int:
    """Pay off the node's debt and reconnect it. Remote: this is a payment.

    The owner may pay: other people's bills are paid by contract, not by the engine.
    """
    if node.owner_identity_id != identity.id:
        raise UtilityError("узел не ваш: чужие счета оплачивает договор, а не движок")
    meter = await meter_of(session, node, create=False)
    if meter is None or meter.debt <= 0:
        raise NothingDue("долга нет")

    pool = await energy.pool_of(session, constants, node, create=False)
    if pool is None:  # pragma: no cover -- a meter is created only in the grid
        raise UtilityError("здесь нет городской сети")

    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    treasury = await ledger.account_for(session, AccountKind.CITY_TREASURY, pool.node_id)
    remainder = await ledger.balance(session, account.id)
    if remainder < meter.debt:
        raise NotEnoughMoney(
            f"долг {money_str(meter.debt)} ₭, а на счету {money_str(remainder)} ₭"
        )

    debt = meter.debt
    await ledger.transfer(
        session,
        PostingReason.ENERGY_BILL,
        debit=account.id,
        credit=treasury.id,
        amount=debt,
        memo={"оплата долга": node.key},
    )
    meter.debt = 0
    meter.cut_off = False
    await session.flush()

    await events.record(
        session,
        EventKind.UTILITY_PAID,
        actor_identity_id=identity.id,
        node_id=node.id,
        paid=debt,
    )
    return debt


async def holdings(
    session: AsyncSession, constants: Constants, identity_id: uuid.UUID
) -> list[dict]:
    """Own nodes and their bills. Remote: holdings are visible from anywhere.

    An empty list is not "the panel broke" but "no holdings": that is enough
    for the client not to show the section at all.
    """
    nodes = (
        await session.execute(select(Node).where(Node.owner_identity_id == identity_id))
    ).scalars().all()

    out: list[dict] = []
    for node in nodes:
        meter = await meter_of(session, node, create=False)
        #: The grid is a property of the place, not of a row existing in the
        #: database: the city pool is created on first need, while the node has bills from day one.
        online = await energy.grid_node(session, node) is not None
        pool = await energy.pool_of(session, constants, node, create=False)
        for_period = draw_for(constants, node, constants[R.ENERGY_METER_PERIOD])
        tariff = (
            float(pool.tariff) if pool is not None else constants[R.ENERGY_TARIFF_DEFAULT]
        )
        out.append(
            {
                "node": node.key,
                "name": node.name,
                "area": float(node.area_m2),
                #: No grid -- the node lives from a battery, and has no utility
                #: relations at all.
                "grid": online,
                "energy_per_period": round(for_period, 1) if online else 0.0,
                "cost_per_period": (
                    money(for_period / ENERGY_PER_TARIFF_UNIT * tariff) if online else 0
                ),
                "debt": 0 if meter is None else meter.debt,
                "cut_off": bool(meter is not None and meter.cut_off),
                "last_energy": 0.0 if meter is None else float(meter.last_energy),
            }
        )
    return out


async def ensure_meters(session: AsyncSession, constants: Constants) -> int:
    """Open a meter for every occupied node in the grid. Returns the number opened.

    A node may have been taken or allotted between passes, and a meter that
    opens only on the first bill would never open: there is nowhere for the
    first bill to come from. The conditions are the same as in `meter_of`:
    owner and grid.
    """
    occupied_ = (
        await session.execute(
            select(Node).where(
                Node.owner_identity_id.is_not(None) | Node.owner_city_id.is_not(None)
            )
        )
    ).scalars().all()
    opened = 0
    for node in occupied_:
        # The second call opens the meter, and it is reached only by one that
        # has none: `and` does not evaluate the right side while the left is false.
        if (
            await meter_of(session, node, create=False) is None
            and await meter_of(session, node) is not None
        ):
            opened += 1
    return opened


async def run_meters(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> int:
    """Walk all meters of the world. Returns the number of bills issued.

    First the missing ones are opened: a node may have been taken between
    passes. Then exactly the meters are walked, not the nodes -- there are as
    many of them as places in the world where there is somebody to pay.
    """
    moment = now or datetime.now(UTC)
    await ensure_meters(session, constants)
    meters = (await session.execute(select(UtilityMeter))).scalars().all()
    listed = 0
    for meter in meters:
        node = await session.get(Node, meter.node_id)
        if node is None:  # pragma: no cover -- a meter without a node is a bug
            continue
        await bill(session, constants, node, now=moment)
        listed += 1
    return listed


def _period() -> timedelta:
    from src.constants import current

    return timedelta(hours=current()[R.ENERGY_METER_PERIOD])


async def schedule_next(session: AsyncSession, after: datetime) -> None:
    """Queue the next pass. The key is the period number, not the call time:
    two processes deciding to queue the meter at once will queue one job."""
    from src.engine.jobs import enqueue

    period = _period()
    run_at = after + period
    number = int(run_at.timestamp() // period.total_seconds())
    await enqueue(
        session,
        JobKind.UTILITY_METER,
        run_at,
        dedup_key=f"utility.meter:{number}",
    )


async def ensure_scheduled(session: AsyncSession, now: datetime | None = None) -> None:
    """Make sure the meter ticks. Called together with the world clock."""
    from src.engine.jobs import enqueue

    moment = now or datetime.now(UTC)
    period = _period()
    number = int(moment.timestamp() // period.total_seconds())
    await enqueue(
        session,
        JobKind.UTILITY_METER,
        moment,
        dedup_key=f"utility.meter:{number}",
    )


@handler(JobKind.UTILITY_METER)
async def meter_tick(session: AsyncSession, job: Job) -> None:
    """The bill for all nodes at once and the next pass in a period."""
    from src.constants import current

    listed = await run_meters(session, current(), now=job.run_at)
    await events.record(
        session,
        EventKind.UTILITY_METERED,
        kind_of_run="all",
        at=job.run_at.isoformat(),
        meters=listed,
    )
    await schedule_next(session, job.run_at)
