# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Wear: why things run out (D-129, D-058, 15-quality).

Pillar P2 requires an item to be finite. Hence five wear streams, each
parameterised by the vault separately: a tool per mining session, a tool per
hour of a batch (D-309), a machine per batch, gear per day of wearing, a
vehicle per transit.

The tool has two of them because it is worked in two shapes. A mining session
is a stretch of swings with no length of its own, so it is charged whole; a
batch has an hour count, and charging it per batch instead would make one
felling of fifty logs cost an axe what fifty fellings of one log cost it.

## Two numbers on an item, and they are confused most often

| | Quality | Condition |
|---|---|---|
| What it means | how well the item is made | how worn it is now |
| Changes | never | constantly, from use |

**Quality determines how fast condition falls.** The service-life multiplier is
given by the formula `quality.durability_factor` -- and it is precisely
evaluated, not rewritten in code: otherwise its numbers would move into the
engine (D-065).

**Condition determines how good the item is now.** The effective quality of a
tool and a machine is quality taken by the share of remaining condition.
Without that wear would be just a countdown to breakage, and "maintenance is
mandatory" would remain words: a broken anvil must give a worse result, not
just break suddenly.

**Reached zero -- the thing is finished.** Not "works with zero output" but
disappears: the acceptance benchmark is direct -- a tool runs out in
`100 / wear.tool_per_session` mining sessions, and in `100 / wear.tool_per_hour`
hours of batch work (07-implementation-map, D-309). Of ordinary quality, both:
a good tool lasts longer exactly as many times as it is better.

