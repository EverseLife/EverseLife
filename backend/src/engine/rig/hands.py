# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The rig at hand: standing it up on its vein (`place`), emptying its hopper
in person (`empty_hopper`, a liquid poured into the vessels held before the
pass, `_hold_vessels`), and what the taking-down door asks of the hopper
before it lets a machine go (`hopper_left`).

The emptying, and a placement of a machine that already has a row, settle
the rig through the pass (`run.advance`) before they write; every door here
keeps the package's lock order (the door's docstring): the rig row first, the
machine after it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current, current_catalog
from src.constants import registry as R
from src.engine import events, gear, liquid, station, travel, wear, world
from src.engine.rig._base import RIG, HopperNotEmpty, NoRig, NoRoom, NotYours, RigError
from src.engine.rig.run import advance
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.rig import Rig as RigRow
from src.models.world import Node, Vein
from src.units import (
    ROUND_AMOUNT,
    SCALE_MAX,
    SCALE_MIN,
    amount,
    amount_float,
    on_grid,
)


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
    #: thing gone is the world's answer, as at `station.place` (D-251).
    await world.lock_thing(session, item, gone=NoRig)
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

    #: A rig put up out of the hands leaves them without `move_stack`, so the
    #: rule of the knife is asked here (D-346), on the machine's row taken for
    #: the transaction -- after the rig's own row, in the tick's order. A bare
    #: lock is enough: the rule reads the place off the database, not off the
    #: instance.
    await session.execute(select(Item.id).where(Item.id == item.id).with_for_update())
    await world.require_not_taken_apart(session, item)

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
