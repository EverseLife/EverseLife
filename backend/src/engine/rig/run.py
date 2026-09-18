# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The rig's clock: one pass that settles a rig up to now (`advance`) and the
world's tick that passes every rig at once (`tick_rigs`).

A pass has three limiters -- room in the hopper, fuel in the yard, the vein's
remainder -- and writes the vein, the hopper, the fuel and the wear. What it
writes it takes first, in the package's lock order (the door's docstring):
the vein, then the machine and the fuel of its yard in one statement
(`_held`); the tick takes all of it for the whole world before its first pass
(`_hold_the_world`).

Asks only the floor: the hands at the machine (`hands`) settle through this
room, never the other way round.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import ROUND_FLOOR

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import stock, wear, world
from src.engine.rig._base import _coal_available, _fuel_names, hopper_capacity
from src.models.inventory import Container, ContainerKind, Item
from src.models.rig import Rig as RigRow
from src.models.world import Node, Vein
from src.units import (
    ROUND_AMOUNT,
    ROUND_REMAINDER,
    SECONDS_PER_HOUR,
    amount,
    amount_float,
    on_grid,
)


async def advance(
    session: AsyncSession,
    constants: Constants,
    rig: RigRow,
    *,
    now: datetime | None = None,
) -> float:
    """Advance the rig up to "now". Returns what was mined in that time.

    Three limiters, and any of them stops the machine: room in the hopper,
    coal in the node and the vein's remainder. None is an error -- these are
    the enterprise's obligations.
    """
    moment = now or datetime.now(UTC)
    hours = (moment - rig.counted_at).total_seconds() / SECONDS_PER_HOUR
    if hours <= 0:
        return 0.0

    machine = await session.get(Item, rig.item_id)
    if machine is None:
        return await _bury_row(session, rig)
    vein = await session.get(Vein, rig.vein_id)
    if vein is None:  # pragma: no cover -- a vein outlives every rig upon it
        rig.counted_at = moment
        await session.flush()
        return 0.0

    #: Standing, and in its own node (D-278, D-314). A machine taken down, put
    #: on a counter, dropped by a demolition or fallen with its owner drills
    #: nothing -- the rule the automat keeps at this very place, and the one
    #: door the rig had not got. Only the stamp moves: the hopper, the vein and
    #: the slivers wait for it to be stood up again, and a stamp held back
    #: would mine the whole spell in the sack on the first pass after. Nor coal
    #: nor wear: the rig's wear is the work's (`rig_work`), and "an abandoned
    #: one falls apart" is said of a machine standing on its vein with nobody
    #: coming, not of one lying in a chest.
    yard = await world.node_container(session, await session.get(Node, rig.node_id))
    if not _stands(machine, yard.id):
        rig.counted_at = moment
        await session.flush()
        return 0.0

    #: The rig's output is set by the vault and does not depend on its
    #: condition: a worn machine does not dig less -- it digs **worse**, and
    #: that shows in ore quality on emptying (15-quality: the machine sets the ceiling).
    place = max(0.0, hopper_capacity(constants) - float(rig.hopper))
    output_per_hour = constants[R.RIG_OUTPUT_PER_HOUR]

    #: Coal: how many hours the rig could burn at all.
    fuel = constants[R.RIG_FUEL_PER_HOUR]
    coal = await _coal_available(session, yard.id)
    hours_by_fuel = coal / fuel if fuel > 0 else hours
    hours_by_bunker = place / output_per_hour if output_per_hour > 0 else 0.0
    hours_by_vein = (
        amount_float(vein.remaining) / (output_per_hour * constants[R.RIG_DEPLETION_MULTIPLIER])
        if output_per_hour > 0
        else 0.0
    )
    workers = max(0.0, min(hours, hours_by_fuel, hours_by_bunker, hours_by_vein))

    #: What this pass writes is taken before it writes any of it, in the
    #: package's lock order: the vein for a pass that drills, then the machine
    #: and its fuel in one statement. A tick's pass finds it all taken already.
    if workers > 0:
        await session.refresh(vein, with_for_update=True)
    held, stacks = await _held(session, rig.item_id, yard.id if workers > 0 else None)
    if held is None:
        #: Burnt, or fallen with the house, since the free read above -- and a
        #: write to a row that is gone throws out of `tick_rigs`, taking the
        #: pass of every rig in the world with it. The row ends as it does
        #: for a machine found gone; the free copy goes out of the session
        #: too, or whoever settles through here would read the machine back
        #: out of its memory (`empty_hopper`, `place`).
        session.expunge(machine)
        return await _bury_row(session, rig)
    machine = held
    if not _stands(machine, yard.id):
        rig.counted_at = moment
        await session.flush()
        return 0.0
    if workers > 0 and fuel > 0:
        #: The plan counted the coal free; the pass burns only what it holds.
        workers = min(workers, sum(amount_float(stack.amount) for stack in stacks) / fuel)

    mined = banked = 0.0
    if workers > 0:
        #: What the last pass raised and the hopper could not be credited with
        #: is added first. The hopper keeps thousandths, and a short pass
        #: raises less than one -- while the vein was emptied for it all the
        #: same, and by twice as much again, so the ore left the world and
        #: reached nobody. The sliver waits on the rig, not on the stamp:
        #: `counted_at` measures the wear as well, and holding it back would
        #: raise the same ore twice.
        raised = output_per_hour * workers + float(rig.hopper_remainder)
        banked = float(on_grid(raised, ROUND_AMOUNT, ROUND_FLOOR))
        #: Shared with the miners (`mining.swing`) and with any other rig on
        #: the same vein, and held since the plan above. The **roof** is not
        #: shared and is meant not to be (D-304): it is a mechanic of the
        #: swing -- a hidden number and a choice each time (D-143) -- and a
        #: machine that works by the clock has no swing to choose at, so
        #: reading `vein.roof` here would be the defect rather than the fix.
        #: A cave-in does not stop a rig and a support does not help one. The
        #: one place the two do meet is right below: richness is the ore
        #: body's, the roof is computed from it, and a rig that eats a vein
        #: twice as fast leaves every later working a kinder roof. The hours above were
        #: planned against a free read, and a plan may be stale -- so nothing
        #: the vein gives up is settled from that plan: it is all derived below,
        #: under the lock. The coal was capped under its own lock above.
        #: The hours were capped by what the vein holds, but the sliver from
        #: the last pass is added after that, so on the vein's last pass the
        #: raise can ask for a shade more than is left in the ground. Take what
        #: is there and no more, or the hopper is filled out of nothing -- the
        #: back of the very coin this fixes. And what the ground allows is not
        #: itself a whole thousandth: `rig.depletion_multiplier` is two, and a
        #: pickaxe leaves an odd remainder behind it, so the half goes back on
        #: the grid by the floor -- written to the hopper as it came, it would
        #: round up, and the ore nobody dug for would be back by another door.
        eats = constants[R.RIG_DEPLETION_MULTIPLIER]
        room = amount_float(vein.remaining) / eats
        banked = float(on_grid(min(banked, room), ROUND_AMOUNT, ROUND_FLOOR))
        #: The sliver waits for the next pass, but only as far as the ground
        #: can still cover it. What was asked for beyond that is ore nobody can
        #: ever hand over -- the vein has not got it -- so it is dropped rather
        #: than owed, and dropping it is what keeps this figure inside a column
        #: that cannot hold a whole unit. Without the cap the plan above, made
        #: on a free read, is enough on its own to overflow it: let a miner
        #: empty the vein in between, and the whole hour's raise lands here.
        #: That throw comes out of `tick_rigs`, which locks every rig in the
        #: world in one transaction -- one exhausted vein would stop that step
        #: for everybody's machines. The bound holds for any read, stale or fresh.
        rig.hopper_remainder = on_grid(
            max(0.0, min(raised - banked, room - banked)), ROUND_REMAINDER, ROUND_FLOOR
        )
        #: The vein gives up what was actually raised, not what was asked for.
        #: The machine eats it twice as fast: capital speeds up the world's depletion.
        from_vein = amount(banked * eats)
        before = vein.extracted
        vein.extracted += min(from_vein, vein.remaining)
        vein.remaining = max(0, vein.remaining - from_vein)
        _deplete(constants, vein, moment, before)
        rig.hopper = on_grid(float(rig.hopper) + banked, ROUND_AMOUNT)
        mined = banked

    #: Coal for the hours that actually raised something, not for the hours
    #: that went by. Fuel is written off in thousandths too, so a pass too
    #: short to raise a thousandth used to burn nothing -- which was harmless
    #: only while the ore was lost to the same rounding. Now the ore is kept,
    #: and charging fuel by elapsed time would leave a rig that is settled
    #: often raising ore for free: `rig.empty` settles it, and nothing
    #: throttles that. Ore and coal are spent by one measure, so the pass that
    #: banks the sliver pays the coal for every pass that saved it.
    if banked > 0 and output_per_hour > 0:
        #: And it too is written off in thousandths, while the coal a
        #: thousandth of ore costs is thinner than that again -- so what cannot
        #: be burned yet is owed and burned when it comes to one.
        owed_coal = fuel * banked / output_per_hour + float(rig.fuel_remainder)
        burns = float(on_grid(owed_coal, ROUND_AMOUNT, ROUND_FLOOR))
        rig.fuel_remainder = on_grid(max(0.0, owed_coal - burns), ROUND_REMAINDER, ROUND_FLOOR)
        if burns > 0:
            await stock.consume(session, stacks, amount(burns))

    #: Wear goes by the time it **stands**, not by what is mined: a rig with no
    #: coal, a full hopper or an eaten-out vein wears exactly as fast as a
    #: working one, and an abandoned one falls apart. Only a machine that
    #: stands wears at all -- the standing check above returns before this, so
    #: one taken into a chest is out of the weather (D-314). The automat
    #: differs here on purpose (D-253 charges it before its own standing
    #: check), and D-314 says which of the two this is.
    day = constants[R.TIME_DAY_TERRA]
    if hours > 0:
        await wear.spend(
            session,
            constants,
            machine,
            constants[R.RIG_WEAR_PER_DAY] * hours / day,
            cause="rig_work",
        )

    rig.counted_at = moment
    await session.flush()
    return mined


