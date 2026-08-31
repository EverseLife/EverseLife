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
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current_catalog
from src.constants import registry as R
from src.engine import events, stock, travel, wear, world
from src.engine.errors import Refusal
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.rig import Rig as RigRow
from src.models.world import Node, Vein
from src.units import (
    SCALE_MAX,
    SCALE_MIN,
    SECONDS_PER_HOUR,
    amount,
    amount_float,
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

    mined = 0.0
    if workers > 0:
        mined = output_per_hour * workers
        await _burn(session, yard.id, fuel * workers)
        #: The machine eats the vein twice as fast: capital speeds up the world's depletion.
        from_vein = amount(mined * constants[R.RIG_DEPLETION_MULTIPLIER])
        #: Shared with the miners (`mining.swing`); same lock order: rig -> vein.
        await session.refresh(vein, with_for_update=True)
        before = vein.extracted
        vein.extracted += min(from_vein, vein.remaining)
        vein.remaining = max(0, vein.remaining - from_vein)
        _deplete(constants, vein, moment, before)
        rig.hopper = Decimal(str(float(rig.hopper) + mined))

    #: Wear goes by time, not by what is mined: an abandoned one falls apart.
    day = constants[R.TIME_DAY_TERRA]
    if hours > 0:
        await wear.spend(
            session,
            constants,
            machine,
            constants[R.RIG_WEAR_PER_DAY] * hours / day,
            cause="работа буровой",
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
    #: The hopper is emptied by hand, and hands are not bottomless: without a
    #: wagon the hopper cannot be emptied whole, and that is work for a carter (D-146).
    if vein is not None:
        from src.engine import gear  # noqa: PLC0415 -- lazy: breaks the import cycle with gear

        await gear.check_carry(session, constants, current_catalog(), body, vein.resource, taken)

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
        #: A rig without a vein should not happen; coal is the least-wrong stub.
        type_key=vein.resource if vein else "coal",
        amount=amount(taken),
        quality=Decimal(str(quality)),
    )
    session.add(emptied)
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
    out: list[dict] = []
    for rig in rigs:
        machine = await session.get(Item, rig.item_id)
        vein = await session.get(Vein, rig.vein_id)
        yard = await world.node_container(session, await session.get(Node, rig.node_id))
        coal_ = await _coal_available(session, yard.id)
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
        _deplete as by_general_rule,
    )

    by_general_rule(constants, vein, moment, extracted_before)
