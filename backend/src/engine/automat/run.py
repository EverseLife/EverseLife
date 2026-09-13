# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The automat at work without the player (D-253): the advance that executes
the programme hour by hour, the tick that brings those hours, the energy
drawn and the wages paid out.

Lock order: the automat's row, the yard's stacks (lubricant and inputs, one
query), the output's twins on the yard, and the energy **last** -- as a bench
takes its stacks before the pool it draws (`craft/batch/work.py`). The tick
holds every automat of the world in one transaction, so it takes no pool until
every machine has worked, and then all of them at once in one order (`bill.pay`):
a pool held while the next machine reached for a stack a crafter held would be
that crafter's pool the other way round, and the two would wait on each other.
The node's meter (D-149) is on no place of that order: it is only read.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current_catalog
from src.constants import registry as R
from src.db.base import forget
from src.engine import events, liquid, stock, utility, wear, world
from src.engine.automat import bill as energy_bill
from src.engine.automat._base import _EPS, LUBE
from src.engine.automat.wire import _chain_order
from src.engine.craft import Procedure, Unmakeable, procedure
from src.models.automat import Automat as AutomatRow
from src.models.automat import AutomatLink
from src.models.event import EventKind
from src.models.inventory import Container, Item
from src.models.world import Node
from src.units import (
    HOURS_PER_DAY,
    PERCENT,
    SECONDS_PER_HOUR,
    amount,
    amount_float,
)

log = logging.getLogger(__name__)


