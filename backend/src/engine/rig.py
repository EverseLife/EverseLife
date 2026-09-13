# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Drilling rig: continuous mining without the player (D-115).

The endgame of mining and, after the automatic machine, the second transition
from labour to capital. Built so as **not to kill the live miner**: the
machine loses to a human on every measure but one -- it does not sleep.

| | Human | Rig |
|---|---|---|
| Output | `mining.iron_per_hour` | `rig.output_per_hour`, noticeably less |
| Quality | by the vein, up to its richness | not above `rig.quality_cap` |
| Eats the vein | by what is mined | twice (`rig.depletion_multiplier`) |
| Requires presence | constantly | only to empty the hopper |

Craft mining remains the way to get **good ore**, the rig the way to get
**a lot of average**.

## Four obligations, and all of them require people

**Fuel.** `rig.fuel_per_hour` of coal from the node where the rig stands.
Ran out -- it stopped: hence a standing contract with a coal hauler rather
than "free ore".

**Emptying.** The hopper holds `rig.hopper_capacity` **hours of work**. Full
-- the rig stands until the owner (or their carter) comes and takes it. On
foot: matter moves only physically (D-047).

**Maintenance.** `rig.wear_per_day` of wear per day. An abandoned one falls
apart, and it is repaired by the same repair as any thing.

**Standing.** It works only put up on its vein (D-278, D-314). Taken down,
dropped by a demolition or fallen with its owner, it drills nothing: the row
waits for the machine and dies with it.

## Lock order

The rig row, then its vein, then the machine and the fuel of its yard in one
statement by id (`_held`), and the node last and unasked: the second write of
the row re-checks its keys and holds the node `FOR KEY SHARE`, which is why
the plot's holders take it `FOR NO KEY UPDATE` (`estate.hold_ground`). It is
the order of the fire and of a falling house: an eruption takes a field's
veins and then what lies in it (`plates.clock`), a fall the plot and then
what it buries (`estate.upkeep._bury`). `tick_rigs` holds every rig of the
world in one transaction, so it takes all the veins and then all the machines
and fuel before the first pass (`_hold_the_world`): one rig at a time, the
order held within a rig and not across two.

The doors keep it. `empty_hopper` takes the row, the vessels a liquid pours
into (`_hold_vessels`) and settles through `advance`; `station.take` takes
the node, the row (`hopper_left`) and then the machine; `place` the row, the
vein (`FOR KEY SHARE`) and then the machine. A first placement has no row to
lock; a rig stood up and taken down again between that empty select and the
machine's lock trips the unique `rig.item_id` rather than making a second row.

## What is not here yet

* **City licence and mining tax** (D-115): the rig occupies a node and is
  subject to the city -- from E3, together with the city itself;
* **Deep mines** with their energy draw (`energy.deep_mine_draw`): that is a
  separate mechanic, not a property of the rig.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current, current_catalog
from src.constants import registry as R
from src.engine import events, liquid, station, stock, travel, wear, world
from src.engine.errors import Refusal
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Container, ContainerKind, Item
from src.models.rig import Rig as RigRow
from src.models.world import Node, Vein
from src.units import (
    ROUND_AMOUNT,
    ROUND_REMAINDER,
    SCALE_MAX,
    SCALE_MIN,
    SECONDS_PER_HOUR,
    amount,
    amount_float,
    on_grid,
)

#: The rig thing class (D-215). A ladder milestone: reachable by the end of E2.75.
RIG = "rig"


def _fuel_names() -> tuple[str, ...]:
    """What the rig burns: every material with a fuel value (D-215).

    People haul the fuel -- that is the whole enterprise. The rig is a motor,
    not a generator: it eats `rig.fuel_per_hour` units whatever the material.
    """
    return tuple(current_catalog().recipes.fuels()) or ("coal",)


class RigError(Refusal):
    pass


class NoRig(RigError):
    pass


class NoRoom(RigError):
    """Nowhere to pour (D-252): a liquid hopper empties only into vessels with room."""


class NotYours(RigError):
    """Somebody else's rig: the hopper is emptied by the owner or their carter by contract."""


class HopperNotEmpty(RigError):
    """The hopper still holds ore of the vein it stood on: a rig moves empty (D-314)."""


