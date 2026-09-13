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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.db.base import forget
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


@dataclass(frozen=True)
class Held:
    """A vessel under its lock, and what lay inside it once the lock was had.

    `inside` is None for a vessel nothing has been poured into yet: the first
    pour makes one (`storage.inside`), a lock does not. `moved` says the wait
    carried it off: it no longer lies where it lay before the lock.
    """

    vessel: Item
    inside: Container | None
    things: tuple[Item, ...]
    moved: bool

    def takes(self, type_key: str) -> bool:
        """Whether the vessel may take this liquid: it is empty, or holds the same.

        One liquid per vessel (D-288): a tank of fuel with water in it is
        nonsense, not a reserve. The rule is one for every way a liquid gets
        into a vessel -- a hand pouring, a batch finishing, a rig's hopper, a
        machine's outlet -- so it is asked here and nowhere is it re-decided.
        """
        return all(one.type_key == type_key for one in self.things)

    def room(self, catalog: Catalog) -> float:
        """Kilograms it still takes -- none, never less, for one somebody
        overfilled. A vessel admits liquids alone, so what lies in it is the
        whole of its load: no storage inside to weigh as well."""
        limit = storage.capacity(catalog, self.vessel.type_key) or 0.0
        load = sum(
            gear.mass_of(catalog, one.type_key, amount_float(one.amount)) for one in self.things
        )
        return max(0.0, limit - load)


