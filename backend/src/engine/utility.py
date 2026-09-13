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

**Lock order.** The meters in id order, then the pools city by city in grid
node order, each after the fuel its plants burn (`energy.produce`) -- the
order `energy.tick_pools` takes them in -- then the holders' accounts in id
order, the order `ledger.post` locks debited accounts in. That is the
automats' tick (`automat/bill.py`) with the meters in front. A bench takes its
pool and then its master's account (`energy.draw_for_work`); paying off a debt
takes the meter and then the account (`pay`); the city taking a node back
takes the meter and then the node (`city/land.py`), every meter it needs before
the first node when it takes back several (`reclaim_all`). A run that took an
account between two pools, or before a meter, would hold one of those the
other way round. A meter's debt is read and written only under the meter's
lock.

The run takes no lock on a household's node, and that rests on one rule:
**a meter's row is updated once a transaction.** Postgres re-checks a foreign
key when a row is updated again by the transaction that last wrote it, so a
second update would take the node `FOR KEY SHARE`, and the run would stand in
the way of whoever holds that node `FOR UPDATE` -- a plot changing hands --
and they in its. Two nodes the rule cannot keep out. A meter the run opens
itself (`ensure_meters`) is inserted, and the insert takes its node the same
way. And the city's own node: a pool brought up to now and then drawn is
updated twice, and takes it -- which is why a hand-over waits for the meter
before it holds any node.

The run holds its holders' purses while it posts into the treasuries, and a
treasury may be paying one of those holders at the same moment. Each credit
takes the other side's account `FOR KEY SHARE` through the entry's foreign
key, and that passes the `FOR NO KEY UPDATE` debits queue under
(`ledger.lock_accounts`), so the two do not wait on each other. One known hole
is shared with every draw from a pool: the one the automats' tick leaves open
(OQ-174, `automat/bill.py`) reaches the meter too -- it draws a city's pool.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current
from src.constants import registry as R
from src.engine import energy, events, ledger
from src.engine.errors import Refusal
from src.engine.jobs import enqueue, handler
from src.models.city import UtilityMeter
from src.models.energy import EnergyPool
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.job import Job, JobKind
from src.models.ledger import AccountKind, LedgerAccount, PostingReason
from src.models.world import Layer, Node, storey_of
from src.units import ENERGY_PER_TARIFF_UNIT, SECONDS_PER_HOUR, money, money_str


class UtilityError(Refusal):
    pass


class NothingDue(UtilityError):
    """Nothing to pay. Not an error, but not an action either."""


class NotEnoughMoney(UtilityError):
    """The account has less than the debt. Partial payment is payment too, but there is no zero."""


async def _grid_of(session: AsyncSession, node: Node) -> Node | None:
    """The grid node a meter on this node bills from. `None` -- no meter belongs here.

    Two conditions, both necessary: the node has an owner (identity or city)
    and the node is in the city grid. An unowned node produces no bill, and
    outside the grid there is no household in this sense -- there it is a battery.
    """
    if node.owner_identity_id is None and node.owner_city_id is None:
        return None
    return await energy.grid_node(session, node)


async def meter_of(
    session: AsyncSession, node: Node, *, create: bool = True, lock: bool = False
) -> UtilityMeter | None:
    """The node's meter. Created only where there is somebody to pay and
    something to pay from (`_grid_of`).

    `lock` takes the row for the transaction, read afresh: the debt on it is
    money, and whoever writes it reads it under the lock (CLAUDE.md).
    """
    if await _grid_of(session, node) is None:
        return None

    stmt = select(UtilityMeter).where(UtilityMeter.node_id == node.id)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    found = (await session.execute(stmt)).scalar_one_or_none()
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


async def _metered(session: AsyncSession, node: Node) -> Node:
    """The node the meter actually stands on (D-247).

    A storey has no household of its own: it is lit and heated by the house,
    and the bill for it comes to the plot below. Asked of the storey itself, the
    grid answered "no grid" -- and a workshop on the third floor kept working
    through a disconnection the ground floor was shut down by.
    """
    if storey_of(node) is None or node.parent_id is None:
        return node
    under = await session.get(Node, node.parent_id)
    return node if under is None else await _metered(session, under)


async def payer_of(session: AsyncSession, node: Node) -> str | None:
    """Who pays for this node: the holder, the city, or nobody. `None` -- no grid.

    The same three lines `bill` decides by, read from the outside. They are
    gathered here on purpose: "whose bill is this" is a question the player
    asks standing in the node, and the answer must not be reassembled from
    ownership fields in the client -- there it would drift away from the engine
    on the first change.
    """
    node = await _metered(session, node)
    if await energy.grid_node(session, node) is None:
        return None
    if node.owner_identity_id is not None:
        return PAYER_OWNER
    if node.owner_city_id is not None:
        return PAYER_CITY
    return PAYER_NOBODY