The environment speeds up gear wear by the `wear.environment_k` multiplier.
That is what makes Pyroxis expensive by itself, without a single special
mechanic (D-129).
"""

from __future__ import annotations

import uuid
from decimal import ROUND_FLOOR, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current_catalog
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.constants.spec import ConstantError
from src.engine import events, gear, world
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Container, ContainerKind, Item
from src.models.world import Node
from src.units import ROUND_QUALITY, ROUND_REMAINDER, on_grid


def life_factor(constants: Constants, quality: float | None) -> float:
    """How many times longer a thing of this quality lasts.

    The formula is taken from the vault and evaluated. One on input means "the
    service life of an ordinary thing": a multiplier goes out, not an absolute term.
    """
    scale = constants[R.QUALITY_SCALE]
    value = scale.mid if quality is None else quality
    factor = constants[R.QUALITY_DURABILITY_FACTOR].value(base_life=1, quality=value)
    if factor <= 0:  # pragma: no cover -- guard against the formula being edited to zero
        raise ConstantError("quality.durability_factor даёт неположительный срок службы")
    return factor


def effective(constants: Constants, item: Item | None) -> float:
    """The thing's effective quality: how it was made, adjusted for wear.

    A worn thing works worse than a new one of the same make -- hence the point
    of maintenance. An intact thing works exactly at its quality.
    """
    scale = constants[R.QUALITY_SCALE]
    if item is None:
        return scale.max
    quality = scale.max if item.quality is None else float(item.quality)
    return scale.clamp(quality * float(item.condition) / scale.max)


def spent_on(
    constants: Constants, item: Item | None, base: float, *, environment: float = 1.0
) -> float:
    """How much condition such wear eats on this thing.

    A good thing wears slower exactly as many times as it lasts longer -- no
    second formula is needed for that.
    """
    if item is None:
        return 0.0
    return base * environment / life_factor(constants, _quality(item))


def wears_out(
    constants: Constants, item: Item | None, base: float, *, environment: float = 1.0
) -> bool:
    """Whether the thing will be finished by such wear -- before it is written off.

    Needed by those who must tidy up **before** the thing disappears: the
    convoy unloads cargo into the node before the wagon is gone (D-157). The
    same formula computes as writes off: they may not diverge.
    """
    if item is None:
        return False
    scale = constants[R.QUALITY_SCALE]
    #: The sliver the last doing could not write is spent first, exactly as
    #: `spend` spends it. Otherwise this says a thing will live, `spend`
    #: finishes it anyway, and whoever trusted the answer is left tidying up
    #: after a thing already gone -- the convoy unloads its cargo before the
    #: wagon goes (D-157), and a wagon that dies inside the "will survive"
    #: branch takes the whole arrival down on the harness's own key.
    owed = spent_on(constants, item, base, environment=environment) + float(item.wear_remainder)
    return float(item.condition) - owed <= scale.min


async def spend(
    session: AsyncSession,
    constants: Constants,
    item: Item | None,
    base: float,
    *,
    environment: float = 1.0,
    cause: str,
    actor_identity_id=None,
) -> bool:
    """Write off wear. Returns True if the thing is finished by it.

    `cause` is a payload key naming the doing that wore the thing
    (`mining_session`, `convoy_move`), never a sentence: the journal stores
    it for good, and stored words cannot be translated (D-251).
    """
    if item is None:
        return False
    #: A relic of the Forerunners does not wear (D-232): it is not taken down,
    #: not taken apart and not worn out. Without this a city's spaceport would
    #: quietly grind itself to nothing at the hands of whoever used it, and the
    #: beacon would go out for a reason nobody could see coming.
    if current_catalog().recipes.is_relic(item.type_key):
        return False
    scale = constants[R.QUALITY_SCALE]
    asked = spent_on(constants, item, base, environment=environment)
    was = float(item.condition)
    #: What the last doing could not write is spent first. Condition is kept to
    #: a hundredth, and a doing may cost less than one -- a rig settled every
    #: half minute, a swing on a fine tool. Dropped, that wear never happened,
    #: and a machine tapped often enough never wore at all. The sliver cannot
    #: ride on a stamp: `rig.counted_at` measures the mining too, and holding
    #: it back mines the same ore twice.
    #: Read-modify-write with no lock of its own. Every stream that reaches
    #: here already holds the thing: the rig and the automat by their own row
    #: (`tick_rigs` and `advance` take it `with_for_update`), a bench by the
    #: body busy at it, a wagon by its harness, gear by the daily step alone.
    #: A second writer would cost less than a hundredth, but there is none --
    #: and whoever adds one takes the lock, as `condition` beside it will need.
    owed = asked + float(item.wear_remainder)
    capacity = was - scale.min
    if owed >= capacity:
        #: More than the thing has left to give: it is finished here, and what
        #: it could not pay dies with it rather than becoming a debt.
        takes, left, rest = capacity, scale.min, 0.0
    else:
        #: Down, so the row is never charged wear nobody asked for. What is
        #: left over is under a hundredth by construction and waits on the
        #: thing for the next doing.
        takes = float(on_grid(owed, ROUND_QUALITY, ROUND_FLOOR))
        left = was - takes
        rest = owed - takes
    item.condition = Decimal(str(left))
    item.wear_remainder = on_grid(rest, ROUND_REMAINDER, ROUND_FLOOR)
    spent = takes

    await events.record(
        session,
        EventKind.ITEM_WORN,
        actor_identity_id=actor_identity_id,
        item_id=str(item.id),
        type_key=item.type_key,
        spent=spent,
        condition=left,
        cause=cause,
    )
    if left > scale.min:
        await session.flush()
        return False

    await events.record(
        session,
        EventKind.ITEM_CONSUMED,
        actor_identity_id=actor_identity_id,
        item_id=str(item.id),
        type_key=item.type_key,
        #: Two keys, not one composed string: what ended the thing, and at
        #: which doing. `«износ: сессия добычи»` was unreadable to any locale.
        cause="worn_out",
        doing=cause,
    )
    #: Asked while the thing is still worn: the slot forgets it, and the excess
    #: of this moment is read to be compared with the one it leaves behind. A
    #: thing that was never worn -- a rig, a wagon, a heap of ore -- answers
    #: `None` here without touching the database (D-305).
    losing = await gear.losing_worn(session, constants, current_catalog(), item)
    await session.delete(item)
    await session.flush()
    if losing is not None:
        #: And **after** the thing is gone, what it was holding up comes down
        #: -- only the difference its ending makes, never an overload another
        #: door let in (D-306).
        wearer, before = losing
        await gear.settle_lost(session, constants, current_catalog(), wearer, before)
    return True


async def daily_gear_wear(session: AsyncSession, constants: Constants, catalog: Catalog) -> int:
    """Daily gear wear on living bodies. Returns the number of things finished.

    Gear wears from wearing, not from use (sink S2), and the environment
    decides how fast: fourfold on Pyroxis.
    """
    rows = (
        await session.execute(
            select(Item, Node.planet, Body.identity_id, Body.id)
            .join(Container, Container.id == Item.container_id)
            .join(Body, Body.id == Container.owner_id)
            .join(Node, Node.id == Body.node_id)
            .where(
                Container.kind == ContainerKind.BODY,
                Body.state == BodyState.ALIVE,
            )
            #: **In body order, and the rows of one body together**, because
            #: this step now takes body rows: a thing worn through ends under
            #: `gear.losing_worn`, which locks its wearer to settle the load.
            #: Tick steps run in transactions of their own (`tick.tick_step`),
            #: so this walks beside every other sweep that locks bodies --
            #: `gear.wear_exoskeletons`, `frost.tick_bodies`,
            #: `oxygen.tick_bodies` -- and all of them take their bodies in id
            #: order too. Two sweeps taking the same rows in two orders is a
            #: deadlock; taking them in one order is not.
            .order_by(Body.id)
        )
    ).all()

    per_day = constants[R.WEAR_GEAR_PER_DAY]
    modifiers = constants[R.WEAR_ENVIRONMENT_K]
    #: Gathered by body before anything is written. The rows arrive in body
    #: order above, so this keeps that order, and it buys the look-ahead the
    #: locking below needs: whether this body loses anything at all today.
    worn: dict[uuid.UUID, list[tuple[Item, float, uuid.UUID]]] = {}
    for item, planet, identity_id, body_id in rows:
        if not _is_gear(catalog, item.type_key):
            continue
        #: `wear.environment_k` keys are planet ids since D-251 normalization.
        worn.setdefault(body_id, []).append((item, modifiers.get(planet.value, 1.0), identity_id))

    #: **The bodies first, then what lies in their hands** -- but only the
    #: bodies something ends on today. Writing wear takes the thing's row, and
    #: a thing worn through then takes its wearer's to settle the load, so the
    #: order here would otherwise be the thing and then the body, against
    #: every other holder in the world (`overload._fall` takes the body and
    #: then the stacks it moves). Asked with `wears_out` rather than taken
    #: blindly, so a daily step does not hold the row of every living body in
    #: the world for a wearing-through that happens to a handful.
    #:
    #: **All of them before the first write**, not each before its own. This
    #: is one transaction over the world, and the rows of a body nothing ends
    #: on are written without its lock; a body further down the walk taken
    #: after them held that write while waiting for a row -- and a handover
    #: holds two bodies' rows at once (`storage.hand`): the giver's thing,
    #: worn here, against the taker's row, taken here later, was a knot the
    #: database could only cut by killing one of the two.
    ending = [
        body_id
        for body_id, theirs in worn.items()
        if any(wears_out(constants, one, per_day, environment=where) for one, where, _ in theirs)
    ]
    await world.lock_bodies(session, ending)

    gone = 0
    for theirs in worn.values():
        for one, where, identity_id in theirs:
            if await spend(
                session,
                constants,
                one,
                per_day,
                environment=where,
                cause="wearing",
                actor_identity_id=identity_id,
            ):
                gone += 1
            elif wears_out(constants, one, per_day, environment=where) and await gear.is_worn(
                session, one
            ):
                #: Tomorrow's wear finishes it, and the end of a worn thing is
                #: not always harmless: a suit outside is the body's breath, a
                #: frame is the load it holds up (D-306). Said the day before,
                #: by the very measure that will write it off -- where the body
                #: stands today -- and only of what is worn (D-343).
                await events.record(
                    session,
                    EventKind.GEAR_WEARING_OUT,
                    actor_identity_id=identity_id,
                    item_id=str(one.id),
                    type_key=one.type_key,
                )
    return gone


def _is_gear(catalog: Catalog, type_key: str) -> bool:
    """Gear and containers are worn and wear out; raw material and food do not (D-090)."""
    try:
        return catalog.recipes.recipe(type_key).kind is ItemKind.GEAR
    except ConstantError:
        return False


def _quality(item: Item) -> float | None:
    return None if item.quality is None else float(item.quality)
