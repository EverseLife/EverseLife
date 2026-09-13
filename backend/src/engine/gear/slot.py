# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Putting on and taking off, and what a fallen limit lets fall (D-305,
D-306): the frame taken off, the charge run dry at the tick, the worn thing
worn through -- three roads down, one settle at the bottom of all of them.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import battery, events, stock, travel, world
from src.engine.gear._base import GearError, NotGear, Unmade
from src.engine.gear.load import capacity, load_of
from src.models.craft import BatchKind, BatchState, CraftBatch
from src.models.event import EventKind
from src.models.gear import Equipped
from src.models.identity import Body, BodyState
from src.models.inventory import Container, ContainerKind, Item


async def equip(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    *,
    now: datetime | None = None,
) -> str:
    """Wear a thing. Slot taken -- the previous one comes off by itself.

    In-person only in the sense that the thing must be in the hands: a
    backpack lying in another city cannot be put on.
    """
    if body.state is not BodyState.ALIVE:
        raise GearError(key="gear-dead-dresses")
    await travel.require_here(session, body)

    slot = catalog.recipes.slot_of(item.type_key)
    if slot is None:
        raise NotGear(key="gear-no-slot", goods=item.type_key)
    if slot not in catalog.recipes.gear_slots:  # pragma: no cover -- vault data
        #: The slot id travels raw: slots have no `NAME()` entry, and this
        #: refusal is about the vault's data, not about a thing in the world.
        raise NotGear(key="gear-unknown-slot", slot=slot)

    pocket = await world.body_container(session, body)
    if item.container_id != pocket.id:
        raise GearError(key="gear-not-in-hands")
    #: A thing under the knife is not put on (D-305): recycling ends it, and
    #: the slot would empty itself when the batch finished. Repair is the
    #: opposite case and deliberately not here -- gear is mended without being
    #: taken off, and the batch works on the row where it lies.
    unmade = await session.scalar(
        select(CraftBatch.id)
        .where(
            CraftBatch.target_item_id == item.id,
            CraftBatch.kind == BatchKind.RECYCLE,
            CraftBatch.state != BatchState.DONE,
        )
        .limit(1)
    )
    if unmade is not None:
        raise Unmade(key="gear-taken-apart", goods=item.type_key)

    over = await _over(session, constants, catalog, body)
    previous_ = (
        await session.execute(
            select(Equipped).where(Equipped.body_id == body.id, Equipped.slot == slot)
        )
    ).scalar_one_or_none()
    taken_off: tuple[uuid.UUID, ...] = ()
    if previous_ is not None:
        if previous_.item_id == item.id:
            return slot
        taken_off = (previous_.item_id,)
        await session.delete(previous_)
        await session.flush()

    #: A slot row outlives the thing leaving the hands, and one row is all a
    #: thing gets: without this, a pack somebody wore and sold could never be
    #: worn again -- the buyer got a unique-key error instead of an answer.
    #: The thing is in these hands, so whoever the old row names is not
    #: wearing it (D-305), and the row is theirs no longer.
    stale = (
        await session.execute(select(Equipped).where(Equipped.item_id == item.id))
    ).scalar_one_or_none()
    if stale is not None:
        await session.delete(stale)
        await session.flush()

    session.add(Equipped(body_id=body.id, slot=slot, item_id=item.id))
    await session.flush()
    await events.record(
        session,
        EventKind.GEAR_EQUIPPED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(item.id),
        type_key=item.type_key,
        slot=slot,
    )
    #: Putting a thing on can **lower** the limit: one slot per thing, so a
    #: lighter frame takes the place of a heavier one and the previous came off
    #: above. What the old one lifted and the new one cannot falls (D-306).
    await _settle(session, constants, catalog, body, over, spare=taken_off)
    return slot


async def unequip(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body, slot: str
) -> Item | None:
    """Take off what is worn from a slot. The thing stays in the hands -- it was there anyway.

    **And what the frame was carrying does not.** The limit falls with the
    exoskeleton, and a load raised to its ceiling would otherwise stay in the
    pocket and walk out of the node: an overloaded body was said not to exist
    (D-265, D-268), and that was true only of the doors a thing comes in
    through. What no longer fits falls underfoot, heaviest first
    (`overload.shed`, D-306).
    """
    line = (
        await session.execute(
            select(Equipped).where(Equipped.body_id == body.id, Equipped.slot == slot)
        )
    ).scalar_one_or_none()
    if line is None:
        return None
    over = await _over(session, constants, catalog, body)
    thing = await session.get(Item, line.item_id)
    await session.delete(line)
    await session.flush()
    await events.record(
        session,
        EventKind.GEAR_UNEQUIPPED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(line.item_id),
        slot=slot,
    )
    await _settle(session, constants, catalog, body, over, spare=(line.item_id,))
    return thing