def hopper_capacity(constants: Constants) -> float:
    """Hopper capacity in ore units: the vault sets it in **hours of work**."""
    return constants[R.RIG_HOPPER_CAPACITY] * constants[R.RIG_OUTPUT_PER_HOUR]


async def place(
    session: AsyncSession,
    body: Body,
    item: Item,
    vein: Vein,
    *,
    now: datetime | None = None,
) -> RigRow:
    """Place a rig on a vein. In person: a machine is placed by hand."""
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise RigError(key="rig-dead-works")
    await travel.require_here(session, body)

    if item.type_key not in world.station_names(RIG):
        raise NoRig(key="rig-not-a-rig", goods=item.type_key)
    if vein.node_id != body.node_id:
        raise RigError(key="rig-vein-not-here")
    #: Whose plot, asked by the door that stands things -- D-278 requires it of
    #: this very door. The rig never asked, and while a machine could only
    #: be stood out of the **hands** that was harmless -- 44 kg does not come
    #: off the floor without an exoskeleton. Standing one up off the floor
    #: (D-314) removes that accidental guard, and the row's owner moves to
    #: whoever stands it: without this door a passer-by would inherit a
    #: knocked-over rig on somebody's plot and haul its hopper away. On
    #: nobody's land this is true for everyone, and a wild vein stays open.
    node_here = await session.get(Node, body.node_id)
    if node_here is None:  # pragma: no cover -- a body without a node is a bug
        raise RigError(key="rig-vein-not-here")
    if not await station.may_build(session, body, node_here):
        raise NotYours(key="rig-node-not-yours")

    #: A machine that already has a row is being **put back up** -- taken down
    #: and brought here, or knocked off its vein by a demolition (D-314). The
    #: row is the enterprise, and it travels with the machine rather than with
    #: the vein: the hopper, the stamp and the slivers go on where they
    #: stopped. Taken under the transaction here, in the tick's own order
    #: (row, then the vein and the machine just below), so two hands standing
    #: one rig do not both re-point it.
    exists = (
        await session.execute(select(RigRow).where(RigRow.item_id == item.id).with_for_update())
    ).scalar_one_or_none()
    #: The vein before the machine (the lock order): writing the row points it
    #: at the vein, and the key it checks holds the vein `FOR KEY SHARE` at the
    #: flush -- taken there, after the machine, it crossed an eruption holding
    #: the field's veins and reaching for everything lying in it.
    await session.execute(
        select(Vein.id).where(Vein.id == vein.id).with_for_update(read=True, key_share=True)
    )
    #: Then the machine's own row, after the rig's (the lock order). The
    #: command read free whether it is in the hands or lying here, and a pick
    #: or a fire committed since shows only under this lock: written from that
    #: look, the machine stood up out of the pocket it had just gone into. A
    #: thing gone is the world's answer, said in words (D-314).
    try:
        await session.refresh(item, with_for_update=True)
    except InvalidRequestError as gone:
        raise NoRig(key="rig-machine-gone") from gone
    pocket = await world.body_container(session, body)
    floor = await world.node_yard(session, node_here)
    #: What stands is not stood up again: a machine is stood from the hands or
    #: off the floor (D-278), and off its vein it goes through the taking-down
    #: door, which asks for the hopper (D-308, D-314).
    lying = floor is not None and item.container_id == floor.id and not item.installed
    if item.container_id != pocket.id and not lying:
        raise RigError(key="station-not-in-hands")
    if exists is not None:
        #: Settle before the row moves. The machine does not stand, so this
        #: banks nothing and only brings the stamp up to now -- before it is
        #: stood up, which is what keeps the hours it lay from being mined.
        await advance(session, current(), exists, now=moment)
        #: The rock is read off the vein the machine stands on **now**, so ore
        #: of the old vein would come out of the hopper as the new one's. A rig
        #: moves empty, and back onto the same vein it moves loaded.
        if exists.vein_id != vein.id and float(exists.hopper) > 0:
            raise HopperNotEmpty(key="rig-hopper-not-empty", goods=item.type_key)

    #: The machine moves from the hands into the node: it is stationary by definition.
    yard = await world.node_container(session, node_here)
    item.container_id = yard.id
    #: And stands (D-278): a rig is put up on its vein the way a machine is put
    #: up in a house, and it drills only standing.
    item.installed = True

    if exists is not None:
        moved = exists.vein_id != vein.id
        exists.node_id = body.node_id
        exists.vein_id = vein.id
        #: Whoever puts it up is who placed it -- the words the field carries.
        #: A machine that changed hands would otherwise keep a hopper only its
        #: former owner could empty.
        exists.owner_identity_id = body.identity_id
        exists.counted_at = moment
        if moved:
            #: The slivers belong to where the machine was: the ore one is rock
            #: of the old vein and must not come out as the new one's, the coal
            #: one is a debt to the old yard. Both under a thousandth, which is
            #: the scale these columns round away in any case.
            exists.hopper_remainder = Decimal(0)
            exists.fuel_remainder = Decimal(0)
        rig = exists
    else:
        rig = RigRow(
            item_id=item.id,
            node_id=body.node_id,
            vein_id=vein.id,
            owner_identity_id=body.identity_id,
            hopper=Decimal(0),
            counted_at=moment,
        )
        session.add(rig)
    await session.flush()

    await events.record(
        session,
        EventKind.MINING_STARTED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        work="rig",
        rig=str(rig.id),
        vein=str(vein.id),
    )
    return rig


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
    #: module's lock order: the vein for a pass that drills, then the machine
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