async def cut_off(session: AsyncSession, node: Node) -> bool:
    """Whether the node is disconnected for non-payment. Checked before machine work."""
    meter = await meter_of(session, await _metered(session, node), create=False)
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

    One meter's run (`run_meters`), taken in the same order.
    """
    moment = now or datetime.now(UTC)
    meter = await meter_of(session, node, lock=True)
    if meter is None:
        return 0
    accrued = await _settle(session, constants, [(meter, node)], now=moment)
    return accrued.get(meter.id, 0)


@dataclass(slots=True)
class _Reading:
    """One meter's household energy taken off its pool; its money and its row wait.

    What the holder's purse paid and what went onto the meter as debt are
    filled in by the money round, and the meter's row is written last
    (`_settle`).
    """

    meter: UtilityMeter
    node: Node
    #: The treasury the money goes to is the grid node's.
    grid_id: uuid.UUID
    released: float
    hours: float
    price: int
    paid: int = 0
    owed: int = 0


async def _settle(
    session: AsyncSession,
    constants: Constants,
    meters: list[tuple[UtilityMeter, Node]],
    *,
    now: datetime,
) -> dict[uuid.UUID, int]:
    """Bill locked meters for the time since each was counted. Returns the money
    accrued per meter, paid or owed.

    In three rounds (the module's lock order): the pools -- the energy written
    off, the money reckoned -- then the money posted, account by account, then
    each meter's row written once. Energy really leaves the pool: the meter
    does not invent the spend, it writes it off. The pool is empty -- what was
    in it is written off: a city without fuel cannot release what it does not
    have.
    """
    due: list[tuple[uuid.UUID, UtilityMeter, Node, float]] = []
    for meter, node in meters:
        hours = (now - meter.counted_at).total_seconds() / SECONDS_PER_HOUR
        if hours <= 0:
            continue
        grid = await _grid_of(session, node)
        if grid is None:
            continue
        due.append((grid.id, meter, node, hours))
    due.sort(key=lambda one: (one[0], one[1].id))

    readings: list[_Reading] = []
    held: dict[uuid.UUID, EnergyPool] = {}
    for grid_id, meter, node, hours in due:
        pool = held.get(grid_id)
        if pool is None:
            pool = await energy.pool_of(session, constants, node)
            if pool is None:  # pragma: no cover -- the grid was found a moment ago
                continue
            #: The city's fuel, then its pool's row: `produce` takes both.
            await energy.produce(session, constants, pool, now=now)
            held[grid_id] = pool
        released = min(draw_for(constants, node, hours), float(pool.stored))
        energy.take_from_pool(pool, released)
        price = energy.price_at(constants, pool, released)
        readings.append(_Reading(meter, node, grid_id, released, hours, price))
    await session.flush()

    #: A city node is maintained by the treasury: it does not pay itself in
    #: money, but pays with energy it could have sold (D-149).
    billed = [
        (one.node.owner_identity_id, one)
        for one in readings
        if one.node.owner_identity_id is not None and one.price > 0
    ]
    accounts: dict[uuid.UUID, LedgerAccount] = {}
    treasuries: dict[uuid.UUID, LedgerAccount] = {}
    for owner, one in billed:
        if owner not in accounts:
            accounts[owner] = await ledger.account_for(session, AccountKind.IDENTITY, owner)
        if one.grid_id not in treasuries:
            treasuries[one.grid_id] = await ledger.account_for(
                session, AccountKind.CITY_TREASURY, one.grid_id
            )
    billed.sort(
        key=lambda bill: (accounts[bill[0]].id, treasuries[bill[1].grid_id].id, bill[1].meter.id)
    )
    for owner, one in billed:
        try:
            await ledger.transfer(
                session,
                PostingReason.ENERGY_BILL,
                debit=accounts[owner].id,
                credit=treasuries[one.grid_id].id,
                amount=one.price,
                memo={"счётчик": one.node.key, "энергии": one.released},
            )
            one.paid = one.price
        except ledger.InsufficientFunds:
            #: Nothing to pay with -- the debt lands on the node, and the node is
            #: disconnected. Writing off "what there is" is not allowed: a half
            #: measure would leave the node working for free. Refused before a
            #: posting is written, and decided under the account's lock.
            one.owed = one.price

    accrued: dict[uuid.UUID, int] = {}
    for one in readings:
        meter, node = one.meter, one.node
        #: Every column of the row set before anything flushes it, and nothing
        #: after: the row is written once (the module's lock order).
        meter.counted_at = now
        meter.last_energy = Decimal(str(one.released))
        if node.owner_identity_id is None:
            await events.record(
                session,
                EventKind.UTILITY_METERED,
                node_id=node.id,
                energy=one.released,
                hours=one.hours,
                at_city_expense=True,
                worth=one.price,
            )
            continue
        if one.price <= 0:
            continue
        cut = one.owed > 0 and not meter.cut_off
        if one.owed > 0:
            meter.debt += one.owed
            meter.cut_off = True
        if cut:
            await events.record(
                session,
                EventKind.UTILITY_CUT_OFF,
                actor_identity_id=node.owner_identity_id,
                node_id=node.id,
                debt=meter.debt,
            )
        await events.record(
            session,
            EventKind.UTILITY_METERED,
            actor_identity_id=node.owner_identity_id,
            node_id=node.id,
            energy=one.released,
            hours=one.hours,
            paid=one.paid,
            debt=one.owed,
        )
        accrued[meter.id] = one.price
    await session.flush()
    return accrued


async def pay(
    session: AsyncSession,
    constants: Constants,
    identity: Identity,
    node: Node,
) -> int:
    """Pay off the node's debt and reconnect it. Remote: this is a payment.

    The owner may pay: other people's bills are paid by contract, not by the engine.
    The meter is taken before the account (the module's lock order), and its
    debt is read under that lock: a meter run adding to it meanwhile is
    waited for, not overwritten.
    """
    if node.owner_identity_id != identity.id:
        raise UtilityError(key="utility-node-not-yours")
    meter = await meter_of(session, node, create=False, lock=True)
    if meter is None or meter.debt <= 0:
        raise NothingDue(key="utility-nothing-due")

    pool = await energy.pool_of(session, constants, node, create=False)
    if pool is None:  # pragma: no cover -- a meter is created only in the grid
        raise UtilityError(key="utility-no-grid")

    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    treasury = await ledger.account_for(session, AccountKind.CITY_TREASURY, pool.node_id)

    debt = meter.debt
    try:
        await ledger.transfer(
            session,
            PostingReason.ENERGY_BILL,
            debit=account.id,
            credit=treasury.id,
            amount=debt,
            memo={"оплата долга": node.key},
        )
    except ledger.InsufficientFunds:
        #: Refused before a posting is written, by the balance read under the
        #: purse's lock. A balance read before that lock could promise money a
        #: purchase elsewhere had just spent, and the holder was then told the
        #: ledger's refusal instead of this one. What they have is read again
        #: under the lock the refused posting took and still holds -- the same
        #: figure, without leaning on the ledger's refusal carrying it.
        have = await ledger.balance(session, account.id)
        raise NotEnoughMoney(
            key="utility-not-enough-money",
            debt=money_str(debt),
            have=money_str(have),
        ) from None
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
    #: Places one holds, not every row that carries one's name (D-247). A sub-node
    #: -- a floor of one's house, a compartment of one's ship -- is part of the
    #: thing actually held and has no meter of its own: listed here it would be
    #: a holding with a household bill nobody ever issues, and a tall house
    #: would fill the table with them.
    nodes = (
        (
            await session.execute(
                select(Node).where(
                    Node.owner_identity_id == identity_id, Node.layer != Layer.LOCATION
                )
            )
        )
        .scalars()
        .all()
    )

    out: list[dict] = []
    for node in nodes:
        meter = await meter_of(session, node, create=False)
        #: The grid is a property of the place, not of a row existing in the
        #: database: the city pool is created on first need, while the node has bills from day one.
        online = await energy.grid_node(session, node) is not None
        pool = await energy.pool_of(session, constants, node, create=False)
        for_period = draw_for(constants, node, constants[R.ENERGY_METER_PERIOD])
        tariff = float(pool.tariff) if pool is not None else constants[R.ENERGY_TARIFF_DEFAULT]
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
        (
            await session.execute(
                select(Node).where(
                    Node.owner_identity_id.is_not(None) | Node.owner_city_id.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )
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
    """Walk all meters of the world. Returns the number of meters walked.

    First the missing ones are opened: a node may have been taken between
    passes. Then exactly the meters are walked, not the nodes -- there are as
    many of them as places in the world where there is somebody to pay.

    Every meter is taken first, in id order (the module's lock order): the run
    adds its bill to the debt it reads, and a payment landing between the read
    and the write would come back as debt. Then all of them are billed at once
    (`_settle`), every pool before any account.
    """
    moment = now or datetime.now(UTC)
    await ensure_meters(session, constants)
    meters = (
        (
            await session.execute(
                select(UtilityMeter)
                .order_by(UtilityMeter.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    #: The nodes afresh, after the meters and in a statement of their own:
    #: whoever hands a node back to the city takes its meter as well, so a
    #: holder read now is the one this bill belongs to. The rows `ensure_meters`
    #: loaded may be older, and a join under the meters' lock would read them
    #: from before the wait.
    nodes = {
        node.id: node
        for node in (
            await session.execute(
                select(Node)
                .join(UtilityMeter, UtilityMeter.node_id == Node.id)
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }
    walked: list[tuple[UtilityMeter, Node]] = []
    for meter in meters:
        node = nodes.get(meter.node_id)
        if node is None:  # pragma: no cover -- a meter without a node is a bug
            continue
        walked.append((meter, node))
    await _settle(session, constants, walked, now=moment)
    return len(walked)


def _period() -> timedelta:

    return timedelta(hours=current()[R.ENERGY_METER_PERIOD])


async def schedule_next(session: AsyncSession, after: datetime) -> None:
    """Queue the next pass. The key is the period number, not the call time:
    two processes deciding to queue the meter at once will queue one job."""

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

    listed = await run_meters(session, current(), now=job.run_at)
    await events.record(
        session,
        EventKind.UTILITY_METERED,
        kind_of_run="all",
        at=job.run_at.isoformat(),
        meters=listed,
    )
    await schedule_next(session, job.run_at)
