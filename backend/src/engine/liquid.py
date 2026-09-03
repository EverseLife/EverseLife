# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Liquids: never loose, always in a vessel (D-230).

Water, spirit, oxidiser and rocket fuel are **liquids**: one list in the
vault (`RecipeBook.liquid`), not a guess by the label class. A liquid has no
place of its own in the world -- it is not held in the hands, does not lie on
the floor and is not put into a chest. It exists inside a **vessel**: a thing
with `store` and `holds: жидкость`, the canister in the hands or the tank in a
ship's room. That is the whole rule, and everything below follows from it.

## What follows

* **Made -- poured.** A batch that ends in a liquid does not land in the pocket:
  it is poured into the vessels the master carries, then into the vessels
  standing at the machine, and what does not fit is **spilled** -- gone, with an
  event to say so. A refusal is not possible at the end of a term, and matter
  that lands nowhere is a lie about the world.
* **Consumed -- from the vessel.** Whoever asks a container for an input --
  the craft, the field, the pot -- reaches into the vessels in it as well
  (`reach`). The consumer does not know it did; that is the point.
* **Moved -- poured over.** The only way a liquid changes place is `pour`: from
  one vessel into another, both within arm's reach, the target locked so two
  hoses into one tank cannot overfill it.
* **Weighed -- with the vessel.** A full canister is a canister plus what is in
  it, for the carry limit (`gear.load_of`) and for the hull (`ship.physics`) alike.

A vessel admits liquids and nothing else; a chest admits everything but.
`admits` is the one question both doors ask.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import events, gear, station, stock, storage, travel, world
from src.engine.errors import Refusal
from src.engine.storage import LIQUID, admits, is_vessel  # noqa: F401 -- the vessel questions
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Container, ContainerKind, Item
from src.models.world import Node
from src.units import AMOUNT_SCALE, amount_float


class LiquidError(Refusal):
    pass


class NotVessel(LiquidError):
    """Not a vessel: liquids go into a canister or a tank, nothing else."""


class NotLiquid(LiquidError):
    """Not a liquid: a vessel holds liquids only."""


class NoRoom(LiquidError):
    """Nothing more fits: the vessel is full, or the hands are."""


def is_liquid(catalog: Catalog, type_key: str) -> bool:
    return catalog.recipes.is_liquid(type_key)


