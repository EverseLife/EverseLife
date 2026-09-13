# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The automat's energy (D-253, D-135, D-071): drawn on the spot for a command,
and for the tick forecast per machine, written down as a bill and drawn once
every machine has worked. On the same tab of a pass, the two stops the tick
reads once a node rather than once a machine: a node cut off for non-payment
(D-149) and a frozen one (D-231).

Lock order of the tick's draw (`pay`): the pools city by city, each after the
fuel its plants burn (`energy.produce`) -- the order `energy.tick_pools` takes
them in -- then the hulls' cells in hull order -- the order
`battery.tick_offgrid` takes them in -- then the owners' accounts in id order,
the order `ledger.post` locks debited accounts in. A bench takes its stacks,
then its pool, then its master's account (`energy.draw_for_work`): an account
taken between two pools would be that bench's account the other way round.

One hole the order does not close (OQ-174): a machine standing by a fuel
plant no longer eats its pile (D-342), but one that makes fuel folds it into
the pile (D-214) and holds that stack from its advance, out of the piles' own
order and before any pool. Whoever takes the piles in order can then wait on
the tick holding what the tick reaches for next: the energy step (an earlier
city's pool, an earlier plant yard of the same city, the rest of the pile),
the frost step's braziers on those piles, or a command drawing this city's
pool (a bench, a charge, a print, the meter) while it holds a stack a later
machine of the tick needs.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.engine import battery, energy, frost, ledger, utility
from src.models.automat import Automat as AutomatRow
from src.models.energy import EnergyPool
from src.models.inventory import Item
from src.models.ledger import AccountKind, LedgerAccount, PostingReason
from src.models.world import Node, Planet
from src.units import amount

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Bill:
    """Energy an automat worked for and has not drawn yet: the tick draws it last.

    Plain ids and numbers, not rows: a later machine's savepoint rolling back
    expires what it touched, and the bills are paid after all of them.
    """

    row_id: uuid.UUID
    owner_identity_id: uuid.UUID | None
    node_id: uuid.UUID
    #: What the energy comes out of: the city's grid node, or -- where no grid
    #: reaches -- the hull whose cells stand beside the machine
    #: (`battery.hull_of`). Two bills on one supply share what it holds.
    supply: uuid.UUID
    #: A city pool, as against cells: the draw takes every pool before any cell.
    grid: bool
    hours: float
    rate: float
    #: What the owner's purse was asked for at the forecast; nought off the grid.
    price: int


@dataclass
class Tab:
    """One pass of the tick: its bills, and what they took out of each supply and purse.

    Each supply and each purse is read once a pass, at the first machine that
    asks, and counted down by the bills from there -- a pool read and a purse
    summed per machine would cost the tick two queries a machine while it holds
    the stacks of every factory of the world. Whether a node is cut off, and
    whether it is warm, is read once a pass the same way (`cut_off`, `frozen`).
    The readings are taken from the database and outlive a machine's savepoint
    rolling back; the bills do not, and `keep` gives back what the dropped ones
    took.
    """

    bills: list[Bill] = field(default_factory=list)
    supplies: dict[uuid.UUID, float] = field(default_factory=dict)
    purses: dict[uuid.UUID, int] = field(default_factory=dict)
    #: The pool row behind a grid supply, for its tariff. A row, not a number,
    #: so the tariff is priced by `energy.price_at` like the draw's. No advance
    #: writes a pool, so a machine's savepoint rolling back leaves it as read;
    #: a pass run again after a moved purse starts a tab of its own.
    pools: dict[uuid.UUID, EnergyPool | None] = field(default_factory=dict)
    #: Whether a machine's node is cut off for non-payment (D-149), by that
    #: node: two floors of one house are two readings of the one meter below
    #: them (`utility.cut_off`), not two answers.
    cut_off: dict[uuid.UUID, bool] = field(default_factory=dict)
    #: Whether a machine's node is warm (D-231), by that node: the warmth is
    #: the place's -- a stove in it or beside it, a pool with energy -- and one
    #: answer serves every machine standing there.
    warm: dict[uuid.UUID, bool] = field(default_factory=dict)
    #: The climate of each planet a machine stands on (`frost.climate_of`): four
    #: of them in the world, and most machines stand where there is none.
    climates: dict[Planet, str | None] = field(default_factory=dict)

    def add(self, bill: Bill) -> None:
        self.bills.append(bill)
        self.supplies[bill.supply] -= bill.hours * bill.rate
        if bill.owner_identity_id is not None and bill.price > 0:
            self.purses[bill.owner_identity_id] -= bill.price

    def keep(self, count: int) -> None:
        """Drop the bills written after the first `count`, giving back what they took."""
        for bill in self.bills[count:]:
            self.supplies[bill.supply] += bill.hours * bill.rate
            if bill.owner_identity_id is not None and bill.price > 0:
                self.purses[bill.owner_identity_id] += bill.price
        del self.bills[count:]


async def cut_off(session: AsyncSession, node: Node, tab: Tab | None) -> bool:
    """Whether the node is disconnected for non-payment (D-149): read, never locked.

    The meter is on no place of the tick's lock order, and the tick holds every
    factory's stacks while it asks. On the tick's tab it is read once a pass per
    node, like a supply; without a tab (a command) it is asked on the spot. A
    debt paid in the middle of a pass stands the node's later machines of that
    pass all the same -- the next tick works them.
    """
    if tab is None:
        return await utility.cut_off(session, node)
    if node.id not in tab.cut_off:
        tab.cut_off[node.id] = await utility.cut_off(session, node)
    return tab.cut_off[node.id]


async def frozen(
    session: AsyncSession, constants: Constants, node: Node, type_key: str, tab: Tab | None
) -> bool:
    """Whether the cold stops this machine in this node (D-231): read, never locked.

    `frost.works_here` in its two halves, because only one of them is the
    node's: what burns its own fuel works in any frost and asks nothing, and
    the rest ask whether the node is warm. That question reads the node's yard,
    its neighbours and its city's pool -- without a lock, and three or four
    queries deep, which the command's memory (`db.base.remember`) would save
    only until the next write, and the tick writes after every machine. So on
    the tick's tab it is asked once a pass per node, like the meter (`cut_off`),
    and not at all where the planet has no climate -- the ground there is warm
    (`frost.is_warm`), and its climate is read once a pass per planet; without
    a tab (a command) on the spot. A stove lit or a pool emptied in the middle
    of a pass reaches the node's later machines at the next tick.

    The name is the cold's, as the refusal's is (`frost.Frozen`), but the
    scorching planet answers the same: nothing cools a node there (D-230), and
    no machine that does not burn works in it.
    """
    if frost.burns_own_fuel(type_key):
        return False
    if tab is None:
        return not await frost.is_warm(session, constants, node)
    if node.id not in tab.warm:
        if node.planet not in tab.climates:
            tab.climates[node.planet] = await frost.climate_of(session, node)
        tab.warm[node.id] = tab.climates[node.planet] is None or await frost.is_warm(
            session, constants, node
        )
    return not tab.warm[node.id]


async def promise(
    session: AsyncSession,
    constants: Constants,
    row: AutomatRow,
    node: Node,
    worked: float,
    rate: float,
    *,
    now: datetime,
    tab: Tab,
) -> Bill | None:
    """The hours the energy will cover, asked without a lock. `None` -- none.

    The same two walls `draw` stands at, read as a forecast: what the supply
    holds less what this pass's earlier bills took out of it, and -- on the
    grid -- whether the owner's purse, less those bills, pays for the hours
    whole (an unpaid bill stops the machine, D-135). Nothing is locked, created
    or advanced: the pool is read as it stands, its production since its last
    count waiting for the draw.

    A forecast can still overshoot the draw. The purse is held to it: one that
    pays less at the draw sends the pass back (`run.tick_automats`). The supply
    is not -- a crafter drank the same pool between the two, or the heat of a
    cold city ate it. The machine keeps those hours: the draw takes what is
    there and bills only that, and the pool never goes below nought. Whether a
    consumer on an emptied pool stops at once or finishes what it began is an
    open point of the vault (`20-systems/12-energy.md`), and this is the
    tick's provisional answer, not a rule decided -- the field automaton's
    decision (D-339, still on its branch) answers the same race with a debt
    the next action waits out.
    """
    grid = await energy.grid_node(session, node)
    supply = battery.hull_of(node) if grid is None else grid.id
    pool = None
    if grid is not None:
        if supply not in tab.pools:
            tab.pools[supply] = await energy.pool_of(session, constants, node, create=False)
        pool = tab.pools[supply]
    if supply not in tab.supplies:
        if grid is None:
            tab.supplies[supply] = await battery.charge_in(session, constants, node, now=now)
        else:
            tab.supplies[supply] = 0.0 if pool is None else float(pool.stored)
    hours = min(worked, max(0.0, tab.supplies[supply]) / rate)
    if hours <= 0:
        return None
    price = 0
    owner = row.owner_identity_id
    if pool is not None and owner is not None:
        price = energy.price_at(constants, pool, hours * rate)
        if price > 0:
            if owner not in tab.purses:
                account = await ledger.find_account(session, AccountKind.IDENTITY, owner)
                tab.purses[owner] = (
                    0 if account is None else await ledger.balance(session, account.id)
                )
            if tab.purses[owner] < price:
                return None
    return Bill(
        row_id=row.id,
        owner_identity_id=owner,
        node_id=node.id,
        supply=supply,
        grid=grid is not None,
        hours=hours,
        rate=rate,
        price=price,
    )


@dataclass(frozen=True, slots=True)
class _Charge:
    """A bill's energy taken off its pool, its money not posted yet."""

    bill: Bill
    owner_identity_id: uuid.UUID
    city_id: uuid.UUID
    drawn: float
    price: int
    tariff: float


async def pay(
    session: AsyncSession, constants: Constants, bills: list[Bill], *, now: datetime
) -> set[uuid.UUID]:
    """Draw a pass's energy. Returns the owners whose purse could not pay.

    In three rounds, each in one order (the module's lock order): the pools --
    the energy taken, the money reckoned -- then the cells, then the money
    posted, account by account. A refused owner is named for the caller to take
    the pass back for (`run.tick_automats`); the rest are posted on, so one run
    names every purse that moved, not one purse a run. What a supply no longer
    holds is not taken and not billed: those hours were worked all the same.
    """
    grid = sorted((one for one in bills if one.grid), key=lambda one: (one.supply, one.row_id))
    cells = sorted((one for one in bills if not one.grid), key=lambda one: (one.supply, one.row_id))

    charges: list[_Charge] = []
    held: dict[uuid.UUID, EnergyPool] = {}
    for bill in grid:
        pool = held.get(bill.supply)
        if pool is None:
            node = await session.get(Node, bill.node_id, populate_existing=True)
            if node is None:  # pragma: no cover -- a node is never deleted
                continue
            pool = await energy.pool_of(session, constants, node)
            if pool is None:  # pragma: no cover -- the forecast saw this grid a moment ago
                continue
            await energy.produce(session, constants, pool, now=now)
            held[bill.supply] = pool
        hours = min(bill.hours, float(pool.stored) / bill.rate)
        _short(bill, hours)
        if hours <= 0:
            continue
        drawn = hours * bill.rate
        price = energy.price_at(constants, pool, drawn)
        energy.take_from_pool(pool, drawn)
        if price > 0 and bill.owner_identity_id is not None:
            charges.append(
                _Charge(
                    bill, bill.owner_identity_id, pool.node_id, drawn, price, float(pool.tariff)
                )
            )

    hulls: dict[uuid.UUID, list[Item]] = {}
    for bill in cells:
        if bill.supply not in hulls:
            node = await session.get(Node, bill.node_id, populate_existing=True)
            if node is None:  # pragma: no cover -- a node is never deleted
                continue
            hulls[bill.supply] = await battery.batteries_in(session, node)
        taken = await battery.drain_cells(
            session, constants, hulls[bill.supply], bill.hours * bill.rate, now=now
        )
        _short(bill, taken / bill.rate)
    await session.flush()

    refused: set[uuid.UUID] = set()
    accounts: dict[uuid.UUID, LedgerAccount] = {}
    treasuries: dict[uuid.UUID, LedgerAccount] = {}
    for charge in charges:
        if charge.owner_identity_id not in accounts:
            accounts[charge.owner_identity_id] = await ledger.account_for(
                session, AccountKind.IDENTITY, charge.owner_identity_id
            )
        if charge.city_id not in treasuries:
            treasuries[charge.city_id] = await ledger.account_for(
                session, AccountKind.CITY_TREASURY, charge.city_id
            )
    for charge in sorted(
        charges, key=lambda one: (accounts[one.owner_identity_id].id, one.bill.row_id)
    ):
        owner = charge.owner_identity_id
        try:
            await ledger.transfer(
                session,
                PostingReason.ENERGY_BILL,
                debit=accounts[owner].id,
                credit=treasuries[charge.city_id].id,
                amount=charge.price,
                memo={"energy": charge.drawn, "for": "automat", "tariff": charge.tariff},
            )
        except ledger.InsufficientFunds:
            #: Refused before a posting is written.
            refused.add(owner)
    await session.flush()
    return refused


def _short(bill: Bill, hours: float) -> None:
    if amount(max(0.0, hours) * bill.rate) < amount(bill.hours * bill.rate):
        log.debug(
            "automat %s: worked %.3f h on credit, the supply gave out after the forecast",
            bill.row_id,
            bill.hours - max(0.0, hours),
        )


async def draw(
    session: AsyncSession,
    constants: Constants,
    owner_identity_id: uuid.UUID | None,
    node: Node,
    worked: float,
    rate: float,
    *,
    now: datetime,
) -> float:
    """Cap the worked hours by energy and pay for them on the spot. Returns the hours.

    The draw of a command (`program`, `stop`), which advances one machine and
    holds nothing else. From the city pool at the tariff, billed to the owner
    (D-135: whoever burns pays, presence or not) -- or from the node's own
    batteries where no grid reaches (D-071): no pool, no tariff, the energy was
    bought when the battery was charged.
    """
    pool = await energy.pool_of(session, constants, node)
    if pool is None:
        taken = await battery.drain_batteries(session, constants, node, worked * rate, now=now)
        return taken / rate
    await energy.produce(session, constants, pool, now=now)
    can_hours = float(pool.stored) / rate
    worked = min(worked, can_hours)
    if worked <= 0:
        return 0.0
    drawn = worked * rate
    #: The forecast's own price (`promise`): two spellings of one tariff would
    #: one day let a machine through that the draw then refuses.
    price = energy.price_at(constants, pool, drawn)
    if price > 0 and owner_identity_id is not None:
        account = await ledger.account_for(session, AccountKind.IDENTITY, owner_identity_id)
        treasury = await ledger.account_for(session, AccountKind.CITY_TREASURY, pool.node_id)
        try:
            await ledger.transfer(
                session,
                PostingReason.ENERGY_BILL,
                debit=account.id,
                credit=treasury.id,
                amount=price,
                memo={"energy": drawn, "for": "automat", "tariff": float(pool.tariff)},
            )
        except ledger.InsufficientFunds:
            #: Whoever burns pays (D-135), and whoever cannot pay does not
            #: burn: the machine stands, the pool keeps its energy, and the
            #: command goes on -- an unpaid factory is an obligation broken,
            #: not a crash.
            return 0.0
    energy.take_from_pool(pool, drawn)
    await session.flush()
    return worked