async def wear_exoskeletons(
    session: AsyncSession, constants: Constants, catalog: Catalog, *, hours: float, now: datetime
) -> float:
    """Every worn exoskeleton drinks from the batteries in its wearer's hands (D-268).

    Called by the tick for its own length. **A wearer with no charge left drops
    what the charge was carrying** (D-306): the frame stays on the shoulders
    and lifts nothing, so the limit is the bare pair of hands and everything
    above it lies down underfoot. One sweep answers every way the charge can
    go -- drunk to the bottom here, or the cell put down, given away, sold or
    spent as a recipe's input before the tick came round: whatever the road to
    it, the wearer is found without a charge and settles at the next tick.

    Returns the charge drunk, all wearers together.

    **The lock order is the bodies first, then the cells**, and both in id
    order. It is the order `overload._fall` takes when a frame comes off -- the
    body's row, then the stacks it moves -- so a tick draining a cell and a
    player undressing beside it never wait on each other. One query for every
    wearer's cells at once (`stock.locked_stacks` over all the pockets): a
    hand-over of a battery locks cells in id order too.
    """
    rate = constants[R.GEAR_EXO_ENERGY_PER_HOUR]
    if rate <= 0 or hours <= 0:
        return 0.0
    names = {catalog.recipes.resolve(name) for name in constants[R.INVENTORY_EXO_BONUS]}
    #: Keyed by body: the join gives one row per worn frame, and a body that
    #: ever wore two would otherwise be drunk from -- and shed -- twice.
    found = (
        await session.execute(
            select(Body.id, Container.id)
            .join(Container, Container.owner_id == Body.id)
            .join(Equipped, Equipped.body_id == Body.id)
            .join(Item, Item.id == Equipped.item_id)
            .where(
                Container.kind == ContainerKind.BODY,
                Item.type_key.in_(names),
                Body.state == BodyState.ALIVE,
                #: Worn means in these hands (D-305): a frame left on the floor
                #: lifts nothing and so drinks nothing either.
                Item.container_id == Container.id,
            )
        )
    ).all()
    if not found:
        return 0.0
    pocket_of = dict(found)

    #: Locked **and reread**: the rows below say where a fall lands
    #: (`body.node_id`) and are moved on, so a snapshot taken before the lock
    #: is the very thing the lock stands against (`stock.locked_stacks`).
    wearers = (
        (
            await session.execute(
                select(Body)
                .where(Body.id.in_(list(pocket_of)))
                .order_by(Body.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    pockets = sorted(set(pocket_of.values()))
    cells = await stock.locked_stacks(session, pockets, world.station_names(battery.BATTERY))
    by_pocket: dict[uuid.UUID, list[Item]] = {}
    for cell in cells:
        by_pocket.setdefault(cell.container_id, []).append(cell)
    drunk = 0.0
    for pocket in pockets:
        held = by_pocket.get(pocket)
        if held:
            drunk += await battery.drain_cells(session, constants, held, rate * hours, now=now)

    #: The frame lifts on a charge, so no charge is a fallen limit (D-306).
    #: Asked of the cells already read and locked above rather than of the
    #: database again: `battery.charged_carried` would walk the same pocket a
    #: second time, once per wearer, every minute of the world.
    for body in wearers:
        held = by_pocket.get(pocket_of[body.id], ())
        if any(battery.charge_of(constants, cell, now=now) > 0 for cell in held):
            continue
        await _settle(session, constants, catalog, body)
    return drunk


async def losing_worn(
    session: AsyncSession, constants: Constants, catalog: Catalog, item: Item
) -> tuple[Body, float] | None:
    """A worn thing is about to end: its wearer, and the excess of this moment.

    Wear is the one road to a fallen limit that no sweep can walk. The tick
    finds its wearers by joining the slot to the thing (`wear_exoskeletons`),
    so a thing that ceases to exist takes its wearer out of that join at
    exactly the moment the limit falls. The answer is given where the thing
    dies -- and in two halves, because the excess has to be read while the
    thing is still worn and the fall has to happen after it is gone
    (`settle_lost`).

    `None` for everything the vault gives no slot -- ore, a rig, a wagon --
    answered without touching the database: this is asked of every thing that
    ever wears through, and almost none of them were ever worn.

    **The wearer is whose pocket the thing lies in**, not whoever the slot row
    names. A row outlives the thing leaving the hands on purpose (D-305,
    `models/gear`), so a stale one names a body that stopped wearing this long
    ago -- and settling on the strength of it would drop a stranger's ore.

    The row is removed here whoever it names. D-305 does not ask for it -- a
    row that can never match again means nothing to any reader, and `equip`
    clears a stale one itself -- but this is the one moment the world knows
    the thing is dead, and a row about a thing that no longer exists cannot
    become true by waiting.

    **The body's row is taken before the excess is read**, and the caller must
    already hold it if it holds anything else of this body's: `daily_gear_wear`
    takes it before the first thing it wears down, so the order everywhere
    stays the body first, then what lies in its hands.
    """
    if catalog.recipes.slot_of(item.type_key) is None:
        return None
    line = (
        await session.execute(select(Equipped).where(Equipped.item_id == item.id))
    ).scalar_one_or_none()
    if line is None:
        return None
    #: **Whose pocket, asked before any row is taken.** A slot row outlives the
    #: thing leaving the hands on purpose (D-305), so a stale one names a body
    #: that stopped wearing this long ago -- and locking *that* body would take
    #: a row outside the id order the sweeps agree on, which is the one thing
    #: the order was for. Asked without a lock: a stale row is answered by
    #: walking away from it, not by writing.
    holder = await session.scalar(
        select(Container.owner_id).where(
            Container.id == item.container_id, Container.kind == ContainerKind.BODY
        )
    )
    if holder != line.body_id:
        await _forget(session, line)
        return None
    #: Locked **and reread**: the fall below asks the body where it stands
    #: (`body.node_id`) and moves matter there, so a snapshot taken before the
    #: lock is the very thing the lock stands against.
    body = (
        (
            await session.execute(
                select(Body)
                .where(Body.id == line.body_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .one_or_none()
    )
    if body is None:  # pragma: no cover -- a slot without a body is a bug
        await _forget(session, line)
        return None
    #: **Read while the thing is still worn** -- the row is still here and the
    #: thing is still in the pocket, so the lift and the lightening still
    #: count. Taken after either of them goes, this would already be the
    #: excess of a body wearing nothing, and the difference would vanish.
    before = await _over(session, constants, catalog, body)
    await _forget(session, line)
    return body, before


async def _forget(session: AsyncSession, line: Equipped) -> None:
    """The slot row goes with the thing, whoever the row names.

    D-305 does not ask for it -- a row that can never match again means
    nothing to any reader, and `equip` clears a stale one itself -- but a row
    about a thing that has ceased to exist cannot become true by waiting, and
    this is the one moment the world knows the thing is dead.
    """
    await session.delete(line)
    await session.flush()


async def settle_lost(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body, before: float
) -> float:
    """And what the lost thing was holding up comes down -- after it is gone.

    The second half of `losing_worn`, and it must run **after** the thing has
    been deleted: settled before, the dying thing still weighs in the load it
    is about to settle, inflates the excess by its own mass and is itself a
    candidate for the fall -- so more matter than owed reaches the ground and
    the journal names a thing that is not lying there.

    `before` is what `losing_worn` found, and it is passed on rather than
    dropped: **only the difference this death makes falls** (D-306). A suit --
    insulated, heatproof, pyroxite -- lifts nothing and lightens nothing, so
    its ending leaves the excess where it was, less its own weight, and
    nothing falls at all. An overload somebody else's door let in is not this
    one to answer for.

    Returns the kilograms that fell.
    """
    return await _settle(session, constants, catalog, body, before)


async def _over(session: AsyncSession, constants: Constants, catalog: Catalog, body: Body) -> float:
    """By how many kilograms the hands are over the limit right now."""
    return await load_of(session, constants, catalog, body) - await capacity(
        session, constants, catalog, body
    )


async def _settle(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    before: float | None = None,
    *,
    spare: tuple[uuid.UUID, ...] = (),
) -> float:
    """What no longer fits falls underfoot (D-265, D-306).

    `before` is the excess this act found, and dressing and undressing pass it:
    a frame taken off leaves more than it found and **the difference** lies
    down, while putting a suit on leaves it exactly as it was and touches
    nothing. An overload that was already there is not this door to answer for
    -- it arrived by another one, and that one settles it: a lost charge at the
    tick, which passes no `before` at all because losing the lift **is** the
    act, or an arrival past the limit where it lands (`overload.settle_load`).

    `spare` is what this act must not drop whatever it weighs: the thing just
    taken off stays in the hands (D-306), or "take the frame off" would end
    with the frame on the ground and out of reach of the hands that dropped it.
    """
    from src.engine import overload  # noqa: PLC0415 -- lazy: breaks overload -> gear (the limit)

    floor = 0.0
    if before is not None:
        if await _over(session, constants, catalog, body) <= before + overload.DUST:
            return 0.0
        floor = max(before, 0.0)
    return await overload.shed(session, constants, catalog, body, floor=floor, spare=spare)