async def _lock(session: AsyncSession, *vessels: Item) -> None:
    """Lock the vessel rows, in id order, before their free space is read.

    The free space is read, checked and filled; two hoses into one tank --
    or the worker pouring a batch while the owner pours a canister -- would
    both see it half empty. One order everywhere, ascending id, so two pours
    into each other's vessels never wait on each other (review 2026-08-23).
    """
    ids = sorted({vessel.id for vessel in vessels})
    await session.execute(
        select(Item)
        .where(Item.id.in_(ids))
        .order_by(Item.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def vessels_in(session: AsyncSession, catalog: Catalog, container: Container) -> list[Item]:
    """The vessels lying in a container, in id order -- a stable pouring order."""
    things = await world.contents(session, container)
    return sorted(
        (thing for thing in things if is_vessel(catalog, thing.type_key)), key=lambda t: t.id
    )


async def reach(session: AsyncSession, catalog: Catalog, container: Container) -> list[uuid.UUID]:
    """Where a consumer of this container draws from: the container itself and
    the insides of the vessels in it. A liquid input is found without the
    consumer knowing it reached into a canister."""
    ids = [container.id]
    vessels = await vessels_in(session, catalog, container)
    if vessels:
        insides = (
            (
                await session.execute(
                    select(Container).where(
                        Container.kind == ContainerKind.STORAGE,
                        Container.owner_id.in_([vessel.id for vessel in vessels]),
                    )
                )
            )
            .scalars()
            .all()
        )
        ids.extend(inside.id for inside in sorted(insides, key=lambda c: c.id))
    return ids


async def locked_stacks(
    session: AsyncSession,
    catalog: Catalog,
    container: Container,
    type_keys: Iterable[str],
    *,
    worst_first: bool = False,
) -> list[Item]:
    """`stock.locked_stacks` over the container and the vessels in it."""
    return await stock.locked_stacks(
        session, await reach(session, catalog, container), type_keys, worst_first=worst_first
    )


async def free_in(session: AsyncSession, catalog: Catalog, vessel: Item) -> float:
    """Kilograms the vessel still takes."""
    limit = storage.capacity(catalog, vessel.type_key) or 0.0
    return limit - await storage.stored_mass(session, catalog, vessel)


async def takes(session: AsyncSession, vessel: Item, type_key: str) -> bool:
    """Whether the vessel may take this liquid: it is empty, or holds the same.

    One liquid per vessel (D-288): a tank of fuel with water in it is
    nonsense, not a reserve. The rule is one for every way a liquid gets into
    a vessel -- a hand pouring, a batch finishing, a rig's hopper, a
    machine's outlet -- so it is asked here and nowhere is it re-decided.
    """
    return all(one.type_key == type_key for one in await storage.content(session, vessel))


async def room_for(
    session: AsyncSession, catalog: Catalog, container: Container, type_key: str
) -> float:
    """How many units of this liquid the vessels within reach still take.

    **Under the same lock the pouring takes**, so that an answer may be acted
    on: a caller that has to refuse before making the matter -- a find offered
    by the land, which must keep lying rather than be conjured and spilled --
    would otherwise read the free space, lose the race to a batch finishing
    into the same canister, and pour less than it promised without a word.
    """
    if not is_liquid(catalog, type_key):
        return 0.0
    unit = catalog.recipes.mass_of(type_key)
    free = 0.0
    for vessel in await vessels_in(session, catalog, container):
        await _lock(session, vessel)
        if not await takes(session, vessel, type_key):
            continue
        free += await free_in(session, catalog, vessel)
    return free if unit <= 0 else free / unit


async def fill(
    session: AsyncSession,
    catalog: Catalog,
    item: Item,
    containers: Sequence[Container],
) -> float:
    """Pour as much of a liquid stack as fits into the vessels within reach.

    `item` lies in some container already; it is moved into vessels in the
    order of `containers` -- the pocket first, then the yard. The remainder
    **stays in the stack**, and disposing of it is the caller's decision: a
    batch spills it (`settle`), the rig puts it back into the hopper (D-252)
    -- a loose liquid stack must not outlive the caller either way. A thing
    that is not a liquid is left where it is, untouched.

    Returns what was poured.
    """
    if not is_liquid(catalog, item.type_key):
        return 0.0
    unit = catalog.recipes.mass_of(item.type_key)
    before = amount_float(item.amount)
    for container in containers:
        for vessel in await vessels_in(session, catalog, container):
            if item.amount <= 0:
                break
            #: Under lock, like `pour`: the worker finishing a batch and the
            #: owner filling the same canister must not both see it half empty.
            await _lock(session, vessel)
            if not await takes(session, vessel, item.type_key):
                continue
            room = await free_in(session, catalog, vessel)
            have = amount_float(item.amount)
            fits = have if unit <= 0 else min(have, room / unit)
            if fits * AMOUNT_SCALE < 1:
                continue
            inside = await storage.inside(session, vessel)
            #: `move_stack` copies the stack's whole identity into the vessel
            #: and folds it with a twin already there (D-214). The whole stack
            #: gone over -- nothing left behind.
            await world.move_stack(session, item, inside, fits)
            if item.container_id == inside.id:
                return before
    return before - amount_float(item.amount)


async def settle(
    session: AsyncSession,
    catalog: Catalog,
    item: Item,
    containers: Sequence[Container],
) -> float:
    """Pour a liquid stack that has just appeared into the vessels within reach.

    `item` lies in some container already (the batch put it where the output
    lands); it is moved into vessels in the order of `containers` -- the
    pocket first, then the yard -- and what fits nowhere is **spilled**:
    deleted, and the amount returned so the caller can say so. A thing that is
    not a liquid is left where it is, untouched.
    """
    if not is_liquid(catalog, item.type_key):
        return 0.0
    before = amount_float(item.amount)
    #: What did not pour is what spills -- never the stack's own amount: a
    #: stack that went in whole still carries it, only inside a vessel now.
    spilled = before - await fill(session, catalog, item, containers)
    if spilled > 0:
        await session.delete(item)
        await session.flush()
    return spilled


async def pour(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    source: Item,
    target: Item,
    type_key: str | None = None,
    quantity: float | None = None,
) -> tuple[str, float]:
    """Pour from one vessel into another. Both within arm's reach.

    A vessel in the hands is yours; one standing in the node is opened by
    whoever may dispose of the node (`storage._allowed`, D-181). The target is
    **locked** before its free space is read: two people pouring into one
    tank at the same moment would otherwise both see it half empty.

    Returns what was poured and how much. Nothing poured is a refusal, and
    the reason is named: no such liquid, or no room.
    """
    if body.state is not BodyState.ALIVE:
        raise LiquidError(key="liquid-dead-pours")
    await travel.require_here(session, body)
    if source.id == target.id:
        raise LiquidError(key="liquid-same-vessel")
    for vessel in (source, target):
        if not is_vessel(catalog, vessel.type_key):
            raise NotVessel(key="liquid-not-a-vessel", vessel=vessel.type_key)
    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body always stands in a node
        raise LiquidError(key="liquid-body-off-node")
    pocket = await world.body_container(session, body)
    await _within_reach(session, catalog, body, node, pocket, source)
    await _within_reach(session, catalog, body, node, pocket, target)

    #: Both vessels under lock, in id order, before the free space is read:
    #: the space is what the pour is sized by, a second hose must see this
    #: one's result, and two pours into each other's vessels must queue
    #: rather than deadlock.
    await _lock(session, source, target)
    room = await free_in(session, catalog, target)

    names = (type_key,) if type_key else tuple(catalog.recipes.liquid)
    inside = await storage.inside(session, source, create=False)
    if inside is None:
        raise LiquidError(key="liquid-source-empty", vessel=source.type_key, named="false")
    stacks = await stock.locked_stacks(session, inside.id, names)
    if not stacks:
        raise LiquidError(
            key="liquid-source-empty",
            vessel=source.type_key,
            goods=type_key or "",
            named="true" if type_key else "false",
        )
    liquid_name = stacks[0].type_key
    unit = catalog.recipes.mass_of(liquid_name)
    have = sum(amount_float(stack.amount) for stack in stacks if stack.type_key == liquid_name)
    want = have if quantity is None else min(quantity, have)
    if want <= 0:
        raise LiquidError(key="liquid-nothing-to-pour")
    fits = want if unit <= 0 else min(want, room / unit)
    if fits * AMOUNT_SCALE < 1:
        raise NoRoom(key="liquid-no-room", vessel=target.type_key, free=max(room, 0))
    #: Into the hands -- under the carry limit, with the vessel already counted.
    if target.container_id == pocket.id:
        await gear.check_carry(session, constants, catalog, body, liquid_name, fits)

    hold = await storage.inside(session, target)
    #: One liquid per vessel (D-288), and by hand the refusal is worded: a
    #: machine skips the vessel, a person is told what is in it.
    other = next(
        (one for one in await storage.content(session, target) if one.type_key != liquid_name),
        None,
    )
    if other is not None:
        raise LiquidError(key="liquid-mixed", vessel=target.type_key, have=other.type_key)
    left = fits
    poured = 0.0
    for stack in stacks:
        if stack.type_key != liquid_name or left * AMOUNT_SCALE < 1:
            continue
        moved = await world.move_stack(session, stack, hold, min(left, amount_float(stack.amount)))
        poured += moved
        left -= moved
    await events.record(
        session,
        EventKind.STORAGE_POURED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        item_id=str(target.id),
        source=str(source.id),
        type_key=liquid_name,
        amount=poured,
    )
    return liquid_name, poured


async def _within_reach(
    session: AsyncSession,
    catalog: Catalog,
    body: Body,
    node: Node,
    pocket: Container,
    vessel: Item,
) -> None:
    """In the hands, or standing here and yours to open."""
    if vessel.container_id == pocket.id:
        return
    yard = await world.node_container(session, node)
    if vessel.container_id != yard.id:
        raise LiquidError(key="liquid-vessel-not-here", vessel=vessel.type_key)
    #: The same door as a chest: the holder of the node, and the authority on civic land.
    if not await station.may_build(session, body, node):
        raise storage.NotYours(key="liquid-vessel-not-yours", vessel=vessel.type_key)