async def advance(
    session: AsyncSession,
    constants: Constants,
    row: AutomatRow,
    *,
    catalog: Catalog | None = None,
    now: datetime | None = None,
    tab: energy_bill.Tab | None = None,
) -> float:
    """Advance the automat up to "now". Returns the units paid out.

    Four limiters, and any of them stops the machine: lubricant in the
    node's vessels, energy in the pool (or the batteries), inputs on the
    yard, and -- for a liquid output -- room in a vessel. None is an error:
    these are the enterprise's obligations, exactly as with the rig.

    With a `tab` (the tick, which took the row already) the energy is not
    drawn but asked for without a lock and written down as a bill, for the
    tick to draw once every machine has worked; the bills already on the tab
    count as spent out of their supply and their owner's purse. Without, it is
    drawn here (`program`, `stop`).
    """
    moment = now or datetime.now(UTC)
    if tab is None:
        #: The row is taken for the transaction: the tick and an owner
        #: reprogramming race for the same backlog and stamp.
        await session.refresh(row, with_for_update=True)
    hours = (moment - row.counted_at).total_seconds() / SECONDS_PER_HOUR
    if hours <= 0:
        return 0.0
    book = catalog or current_catalog()

    machine = await session.get(Item, row.item_id)
    node = await session.get(Node, row.node_id)
    if machine is None:
        #: The machine is gone -- dismantled or worn to nothing. The row
        #: goes with it (the wires went with the machine itself, by CASCADE):
        #: a dead automat must not cost the tick a lock every pass.
        await session.delete(row)
        await session.flush()
        return 0.0
    #: Wear runs by the clock, worked or stood: an abandoned automat falls
    #: apart. Charged before the limiters, like the rig's -- and a machine
    #: the wear just finished does not work the window as a ghost.
    if await wear.spend(
        session,
        constants,
        machine,
        constants[R.AUTO_WEAR_PER_DAY] * hours / HOURS_PER_DAY,
        cause="automat_work",
    ):
        await session.delete(row)
        await session.flush()
        return 0.0
    if node is None or row.recipe_key is None:
        #: A row without a programme is a leftover of an older shape: the row
        #: is the working state, and a machine that works nothing has none.
        await session.delete(row)
        await session.flush()
        return 0.0
    yard = await world.node_container(session, node)
    if machine.container_id != yard.id or not machine.installed:
        #: Carried away from its node: a machine works only where it stands.
        row.counted_at = moment
        await session.flush()
        return 0.0
    if await utility.cut_off(session, node):
        #: Disconnected for non-payment (D-149): the machines of a node in debt
        #: do not work until the bill is paid -- the automat as much as a bench
        #: (`craft._internal._pick_station`), whatever feeds it, the city's
        #: pool or cells of its own. The hours pass as at any other stop, and
        #: the wear above ran through them. Asked of the meter by a read that
        #: locks nothing and writes nothing: the meter is on no lock order of
        #: this module, and the tick holds every factory's stacks meanwhile.
        row.counted_at = moment
        await session.flush()
        return 0.0

    try:
        proc = procedure(book, row.recipe_key)
    except Unmakeable:  # pragma: no cover -- the vault dropped a recipe mid-world
        row.counted_at = moment
        await session.flush()
        return 0.0

    share = constants[R.AUTO_SPEED_SHARE] / PERCENT
    unit_hours = (proc.step_hours / share) if share > 0 else 0.0
    if unit_hours <= 0:
        row.counted_at = moment
        await session.flush()
        return 0.0

    #: Everything the advance will touch, taken in ONE query and one lock
    #: order (stock.py: "one query and one lock order, never two"): the
    #: lubricant and every input, off the yard and the vessels in it. Split
    #: by name after the lock -- two queries would hold id=9 while waiting
    #: for id=3 against a crafter taking them the one true way round.
    lube_rate = constants[R.AUTO_LUBE_PER_HOUR]
    lube_names = set(world.station_names(LUBE))
    every_key = lube_names | set(proc.per_unit)
    by_name: dict[str, list[Item]] = {}
    for stack in await liquid.locked_stacks(session, book, yard, tuple(every_key)):
        by_name.setdefault(stack.type_key, []).append(stack)
    lube_stacks = [stack for name in sorted(lube_names) for stack in by_name.get(name, [])]
    lube_have = sum(amount_float(stack.amount) for stack in lube_stacks)
    lube_hours = (lube_have / lube_rate) if lube_rate > 0 else hours

    #: The backlog's own inputs are still unconsumed, so the cap counts them too.
    backlog = float(row.backlog)
    units_by_inputs = math.inf
    for name, per in proc.per_unit.items():
        if per <= 0:
            continue
        have = sum(amount_float(stack.amount) for stack in by_name.get(name, []))
        units_by_inputs = min(units_by_inputs, have / per)
    input_hours = max(0.0, (units_by_inputs - backlog) * unit_hours)

    #: A liquid output waits for room (D-230): the backlog holds the worked
    #: units, and work past the room would burn lubricant for nothing.
    is_liquid_out = book.recipes.is_liquid(proc.output)
    room_units = math.inf
    if is_liquid_out:
        unit_mass = book.recipes.mass_of(proc.output)
        room = 0.0
        for vessel in await liquid.vessels_in(session, book, yard):
            room += await liquid.free_in(session, book, vessel)
        room_units = (room / unit_mass) if unit_mass > 0 else math.inf
    room_hours = max(0.0, (room_units - backlog) * unit_hours) if is_liquid_out else hours

    worked = max(0.0, min(hours, lube_hours, input_hours, room_hours))

    #: Energy caps last (D-135): from the city pool at the tariff, billed to
    #: the owner -- or from the node's own batteries where no grid reaches.
    energy_rate = constants[R.AUTO_ENERGY_PER_HOUR]
    bill: energy_bill.Bill | None = None
    if worked > 0 and energy_rate > 0:
        if tab is None:
            worked = await energy_bill.draw(
                session, constants, row.owner_identity_id, node, worked, energy_rate, now=moment
            )
        else:
            bill = await energy_bill.promise(
                session, constants, row, node, worked, energy_rate, now=moment, tab=tab
            )
            worked = 0.0 if bill is None else bill.hours

    produced = 0.0
    if worked > 0:
        progress = backlog + worked / unit_hours
        cap = min(units_by_inputs, room_units)
        progress = min(progress, cap) if cap is not math.inf else progress
        if is_liquid_out or not book.recipes.counted(proc.output):
            paid = min(progress, room_units) if is_liquid_out else progress
        else:
            #: A piece is whole (D-212): the started one waits in the backlog.
            paid = float(math.floor(progress + _EPS))
        if paid > 0:
            await _pay_out(session, constants, book, row, machine, yard, proc, paid, by_name)
            produced = paid
            #: Told, not journaled (D-227), like a swing: the owner watching
            #: the floor sees the payout land without acting, and a thousand
            #: payouts a day stay out of the journal.
            if row.owner_identity_id is not None:
                await events.announce(
                    session,
                    touches=("node",),
                    identity_id=row.owner_identity_id,
                    event="automat.paid",
                    goods=proc.output,
                    made=amount_float(amount(paid)),
                )
        row.backlog = Decimal(str(max(0.0, progress - paid)))
        #: Lubricant burns for the hours worked, produced or not: the machine
        #: ran. Consumed after the payout maths so a refusal-free tick stays
        #: refusal-free.
        if lube_rate > 0:
            await stock.consume(session, lube_stacks, amount(lube_rate * worked))

    row.counted_at = moment
    await session.flush()
    #: Written down only once the machine's whole advance has gone through:
    #: a machine that fails after its forecast must not leave a bill behind.
    if bill is not None and tab is not None:
        tab.add(bill)
    return produced


