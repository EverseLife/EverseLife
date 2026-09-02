# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The battery: energy that stands where it is spent (D-071, D-179).

Split out of `engine/energy.py`, which crossed the length a file should have
when the ship's life support learned to draw charge (D-233). The seam is the
one that was already there: `energy` is about the **grid** -- a city pool that
fills by the hour, a tariff, a bill -- and this is about a **thing**, a cell
that is charged in a city and carried off to be spent where no grid reaches.

That is the whole of it, and it is why a ship needs it: away from a city, and
aboard a hull above all, a machine runs on the cells standing beside it. No
pool, no tariff and no bill -- the energy was bought when the battery was
charged.

Energy is perishable on purpose: a cell leaks `energy.battery_selfdischarge` a
day, so charge cannot be stockpiled for years and stays constant demand rather
than treasure.

`engine.energy` re-exports every name here, so `energy.charge_of` goes on
reading as it always did.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import events, ledger, stock, travel, world
from src.engine.errors import Refusal
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Node
from src.units import (
    ENERGY_PER_TARIFF_UNIT,
    PERCENT,
    SECONDS_PER_HOUR,
    amount_float,
    money,
)

#: The class of the cell (D-215). Behaviour binds to the class, so a second
#: kind of battery is a line in the vault.
BATTERY = "battery"


class BatteryError(Refusal):
    pass


class NotBattery(BatteryError):
    """Not a battery: energy does not lie in a sack."""


def _grid():
    """The city grid, imported late.

    `energy` re-exports everything here, so it imports this module at its top;
    the pool, the tariff and their two refusals travel back the other way. One
    edge of the cycle, and this is it.
    """
    from src.engine import energy  # noqa: PLC0415 -- lazy: breaks the cycle with energy

    return energy


def capacity(constants: Constants) -> float:
    return constants[R.ENERGY_BATTERY_CAPACITY]


def charge_of(constants: Constants, item: Item, *, now: datetime | None = None) -> float:
    """Battery charge with self-discharge -- by elapsed time.

    Energy is a perishable commodity: it cannot be stockpiled for years, and
    that makes it constant demand rather than treasure.
    """
    if item.charge is None:
        return 0.0
    moment = now or datetime.now(UTC)
    countdown = item.charged_at or item.created_at
    #: A day here is planetary, like all other terms of the world (D-008).
    hours_per_day = constants[R.TIME_DAY_TERRA]
    days = max(0.0, (moment - countdown).total_seconds() / SECONDS_PER_HOUR / hours_per_day)
    leaked = capacity(constants) * constants[R.ENERGY_BATTERY_SELFDISCHARGE] / PERCENT
    return max(0.0, float(item.charge) - leaked * days)


async def settle_charge(
    session: AsyncSession, constants: Constants, item: Item, *, now: datetime | None = None
) -> float:
    """Write into the battery its actual charge as of now."""
    moment = now or datetime.now(UTC)
    charge_ = charge_of(constants, item, now=moment)
    item.charge = Decimal(str(charge_))
    item.charged_at = moment
    await session.flush()
    return charge_


