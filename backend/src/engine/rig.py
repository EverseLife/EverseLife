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

## Three obligations, and all three require people

**Fuel.** `rig.fuel_per_hour` of coal from the node where the rig stands.
Ran out -- it stopped: hence a standing contract with a coal hauler rather
than "free ore".

**Emptying.** The hopper holds `rig.hopper_capacity` **hours of work**. Full
-- the rig stands until the owner (or their carter) comes and takes it. On
foot: matter moves only physically (D-047).

**Maintenance.** `rig.wear_per_day` of wear per day. An abandoned one falls
apart, and it is repaired by the same repair as any thing.

## What is not here yet

* **City licence and mining tax** (D-115): the rig occupies a node and is
  subject to the city -- from E3, together with the city itself;
* **Deep mines** with their energy draw (`energy.deep_mine_draw`): that is a
  separate mechanic, not a property of the rig.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current_catalog
from src.constants import registry as R
from src.engine import events, liquid, stock, travel, wear, world
from src.engine.errors import Refusal
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
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

    exists = (
        await session.execute(select(RigRow).where(RigRow.item_id == item.id))
    ).scalar_one_or_none()
    if exists is not None:
        return exists

    #: The machine moves from the hands into the node: it is stationary by definition.
    node = await session.get(Node, body.node_id)
    yard = await world.node_container(session, node)
    item.container_id = yard.id
    #: And stands (D-278): a rig is put up on its vein the way a machine is put
    #: up in a house, and it drills only standing.
    item.installed = True

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
    vein = await session.get(Vein, rig.vein_id)
    if machine is None or vein is None:  # pragma: no cover -- the machine may have been dismantled
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
    yard = await world.node_container(session, await session.get(Node, rig.node_id))
    coal = await _coal_available(session, yard.id)
    hours_by_fuel = coal / fuel if fuel > 0 else hours
    hours_by_bunker = place / output_per_hour if output_per_hour > 0 else 0.0
    hours_by_vein = (
        amount_float(vein.remaining) / (output_per_hour * constants[R.RIG_DEPLETION_MULTIPLIER])
        if output_per_hour > 0
        else 0.0
    )
    workers = max(0.0, min(hours, hours_by_fuel, hours_by_bunker, hours_by_vein))

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
        #: the same vein; same lock order: rig -> vein. The hours above were
        #: planned against a free read, and a plan may be stale -- so nothing
        #: the vein gives up is settled from that plan: it is all derived below,
        #: under this lock. The coal is another matter and not bounded here --
        #: `hours_by_fuel` is read free too, and `_burn` does not check what it
        #: actually got, so a yard raided for its coal mid-pass can leave the
        #: rig with ore it did not pay for. Older than this fix and left as it
        #: was found.
        await session.refresh(vein, with_for_update=True)
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
            await _burn(session, yard.id, burns)

    #: Wear goes by time, not by what is mined: an abandoned one falls apart.
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
    await advance(session, constants, rig, now=moment)
    taken = float(rig.hopper)
    if taken <= 0:
        return 0.0

    vein = await session.get(Vein, rig.vein_id)
    #: A rig without a vein should not happen; coal is the least-wrong stub.
    resource = vein.resource if vein else "coal"
    catalog = current_catalog()
    #: The hopper is emptied by hand, and hands are not bottomless: without a
    #: wagon the hopper cannot be emptied whole, and that is work for a carter
    #: (D-146). A liquid is exempt: it goes into vessels, and a full canister
    #: already weighs its fill (D-230) -- the carry limit judges the vessel.
    if vein is not None and not liquid.is_liquid(catalog, resource):
        from src.engine import gear  # noqa: PLC0415 -- lazy: breaks the import cycle with gear

        await gear.check_carry(session, constants, catalog, body, resource, taken)

    machine = await session.get(Item, rig.item_id)
    #: Three ceilings, and the lowest is taken: the vein gives no more than its
    #: richness, the machine no more than `rig.quality_cap` (it works by its
    #: setting), and a worn machine no more than its effective quality (D-129).
    quality = min(
        constants[R.RIG_QUALITY_CAP],
        max(SCALE_MIN, min(SCALE_MAX, float(vein.richness) if vein else SCALE_MIN)),
        wear.effective(constants, machine),
    )
    pocket = await world.body_container(session, body)
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
        node = await session.get(Node, rig.node_id)
        yard = await world.node_container(session, node)
        taken = await liquid.fill(session, catalog, emptied, (pocket, yard))
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
        rig.hopper = Decimal(0)
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


async def _burn(session: AsyncSession, container_id: uuid.UUID, qty: float) -> None:
    stacks = await stock.locked_stacks(session, container_id, _fuel_names())
    await stock.consume(session, stacks, amount(qty))


def _deplete(constants: Constants, vein: Vein, moment: datetime, extracted_before: int) -> None:
    """The vein depletes in the same tiers as from a pickaxe: one rule for all."""
    from src.engine.mining import (  # noqa: PLC0415 -- lazy: breaks the import cycle with mining
        deplete as by_general_rule,
    )

    by_general_rule(constants, vein, moment, extracted_before)