async def tick_rigs(
    session: AsyncSession, constants: Constants, *, now: datetime | None = None
) -> float:
    """Advance all rigs of the world. The machine does not sleep -- that is its whole strength."""
    moment = now or datetime.now(UTC)
    rigs = (
        (await session.execute(select(RigRow).order_by(RigRow.id).with_for_update()))
        .scalars()
        .all()
    )
    await _hold_the_world(session, rigs)
    result = 0.0
    for rig in rigs:
        result += await advance(session, constants, rig, now=moment)
    return result


# --- internal ----------------------------------------------------------------


async def _held(
    session: AsyncSession, machine_id: uuid.UUID, yard_id: uuid.UUID | None
) -> tuple[Item | None, list[Item]]:
    """The machine and, given its yard, the fuel lying there: locked in one
    statement by id and reread (the package's lock order). `None` for a machine
    whose row is gone; the stacks in id order, as `stock.consume` spends them.
    """
    wanted = Item.id == machine_id
    if yard_id is not None:
        wanted = or_(wanted, and_(Item.container_id == yard_id, Item.type_key.in_(_fuel_names())))
    rows = (
        (
            await session.execute(
                select(Item)
                .where(wanted)
                .order_by(Item.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    machine = next((row for row in rows if row.id == machine_id), None)
    return machine, [row for row in rows if row.id != machine_id]


async def _hold_the_world(session: AsyncSession, rigs: Sequence[RigRow]) -> None:
    """Take what every pass of this tick may write, before the first pass does:
    all the veins, then all the machines and the fuel of their yards, each in
    one statement by id (the package's lock order). More than a pass needs -- the
    vein of a rig with a full hopper -- and held to the end of the tick, where
    the passes' rows were held anyway; each pass takes its own again (`_held`).
    """
    if not rigs:
        return
    await session.execute(
        select(Vein.id)
        .where(Vein.id.in_({rig.vein_id for rig in rigs}))
        .order_by(Vein.id)
        .with_for_update()
    )
    yards = select(Container.id).where(
        Container.kind == ContainerKind.NODE,
        Container.owner_id.in_({rig.node_id for rig in rigs}),
    )
    await session.execute(
        select(Item.id)
        .where(
            or_(
                Item.id.in_({rig.item_id for rig in rigs}),
                and_(Item.container_id.in_(yards), Item.type_key.in_(_fuel_names())),
            )
        )
        .order_by(Item.id)
        .with_for_update()
    )


def _stands(machine: Item, yard_id: uuid.UUID) -> bool:
    """Put up in its own node's yard: the one state a rig drills in (D-278, D-314)."""
    return machine.installed and machine.container_id == yard_id


async def _bury_row(session: AsyncSession, rig: RigRow) -> float:
    """The machine is gone -- worn to nothing (`wear.spend` deletes what it
    finishes), burnt, fallen with the house. The enterprise ends with it, and
    so does the row: nothing else ever deleted one, and `tick_rigs` takes every
    row in the world under lock each pass, so an orphan is a lock the world
    pays for to the end of time. What the hopper still held goes too -- the ore
    was **inside** the machine (D-314). The automat buries its row the same way
    (D-253)."""
    await session.delete(rig)
    await session.flush()
    return 0.0


def _deplete(constants: Constants, vein: Vein, moment: datetime, extracted_before: int) -> None:
    """The vein depletes in the same tiers as from a pickaxe: one rule for all."""
    from src.engine.mining import (  # noqa: PLC0415 -- lazy: breaks the import cycle with mining
        deplete as by_general_rule,
    )

    by_general_rule(constants, vein, moment, extracted_before)