async def charge_battery(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    item: Item,
    amount_wanted: float | None = None,
    *,
    now: datetime | None = None,
) -> float:
    """Charge a battery from the city pool at the tariff.

    In person: charge is taken in the city and by hand. The taker pays -- into
    the city treasury: there is no free energy, and zero is a tariff too (D-085).

    Both the one in hand and the one standing here as a machine are charged
    (D-179): a battery is property of the place no less than a load.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise BatteryError(key="battery-dead-charges")
    await travel.require_here(session, body)

    if item.type_key not in world.station_names(BATTERY):
        raise NotBattery(key="battery-not-a-battery", goods=item.type_key)

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise BatteryError(key="battery-body-off-node")
    pocket = await world.body_container(session, body)
    yard = await world.node_container(session, node)
    if item.container_id not in (pocket.id, yard.id):
        raise BatteryError(key="battery-not-here")
    pool = await _grid().pool_of(session, constants, node, lock=True)
    if pool is None:
        raise _grid().NoGrid(key="battery-no-grid")
    await _grid().produce(session, constants, pool, now=moment)

    #: The cell's row before its charge is read and rewritten: a worn
    #: exoskeleton drinks from this very cell every tick (D-268), and a charge
    #: written over a drain the tick just committed would undo the drain.
    await session.refresh(item, with_for_update=True)
    have = await settle_charge(session, constants, item, now=moment)
    place = max(0.0, capacity(constants) - have)
    wants = place if amount_wanted is None else min(float(amount_wanted), place)
    will_give = min(wants, float(pool.stored))
    if will_give <= 0:
        raise _grid().NotEnough(key="battery-nothing-to-give", have=float(pool.stored), place=place)

    #: The tariff is given per hundred energy -- the bill is issued by it too.
    price = money(will_give / ENERGY_PER_TARIFF_UNIT * float(pool.tariff))
    if price > 0:
        account = await ledger.account_for(session, AccountKind.IDENTITY, body.identity_id)
        treasury = await ledger.account_for(session, AccountKind.CITY_TREASURY, pool.node_id)
        await ledger.transfer(
            session,
            PostingReason.ENERGY_BILL,
            debit=account.id,
            credit=treasury.id,
            amount=price,
            memo={"энергии": will_give, "тариф": float(pool.tariff)},
        )

    pool.stored = Decimal(str(float(pool.stored) - will_give))
    item.charge = Decimal(str(have + will_give))
    item.charged_at = moment
    await session.flush()

    await events.record(
        session,
        EventKind.ENERGY_CHARGED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(item.id),
        energy=will_give,
        paid=price,
        tariff=float(pool.tariff),
    )
    return will_give


async def batteries_in(session: AsyncSession, node: Node) -> list[Item]:
    """The batteries standing in this node, locked for the transaction.

    Locked because charge is a quantity of a shared thing (CLAUDE.md): the
    ship's life support draws from the same cells a crew member is unplugging
    to carry off. In id order, like every other write-off over a node's yard.
    """
    yard = await world.node_container(session, node)
    return await stock.locked_stacks(session, yard.id, world.station_names(BATTERY))


async def batteries_carried(session: AsyncSession, body: Body) -> list[Item]:
    """The batteries in this body's hands, locked for the transaction (D-268).

    The exoskeleton drinks from them by the hour, and the same cell may be
    handed over or put down in the same moment: locked in id order, like the
    yard's.
    """
    pocket = await world.body_container(session, body)
    return await stock.locked_stacks(session, pocket.id, world.station_names(BATTERY))


async def charged_carried(
    session: AsyncSession, constants: Constants, body: Body, *, now: datetime | None = None
) -> bool:
    """Whether any battery in the hands holds a charge -- what powers a worn exoskeleton."""
    moment = now or datetime.now(UTC)
    pocket = await world.body_container(session, body)
    cells = (
        await session.execute(
            select(Item).where(
                Item.container_id == pocket.id,
                Item.type_key.in_(world.station_names(BATTERY)),
            )
        )
    ).scalars()
    return any(charge_of(constants, cell, now=moment) > 0 for cell in cells)


async def drain_batteries(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    wanted: float,
    *,
    now: datetime | None = None,
) -> float:
    """Take charge out of the node's own batteries. Returns what was taken.

    The grid does not reach everywhere and is not meant to: away from a city --
    and aboard a ship above all (D-071, D-233) -- a machine runs on the cells
    standing beside it. No pool, no tariff and no bill: the energy was bought
    when the battery was charged, and it is spent where it stands.

    Takes what there is when there is not enough: the caller decides what a
    half-fed machine does, and for life support that is exactly the point --
    it makes less air rather than stopping dead.
    """
    return await drain_cells(session, constants, await batteries_in(session, node), wanted, now=now)


async def drain_carried(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    wanted: float,
    *,
    now: datetime | None = None,
) -> float:
    """Take charge out of the batteries in the hands (D-268). Returns what was taken."""
    return await drain_cells(
        session, constants, await batteries_carried(session, body), wanted, now=now
    )


async def drain_cells(
    session: AsyncSession,
    constants: Constants,
    cells: list[Item],
    wanted: float,
    *,
    now: datetime | None = None,
) -> float:
    """Take charge out of these locked cells, in order. Returns what was taken."""
    moment = now or datetime.now(UTC)
    if wanted <= 0:  # pragma: no cover -- callers ask for a positive draw
        return 0.0
    left = wanted
    taken = 0.0
    for cell in cells:
        if left <= 0:
            break
        have = await settle_charge(session, constants, cell, now=moment)
        #: A stack of batteries is a stack of full cells: the charge column is
        #: one cell's, and the amount says how many stand there (D-179).
        pile = amount_float(cell.amount)
        if have <= 0 or pile <= 0:
            continue
        spend = min(left, have * pile)
        #: Drawn evenly from the pile, so a stack never splits into cells of
        #: different charge -- that is what would make it two stacks.
        cell.charge = Decimal(str(max(0.0, have - spend / pile)))
        cell.charged_at = moment
        left -= spend
        taken += spend
    if taken > 0:
        await session.flush()
    return taken
