# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The automat at work without the player (D-253): the advance that executes
the programme hour by hour, the tick that brings those hours, the energy
drawn and the wages paid out.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current_catalog
from src.constants import registry as R
from src.engine import battery, energy, events, ledger, liquid, stock, wear, world
from src.engine.automat._base import _EPS, LUBE
from src.engine.automat.wire import _chain_order
from src.engine.craft import Procedure, Unmakeable, procedure
from src.models.automat import Automat as AutomatRow
from src.models.automat import AutomatLink
from src.models.event import EventKind
from src.models.inventory import Container, Item
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Node
from src.units import (
    ENERGY_PER_TARIFF_UNIT,
    HOURS_PER_DAY,
    PERCENT,
    SECONDS_PER_HOUR,
    amount,
    amount_float,
    money,
)


async def advance(
    session: AsyncSession,
    constants: Constants,
    row: AutomatRow,
    *,
    catalog: Catalog | None = None,
    now: datetime | None = None,
) -> float:
    """Advance the automat up to "now". Returns the units paid out.

    Four limiters, and any of them stops the machine: lubricant in the
    node's vessels, energy in the pool (or the batteries), inputs on the
    yard, and -- for a liquid output -- room in a vessel. None is an error:
    these are the enterprise's obligations, exactly as with the rig.
    """
    moment = now or datetime.now(UTC)
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
    if worked > 0 and energy_rate > 0:
        worked = await _draw_energy(session, constants, row, node, worked, energy_rate, now=moment)

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
    """
    moment = now or datetime.now(UTC)
    rows = (await session.execute(select(AutomatRow).order_by(AutomatRow.id))).scalars().all()
    links = (await session.execute(select(AutomatLink))).scalars().all()
    made = 0.0
    for row in _chain_order(rows, links):
        made += await advance(session, constants, row, now=moment)
    return made


async def _draw_energy(
    session: AsyncSession,
    constants: Constants,
    row: AutomatRow,
    node: Node,
    worked: float,
    rate: float,
    *,
    now: datetime,
) -> float:
    """Cap the worked hours by energy and pay for them. Returns the hours.

    From the city pool at the tariff, billed to the owner (D-135: whoever
    burns pays, presence or not) -- or from the node's own batteries where no
    grid reaches (D-071): no pool, no tariff, the energy was bought when the
    battery was charged.
    """
    pool = await energy.pool_of(session, constants, node, lock=True)
    if pool is None:
        taken = await battery.drain_batteries(session, constants, node, worked * rate, now=now)
        return taken / rate
    await energy.produce(session, constants, pool, now=now)
    can_hours = float(pool.stored) / rate
    worked = min(worked, can_hours)
    if worked <= 0:
        return 0.0
    drawn = worked * rate
    price = money(drawn / ENERGY_PER_TARIFF_UNIT * float(pool.tariff))
    if price > 0 and row.owner_identity_id is not None:
        account = await ledger.account_for(session, AccountKind.IDENTITY, row.owner_identity_id)
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
            #: tick survives -- an unpaid factory is an obligation broken,
            #: not a worker crash.
            return 0.0
    energy.take_from_pool(pool, drawn)
    await session.flush()
    return worked


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