async def tick_automats(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> float:
    """Advance all automats of the world.

    The machine does not sleep -- that is its whole strength. Within a node
    the wires set the order (D-253 wave 5): a producer advances before the
    consumer it feeds, so a chain flows within one pass instead of lagging a
    tick per stage. A cycle of wires falls back to id order -- harmless: the
    order is a courtesy, not a correctness rule.

    The machines are paid for after they have worked (`bill.pay`), and a purse can
    empty in between -- the owner's own bench or market order committing while
    the step runs. Whoever cannot pay does not burn (D-135), and a machine that
    has already worked cannot be taken back alone: its goods may already feed
    the next machine of the chain. So the whole pass goes back and runs again
    with that owner's purse taken as empty: all their machines on the tariff
    stand and their hours are gone, as when the forecast itself finds the purse
    short -- one outcome for "did not pay", whichever second the money left in,
    and stricter than a forecast that would still fund some of them with what
    is left: D-135 cuts the building off, not one machine. Hours kept for later
    would be a bank an owner fills by moving the money away before every draw.
    Their machines on cells or at a free tariff ask no purse and work on. A
    barred owner is never billed money again, so each run bars at least one
    owner more and the runs end -- while the tariff holds still: one raised
    between a forecast that saw it free and the draw costs one run more, and
    the run after it reads the new tariff at its forecast.
    """
    moment = now or datetime.now(UTC)
    rows = (await session.execute(select(AutomatRow).order_by(AutomatRow.id))).scalars().all()
    links = (await session.execute(select(AutomatLink))).scalars().all()
    #: Ids, not rows: a savepoint rolled back expires what it touched.
    order = [row.id for row in _chain_order(rows, links)]
    barred: set[uuid.UUID] = set()
    while True:
        try:
            async with session.begin_nested():
                return await _pass(session, constants, order, barred, now=moment)
        except _PurseMoved as moved:
            barred |= moved.owners
            _forget_the_run(session)
            log.info(
                "automats: %d purse(s) emptied under the step, the pass runs again with them empty",
                len(moved.owners),
            )


class _PurseMoved(Exception):
    """A purse the forecast found full could not pay the draw: the pass goes back."""

    def __init__(self, owners: set[uuid.UUID]) -> None:
        super().__init__(owners)
        self.owners = owners


async def _pass(
    session: AsyncSession,
    constants: Constants,
    order: list[uuid.UUID],
    barred: set[uuid.UUID],
    *,
    now: datetime,
) -> float:
    """One run over every machine, the energy drawn at its end. Returns the units paid out.

    Each machine works in a savepoint of its own: one whose programme the
    vault has since broken is logged and passed over, and the rest of the
    world's factories go on -- a failing step would stop them all, every tick,
    for one machine. A machine its owner holds right now (reprogramming it)
    is skipped for this tick rather than waited for: the tick already holds
    other machines' stacks, and the owner's command may be waiting for one of
    them. Its hours are not lost -- the next tick works them by the clock.

    The energy of all of them is drawn at the end, supply by supply in one
    order (`bill.pay`). Any other failure there rolls the whole step back,
    which loses nothing either: the stamps roll back with it.
    """
    #: A purse that already failed a draw this tick pays nothing in the rerun.
    tab = energy_bill.Tab(purses=dict.fromkeys(barred, 0))
    made = 0.0
    for row_id in order:
        owed = len(tab.bills)
        try:
            async with session.begin_nested():
                row = (
                    await session.execute(
                        select(AutomatRow)
                        .where(AutomatRow.id == row_id)
                        .with_for_update(skip_locked=True)
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one_or_none()
                if row is None:
                    continue
                paid = await advance(session, constants, row, now=now, tab=tab)
        except Exception as failure:  # noqa: BLE001 -- one machine must not stop the world's factories
            if isinstance(failure, DBAPIError) and failure.connection_invalidated:
                #: Not the machine's fault: the connection is gone, and every
                #: machine after this one would fail the same way.
                raise
            tab.keep(owed)
            _forget_the_run(session)
            log.exception("automat %s: the advance failed and was passed over", row_id)
            continue
        made += paid
    refused = await energy_bill.pay(session, constants, tab.bills, now=now)
    if refused:
        raise _PurseMoved(refused)
    return made


def _forget_the_run(session: AsyncSession) -> None:
    """Let nothing the rolled-back run remembered answer for the next one.

    A savepoint rolled back expires only the rows it wrote: a stack it deleted
    comes back with the numbers it had, and one it merely locked keeps them --
    while the locks themselves are gone, so a player may take from either
    before the next machine reads it. The amounts the tick writes it reads under
    a lock that rereads the row (`stock.locked_stacks`, `world.stack_up`,
    `energy.pool_of(lock=True)`), so a stale row misleads a forecast and not a
    remainder. One known exception, older than this tick: a liquid output
    measures a vessel's room off contents read without a reread
    (`liquid.fill`, `storage.stored_mass`), and can overfill it by what a hand
    poured in meanwhile. The session is not expired wholesale: it is the caller's
    too, and the job runner reads its own row after the step. What does go is
    the command's memory (`db.base.remember`), which only a write clears.
    """
    forget(session)


async def _pay_out(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    row: AutomatRow,
    machine: Item,
    yard: Container,
    proc: Procedure,
    paid: float,
    by_name: dict[str, list[Item]],
) -> None:
    """Consume the inputs for `paid` units and land the output on the yard.

    The stacks arrive already locked by the advance's single query -- asking
    again here would be the second lock order that door forbids. A liquid
    input was found without the payout knowing it reached into a canister
    (D-230). The output quality is the machine's ceiling: `auto.quality_cap`,
    lowered by wear -- the vein of the factory floor.
    """
    book = catalog.recipes
    for name, per in proc.per_unit.items():
        if per <= 0:
            continue
        await stock.consume(session, by_name.get(name, []), amount(per * paid))

    quality = min(constants[R.AUTO_QUALITY_CAP], wear.effective(constants, machine))
    fresh = Item(
        container_id=yard.id,
        type_key=proc.output,
        amount=amount(paid),
        quality=Decimal(str(quality)),
    )
    session.add(fresh)
    await session.flush()
    if book.is_liquid(proc.output):
        #: Into the vessels standing here (D-230). The room was counted under
        #: this transaction's locks; a pour that raced it anyway spills the
        #: difference with an event, exactly as a batch's liquid output does.
        spilled = await liquid.settle(session, catalog, fresh, (yard,))
        if spilled > 0:  # pragma: no cover -- a race the vessel locks make rare
            await events.record(
                session,
                EventKind.STORAGE_SPILLED,
                node_id=row.node_id,
                automat=str(row.id),
                spilled=spilled,
                goods=proc.output,
            )
    else:
        await world.stack_up(session, fresh)