async def empty_hopper(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    rig: RigRow,
    *,
    now: datetime | None = None,
) -> float:
    """Empty the hopper. In person and on foot: otherwise the machine stands.

    Quality by the vein, but **not above `rig.quality_cap`**: a human adapts to
    the seam, a machine works by its setting (D-058, D-115).

    A machine that does not stand is emptied all the same (D-314): D-278
    forbids a lying machine to work and to be programmed, and opening a hatch
    is neither. Without this the ore would be stuck for good in everything that
    knocks a rig down past the taking-down door -- a demolition, an owner's death.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise RigError(key="rig-dead-works")
    await travel.require_here(session, body)
    if rig.node_id != body.node_id:
        raise RigError(key="rig-not-here")
    if rig.owner_identity_id not in (None, body.identity_id):
        raise NotYours(key="rig-not-yours")

    #: Emptying is a write and races the world tick for the same row.
    await session.refresh(rig, with_for_update=True)
    into = await _hold_vessels(session, body, rig)
    await advance(session, constants, rig, now=moment)
    #: The row says which node it belongs to, and the machine may have left it
    #: without the row hearing: a counter takes an unsold machine off the yard
    #: (`market.counter`), and the row would go on offering its hopper to
    #: whoever stands where the rig used to be. The machine answers for itself
    #: -- in the yard, standing or lying, or in the hands of whoever came.
    machine = await session.get(Item, rig.item_id)
    if machine is None:
        #: The machine is gone, so there is nothing to open. `advance` above
        #: asked for the row's burial, but the refusal rolls this transaction
        #: back and takes the delete with it -- the row is buried by the next
        #: `tick_rigs`, which is where it belongs anyway.
        raise NoRig(key="rig-machine-gone")
    yard = await world.node_container(session, await session.get(Node, rig.node_id))
    pocket = await world.body_container(session, body)
    if machine.container_id not in (yard.id, pocket.id):
        #: Its own refusal, not the body's (`rig-not-here`): the carter is
        #: standing in the right place, and "the hopper is hauled out on foot"
        #: would send them walking after a machine that went onto a counter.
        raise RigError(key="rig-machine-elsewhere")
    taken = float(rig.hopper)
    if taken <= 0:
        return 0.0

    vein = await session.get(Vein, rig.vein_id)
    #: A rig without a vein should not happen; coal is the least-wrong stub.
    resource = vein.resource if vein else "coal"
    catalog = current_catalog()
    #: The hopper is emptied by hand, and hands are not bottomless: without a
    #: wagon it does not come out whole, and that is work for a carter (D-146).
    #: What fits is taken and the rest waits (D-314) -- the shape the liquid
    #: branch below has had since D-252. All or nothing made a **full** hopper
    #: impossible to empty at all: twelve hours of work is 60 kg of ore against
    #: `inventory.carry_mass` = 30, so the enterprise's own obligation fell due
    #: exactly when it could not be met. A liquid is exempt from the weighing:
    #: it goes into vessels, and a full canister already weighs its fill
    #: (D-230) -- there the carry limit judges the vessel.
    if not liquid.is_liquid(catalog, resource):
        from src.engine import gear  # noqa: PLC0415 -- lazy: breaks the import cycle with gear

        room = await gear.room_for(session, constants, catalog, body, resource)
        #: Only a bound that actually bites goes to the grid: a thing the
        #: catalog gives no mass has no bound at all, and infinity is not a
        #: number `on_grid` can round.
        if room < taken:
            taken = float(on_grid(room, ROUND_AMOUNT, ROUND_FLOOR))
        if taken <= 0:
            #: Not even a thousandth of room: the refusal is the carry door's
            #: own, so the three figures in it add up for whoever reads them.
            await gear.check_carry(session, constants, catalog, body, resource, float(rig.hopper))

    #: Three ceilings, and the lowest is taken: the vein gives no more than its
    #: richness, the machine no more than `rig.quality_cap` (it works by its
    #: setting), and a worn machine no more than its effective quality (D-129).
    quality = min(
        constants[R.RIG_QUALITY_CAP],
        max(SCALE_MIN, min(SCALE_MAX, float(vein.richness) if vein else SCALE_MIN)),
        wear.effective(constants, machine),
    )
    emptied = Item(
        container_id=pocket.id,
        type_key=resource,
        amount=amount(taken),
        quality=Decimal(str(quality)),
    )
    session.add(emptied)
    if liquid.is_liquid(catalog, resource):
        #: A liquid hopper is poured, not handed over (D-252): into the vessels
        #: in the hands first, then those standing in the node -- the same
        #: order as a batch's liquid output. What fits nowhere **stays in the
        #: hopper**: the well does not spill for a forgotten canister, it
        #: waits. Nothing poured at all is a refusal, so the trip is not
        #: silently for nothing.
        await session.flush()
        taken = await liquid.fill_vessels(session, catalog, emptied, into)
        if taken <= 0:
            raise NoRoom(key="rig-liquid-no-room", goods=resource)
        left = 0.0
        if emptied.container_id == pocket.id:
            #: The stack that went in whole lives inside a vessel now, and its
            #: amount may have grown by the twins it swallowed -- only the
            #: remainder still lying loose in the pocket reads as leftover.
            #: It must not outlive this call (D-230): back into the hopper as
            #: a number, not a stack on the ground.
            left = amount_float(emptied.amount)
            await session.delete(emptied)
        rig.hopper = Decimal(str(left))
    else:
        await world.stack_up(session, emptied)
        #: What the hands could not take stays in the hopper: the machine is
        #: emptied over as many trips as it takes, or in one by a carter (D-314).
        rig.hopper = on_grid(float(rig.hopper) - taken, ROUND_AMOUNT)
    await session.flush()

    await events.record(
        session,
        EventKind.MINING_LEFT,
        actor_identity_id=body.identity_id,
        node_id=rig.node_id,
        work="rig",
        rig=str(rig.id),
        got=taken,
        quality=quality,
    )
    return taken


async def _hold_vessels(session: AsyncSession, body: Body, rig: RigRow) -> list[Item]:
    """Lock the vessels a liquid hopper pours into, before the advance burns
    coal, and return those the wait left in place, in the pouring order: the
    hands first, then the node (D-252). Nothing for a hopper of anything else.

    A vessel before any stack, the order the automats' tick takes a yard in
    (`automat.run`): emptying burned the rig's coal and reached for a canister
    only to pour, while the tick held that canister and waited for the same
    coal -- a furnace automat on the floor burns it too -- and one of the two
    was killed as a deadlock. All of them at once, in id order, and the pour
    goes into these and no other: listing the node again at the pour would
    lock a canister put down since after the coal, and pour into one carried
    off meanwhile.
    """
    vein = await session.get(Vein, rig.vein_id)
    catalog = current_catalog()
    if vein is None or not liquid.is_liquid(catalog, vein.resource):
        return []
    pocket = await world.body_container(session, body)
    yard = await world.node_container(session, await session.get(Node, rig.node_id))
    vessels = await liquid.vessels_in(session, catalog, pocket)
    vessels += await liquid.vessels_in(session, catalog, yard)
    held = await liquid.lock_vessels(session, vessels)
    return [held[one.id].vessel for one in vessels if one.id in held and not held[one.id].moved]


async def hopper_left(session: AsyncSession, item: Item) -> float:
    """What this machine's hopper still holds. Nought for anything but a placed rig.

    Asked by the taking-down door, which must not let twelve hours of work ride
    off in the hands past the carry limit and past the carter the hopper exists
    for (D-181, D-314). The row is **locked**: the tick holds every rig row of
    the world in one uncommitted transaction, and an unlocked read would see
    the last committed hopper -- a nought where a whole pass already stands --
    and wave the loaded machine out through the very rule this asks for. The
    order is the tick's own (the rig row, then the machine's, `_held`), so
    whoever asks must ask **before** locking the machine's
    row: the taking-down door once locked the machine first, and a take-down
    arriving mid-pass waited here while the tick waited on the machine.
    """
    if item.type_key not in world.station_names(RIG):
        return 0.0
    held = (
        await session.execute(
            select(RigRow.hopper).where(RigRow.item_id == item.id).with_for_update()
        )
    ).scalar_one_or_none()
    return 0.0 if held is None else float(held)


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


async def status(session: AsyncSession, constants: Constants, node_id: uuid.UUID) -> list[dict]:
    """What stands in the node and in what condition -- for the location scene.

    A read: the hopper is shown as of the last tick (`counted_at`), the scene
    does not move the machine. Advancing here used to race the world tick
    and the emptying for the same row (review 2026-08-23).
    """
    rigs = (await session.execute(select(RigRow).where(RigRow.node_id == node_id))).scalars().all()
    if not rigs:
        return []
    #: One node for the whole list -- the rigs were selected by it. A read of
    #: the scene, so the yard is looked into and never made for the look.
    place = await session.get(Node, node_id)
    yard = None if place is None else await world.node_yard(session, place)
    coal_ = 0.0 if yard is None else await _coal_available(session, yard.id)
    out: list[dict] = []
    for rig in rigs:
        machine = await session.get(Item, rig.item_id)
        vein = await session.get(Vein, rig.vein_id)
        out.append(
            {
                "id": str(rig.id),
                #: The machine itself. Not the row's own state but the one fact
                #: the window cannot reach from here: whether the rig stands is
                #: read off this id among what stands (`bench`), and putting a
                #: lying one back up (D-314) needs the thing to name (D-225).
                "item": str(rig.item_id),
                #: And the vein it sits on. Two veins of one rock in a node
                #: make the resource ambiguous, so this is not derivable
                #: either (D-225) -- and standing a rig back up (D-314) puts it
                #: where it was, which is the only vein its loaded hopper may
                #: go back onto.
                "vein": str(rig.vein_id),
                "resource": vein.resource if vein else None,
                "hopper": float(rig.hopper),
                #: When the hopper was last counted: the world tick moves it.
                "counted_at": rig.counted_at.isoformat(),
                "capacity": hopper_capacity(constants),
                "full": float(rig.hopper) >= hopper_capacity(constants),
                "fuel": coal_,
                "hours_of_fuel": coal_ / constants[R.RIG_FUEL_PER_HOUR],
                "condition": float(machine.condition) if machine else 0.0,
                "vein_left": amount_float(vein.remaining) if vein else 0.0,
            }
        )
    return out


# --- internal ----------------------------------------------------------------


async def _coal_available(session: AsyncSession, container_id: uuid.UUID) -> float:
    stacks = (
        (
            await session.execute(
                select(Item).where(
                    Item.container_id == container_id,
                    Item.type_key.in_(_fuel_names()),
                )
            )
        )
        .scalars()
        .all()
    )
    return sum(amount_float(stack.amount) for stack in stacks)


async def _held(
    session: AsyncSession, machine_id: uuid.UUID, yard_id: uuid.UUID | None
) -> tuple[Item | None, list[Item]]:
    """The machine and, given its yard, the fuel lying there: locked in one
    statement by id and reread (the module's lock order). `None` for a machine
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
    one statement by id (the module's lock order). More than a pass needs -- the
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