async def lock_vessels(session: AsyncSession, vessels: Sequence[Item]) -> dict[uuid.UUID, Held]:
    """Lock the vessel rows, in id order, and read what lies in them after the lock.

    The free space is read, checked and filled; two hoses into one tank --
    or the worker pouring a batch while the owner pours a canister -- would
    both see it half empty. One order everywhere, ascending id, so two pours
    into each other's vessels queue rather than deadlock (review 2026-08-23).
    Every door that **adds** to a vessel comes through here, the market's
    included (`market.counter`), and that is what the room rests on: a draw
    takes out of a vessel under the lock on its stacks alone
    (`locked_stacks`), so the contents may still shrink after this lock --
    which only makes the room read smaller -- but they never grow.

    **Every vessel a transaction will use, in one call.** The order a liquid
    is poured in -- the hands before the yard (`fill`) -- is not an order to
    lock in, and neither is taking them one at a time as the pour reaches
    them: a batch held the canister in the hands and reached for the yard's
    while a pour between the two held the yard's, the lower id. And **the
    vessels before any stack**, whoever takes both: a pour locks its vessels
    and then the stacks in its source, and the automats' tick, which draws
    out of the yard's vessels and off the yard itself, locks the vessels
    first (`automat.run.advance`) -- so a door that burns a yard's coal and
    pours afterwards locks them before the coal too (`rig.empty_hopper`), and
    a work that spends a vessel as an input locks it before its other inputs
    (`craft._internal._stock`).

    The free space is measured off what lies **inside**, and the lock is on
    the vessel, so the contents are reread after it and the command's memory
    goes with them (`db.base.forget`), as `estate.hold_ground` does for its
    plot. Whoever held the vessel meanwhile may have poured a canister into
    it: the arriving stack survives the fold and swallows the one inside
    (D-214). A transaction that read that canister before the wait keeps the
    stack with the canister's amount for as long as anything in it still
    refers to the row, and a remembered answer keeps the swallowed one --
    measured off either, the pour after the wait overfills the vessel
    (D-230). The answer carries the reread contents, so the room is measured
    off them without a query per vessel: a yard stands dozens of tanks, and
    the tick pays out into it for every machine.

    Returns what came back, by id. **The wait may have moved a vessel**: one
    absent from the answer is gone -- burnt, spent -- and one `moved` was
    carried off. The place is the one the caller's rows held before the lock,
    so a list read before the wait is judged against the wait. Pouring into
    either by that list lands the liquid in somebody's hands past the carry
    limit, or in an inside made anew for a thing that no longer exists.
    """
    ids = sorted({vessel.id for vessel in vessels})
    if not ids:
        return {}
    #: Read before the lock: it rereads these very rows in place.
    lay = {vessel.id: vessel.container_id for vessel in vessels}
    rows = (
        (
            await session.execute(
                select(Item)
                .where(Item.id.in_(ids))
                .order_by(Item.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    insides: dict[uuid.UUID, Container] = {}
    inner: dict[uuid.UUID, list[Item]] = {}
    if rows:
        found = await session.execute(
            select(Container, Item)
            .outerjoin(Item, Item.container_id == Container.id)
            .where(
                Container.kind == ContainerKind.STORAGE,
                Container.owner_id.in_([row.id for row in rows]),
            )
            .order_by(Item.id)
            .execution_options(populate_existing=True)
        )
        for hold, thing in found.tuples():
            insides[hold.owner_id] = hold
            lying = inner.setdefault(hold.owner_id, [])
            if thing is not None:
                lying.append(thing)
    forget(session)
    return {
        row.id: Held(
            vessel=row,
            inside=insides.get(row.id),
            things=tuple(inner.get(row.id, ())),
            moved=row.container_id != lay[row.id],
        )
        for row in rows
    }


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
    barred: Iterable[str] = (),
    held: Mapping[uuid.UUID, Held] | None = None,
) -> list[Item]:
    """`stock.locked_stacks` over the container and the vessels in it.

    `barred` names what is taken only out of the vessels, never off the
    container itself (`stock.locked_stacks`).

    The vessels themselves are not locked here: a draw alone needs only its
    stacks. A transaction that will also pour into those vessels, or out of
    them, locks them first (`lock_vessels`) and passes the answer as `held`:
    the stacks are then drawn out of the vessels it holds and no other -- one
    put down since is not reached into unlocked -- and the container is not
    walked a second time.
    """
    names = tuple(barred)
    if held is None:
        within = await reach(session, catalog, container)
    else:
        insides = (one.inside for one in held.values() if not one.moved)
        within = [container.id, *sorted(inside.id for inside in insides if inside is not None)]
    return await stock.locked_stacks(
        session,
        within,
        type_keys,
        worst_first=worst_first,
        barred=(container.id, names) if names else None,
    )


async def free_in(session: AsyncSession, catalog: Catalog, vessel: Item) -> float:
    """Kilograms the vessel still takes."""
    limit = storage.capacity(catalog, vessel.type_key) or 0.0
    return limit - await storage.stored_mass(session, catalog, vessel)


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
    held = await lock_vessels(session, await vessels_in(session, catalog, container))
    return room_of(catalog, held.values(), type_key)


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
    vessels: list[Item] = []
    for container in containers:
        vessels.extend(await vessels_in(session, catalog, container))
    return await fill_vessels(session, catalog, item, vessels)


async def fill_vessels(
    session: AsyncSession, catalog: Catalog, item: Item, vessels: Sequence[Item]
) -> float:
    """Pour as much of a liquid stack as fits into these vessels, in this order.

    The one pouring loop: `fill` gives it the vessels within reach, a machine's
    outlet the vessels on its line (D-288). The vessels are locked all at
    once, in id order, whatever order they are filled in (`lock_vessels`); one
    the wait took away is passed over, and so is one holding another liquid
    (D-288: one liquid per vessel). The remainder stays in the stack, for the
    caller to dispose of. Returns what was poured.
    """
    if not is_liquid(catalog, item.type_key):
        return 0.0
    unit = catalog.recipes.mass_of(item.type_key)
    before = amount_float(item.amount)
    #: Under lock, like `pour`: the worker finishing a batch and the owner
    #: filling the same canister must not both see it half empty. Every vessel
    #: at once and in id order, never one by one in the pouring order: a line
    #: names its vessels in the owner's order, and two machines filling the
    #: same two tanks through lines drawn the other way round would each hold
    #: one and wait on the other (review 2026-09-13).
    held = await lock_vessels(session, vessels)
    #: Each vessel once: a second visit would measure its room off the
    #: contents read before the first pour into it.
    for vessel in {vessel.id: vessel for vessel in vessels}.values():
        one = held.get(vessel.id)
        #: Gone during the wait, or carried off: no longer a vessel within
        #: this reach, whatever the list read before the lock said.
        if one is None or one.moved or not one.takes(item.type_key):
            continue
        have = amount_float(item.amount)
        fits = have if unit <= 0 else min(have, one.room(catalog) / unit)
        if fits * AMOUNT_SCALE < 1:
            continue
        inside = one.inside or await storage.inside(session, vessel)
        #: `move_stack` copies the stack's whole identity into the vessel
        #: and folds it with a twin already there (D-214). The whole stack
        #: gone over -- nothing left behind.
        await world.move_stack(session, item, inside, fits)
        if item.container_id == inside.id:
            return before
    return before - amount_float(item.amount)


async def room_in(
    session: AsyncSession,
    catalog: Catalog,
    vessels: Sequence[Item],
    type_key: str,
    *,
    lock: bool = True,
) -> float:
    """How many units of this liquid these vessels still take, together.

    Locked by default, like `room_for`, so that an answer may be acted on in
    the same transaction -- and then only the vessels the wait left where they
    were count (`lock_vessels`); a forecast passes `lock=False` and reads. A
    vessel holding another liquid takes none of it (D-288). Counted by
    `room_of` either way: one arithmetic for the door that refuses and the
    window and "as much as fits" that show it.
    """
    if not is_liquid(catalog, type_key) or not vessels:
        return 0.0
    if lock:
        return room_of(catalog, (await lock_vessels(session, vessels)).values(), type_key)
    return await room_seen(session, catalog, vessels, type_key)


async def room_seen(
    session: AsyncSession, catalog: Catalog, vessels: Sequence[Item], type_key: str
) -> float:
    """`room_in` for a reading: how many units of this liquid these vessels
    take together, their contents read in two queries for all of them and
    nothing locked -- what a window shows and a forecast caps by, asked while
    the player is still choosing."""
    if not is_liquid(catalog, type_key) or not vessels:
        return 0.0
    lying = await storage.contents_of(session, vessels)
    seen = (
        Held(vessel=vessel, inside=None, things=tuple(lying.get(vessel.id, ())), moved=False)
        for vessel in vessels
    )
    return room_of(catalog, seen, type_key)


def room_of(catalog: Catalog, held: Iterable[Held], type_key: str) -> float:
    """How many units of this liquid these vessels take together: those still
    where they were found, and holding nothing else (D-288). A vessel admits
    liquids alone, so what lies in one is the whole of its load: no nested
    storage to walk into."""
    unit = catalog.recipes.mass_of(type_key)
    free = sum(one.room(catalog) for one in held if not one.moved and one.takes(type_key))
    return free if unit <= 0 else free / unit


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


async def fill_or_drop(
    session: AsyncSession, catalog: Catalog, item: Item, vessels: Sequence[Item]
) -> float:
    """Pour a stack that has just appeared into these vessels, in this order,
    and drop what finds no room. Returns what was dropped.

    `settle` for a list of vessels rather than the containers within reach,
    and without a word: whether the drop is a spill -- a batch's oxygen whose
    tank somebody filled meanwhile, said in the journal -- or a vent -- the
    hydrogen of electrolysis going overboard, said nowhere, because nothing
    anybody kept was lost (D-340) -- is the caller's to say.
    """
    if not is_liquid(catalog, item.type_key):
        return 0.0
    before = amount_float(item.amount)
    gone = before - await fill_vessels(session, catalog, item, vessels)
    if gone > 0:
        await session.delete(item)
        await session.flush()
    return gone


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
    await within_reach(session, catalog, body, node, pocket, source)
    await within_reach(session, catalog, body, node, pocket, target)

    #: Both vessels under lock, in id order, before the free space is read:
    #: the space is what the pour is sized by, a second hose must see this
    #: one's result, and two pours into each other's vessels must queue
    #: rather than deadlock.
    held = await lock_vessels(session, (source, target))
    #: And the reach asked again if the wait moved them. It was asked before
    #: the lock -- a refusal holds nobody's vessel -- and the floor is open to
    #: a guest (D-204): one picking the canister up meanwhile would have the
    #: pour drawing out of their hands, or filling them past a carry limit
    #: nobody weighed.
    for vessel in (source, target):
        if vessel.id not in held:
            raise LiquidError(key="thing-gone", goods=vessel.type_key)
    if held[source.id].moved or held[target.id].moved:
        await within_reach(session, catalog, body, node, pocket, source)
        await within_reach(session, catalog, body, node, pocket, target)
    into = held[target.id]
    room = into.room(catalog)

    names = (type_key,) if type_key else tuple(catalog.recipes.liquid)
    inside = held[source.id].inside
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

    hold = into.inside or await storage.inside(session, target)
    #: One liquid per vessel (D-288), and by hand the refusal is worded: a
    #: machine skips the vessel, a person is told what is in it.
    other = next((one for one in into.things if one.type_key != liquid_name), None)
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


async def within_reach(
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
