# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Matter and where it lies: containers, what is in them, and folding stacks.

One subject, because every question about a thing is really a question about
its container. Where a thing lies decides what may be done with it, so the
pocket and the yard are looked up here (`body_container`, `node_container`),
read here (`contents`, `thing_kinds`) and written here (`move_stack`,
`stack_up`, `grant_item`). Splitting the lookups from the moving would give
two modules that could never be used apart.

One of those lookups is split in two on purpose: `node_yard`/`node_things`
answer a glance and never write, while `node_container` still creates the
yard it finds missing -- so it is for whoever **puts** something down. The
node has been born with its yard since the review of 2026-08-23
(`land.create_node`); the creating branch is the leftover for nodes born
before that, and only writers pay that debt now.

`station_names` and `has_station` live here rather than with the machines they
answer about: a machine is a thing standing in a node's yard (D-176), and
"is there a workbench here" is `thing_kinds` asked with a class name (D-215).

No function here creates matter out of nothing just like that: items appear
only through mining and harvest (invariant I1). `grant_item` is a tool for
development sessions and scripts, and it writes an event with an explicit
ground so that such an arrival is visible in telemetry.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import current_catalog
from src.db.base import remember
from src.engine import events, goods
from src.models.craft import BatchState, CraftBatch
from src.models.event import EventKind
from src.models.identity import Body
from src.models.inventory import Container, ContainerKind, Item
from src.models.world import Node
from src.units import AMOUNT_SCALE
from src.units import amount as to_amount
from src.units import amount as to_units


def station_names(thing_class: str) -> tuple[str, ...]:
    """Concrete item names of a thing class (D-215).

    Behaviour binds to classes, and a class may hold several machines: a
    second bed or printer arrives as data. A word the catalog does not know
    as a class falls back to itself, name-for-name -- so a test world with a
    bare catalog keeps working.
    """

    members = current_catalog().recipes.of_class(thing_class)
    return members or (thing_class,)


async def body_container(session: AsyncSession, body: Body) -> Container:
    async def find() -> Container:
        stmt = select(Container).where(
            Container.kind == ContainerKind.BODY, Container.owner_id == body.id
        )
        container = (await session.execute(stmt)).scalar_one_or_none()
        if container is None:  # pragma: no cover -- a body without an inventory is a bug
            raise RuntimeError(f"у тела {body.id} нет инвентаря")
        return container

    #: Asked from everywhere and always with the same answer inside one command
    #: -- a pocket does not move from a body (`db.base.remember`).
    return await remember(session, ("body_container", body.id), find)


async def node_yard(session: AsyncSession, node: Node) -> Container | None:
    """The node's yard as it stands -- **without** making one.

    "Чтение не пишет" (CLAUDE.md): the scene, the forecasts and every "what is
    here" ask this, and a node nobody has yet put anything into simply has no
    yard row. Creating one for a glance writes to a place for looking at it,
    and puts an INSERT under `craft.plan`, which the client counts while the
    player is still typing. Nothing there is empty, so nothing is lost: no
    yard and an empty yard answer every read the same.
    """

    async def find() -> Container | None:
        stmt = select(Container).where(
            Container.kind == ContainerKind.NODE, Container.owner_id == node.id
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    return await remember(session, ("node_container", node.id), find)


async def node_things(session: AsyncSession, node: Node) -> tuple[Item, ...]:
    """What lies and stands in the node's yard. A read: no yard, nothing here."""
    yard = await node_yard(session, node)
    return () if yard is None else await contents(session, yard)


async def node_container(session: AsyncSession, node: Node) -> Container:
    """What stands and lies in the node: machines, products at the machine.

    Made on first need -- so this is for whoever **puts** something down.
    Whoever only looks asks `node_yard` or `node_things`.

    **One** store for the node, and the two surfaces a node has (D-244) -- the
    floor of the house and the open ground beside it -- are a mark on the thing
    (`Item.outdoors`), not a second container. Everything that asks this
    question wants the whole answer: the fire of an eruption looking for what to
    burn, a rig looking for its coal, a brazier for its fuel.
    """
    found = await node_yard(session, node)
    if found is not None:
        return found
    container = Container(kind=ContainerKind.NODE, owner_id=node.id)
    session.add(container)
    #: The flush gives the row its id -- and throws the memo away with it, so
    #: the next `node_yard` reads the yard that now exists.
    await session.flush()
    return container


#: The "Библиотека" thing class (D-176, D-215): the library window is shown
#: where any of its machines stands.
LIBRARY = "library"


async def contents(session: AsyncSession, container: Container) -> tuple[Item, ...]:
    """What lies in the container -- everything, in one reading.

    The same three containers are read over and over inside one command: the
    pocket is asked for by the carry limit, by the load, by the convoy and by
    the inventory itself, and the node's yard by every window of the place.
    Each of those was a query. A tuple, not a list, so that a reader cannot
    quietly change what the next reader will get (`db.base.remember`).
    """

    async def read() -> tuple[Item, ...]:
        rows = await session.execute(select(Item).where(Item.container_id == container.id))
        return tuple(rows.scalars().all())

    return await remember(session, ("contents", container.id), read)


async def thing_kinds(session: AsyncSession, node: Node) -> frozenset[str]:
    """Which kinds of things stand in the node -- names, without counting them.

    The node scene is asked this a dozen times in a row, once per class:
    is there a workbench here, a hall, a library, a printer. Each of those was
    a query of its own, and every one of them read the same short list.
    """

    async def find() -> frozenset[str]:
        yard = await node_yard(session, node)
        if yard is None:
            return frozenset()
        rows = await session.execute(
            select(Item.type_key).where(Item.container_id == yard.id).distinct()
        )
        return frozenset(row[0] for row in rows)

    return await remember(session, ("thing_kinds", node.id), find)


async def has_station(session: AsyncSession, node: Node, name: str) -> bool:
    """Whether a machine of this class stands in the node: the node scene is
    built from machines (D-176), and this is the only way to ask what a place
    is. The word is a thing class (D-215); a plain item name still matches
    itself through the fallback in `station_names`."""
    return bool(await thing_kinds(session, node) & frozenset(station_names(name)))


async def is_library(session: AsyncSession, node: Node) -> bool:
    """The library is a machine, not a node property (D-176). The `library`
    property remains a legacy of old worlds: the catch-up seed places the
    machine, but a world that was not caught up must not lose the window."""
    if (node.properties or {}).get("library"):
        return True
    return await has_station(session, node, LIBRARY)


async def move_stack(
    session: AsyncSession,
    item: Item,
    target: Container,
    quantity: float,
    *,
    outdoors: bool = False,
) -> float:
    """Move a stack or part of it into another container.

    The split-off part is **the same thing**: mark, shelf life, condition,
    fineness, cultivar and charge travel with it. Losing them when splitting a
    stack would depersonalise the goods: fifty seeds of a cultivar would turn
    into fifty seeds in general.

    One function for all moving in the world -- hold, chest, terminal: each
    own copy sooner or later falls behind on the field list, and a thing
    quietly loses part of itself on one of the paths.

    `outdoors` is which of a node's two surfaces it comes to rest on (D-244).
    False everywhere but a drop on the open ground -- in a pocket, a chest or a
    hold there is no sky to be under, and on a node with no building the floor
    does not exist and everything reads as outdoors anyway.
    """

    #: A counted thing moves in whole pieces (D-212). A fraction is floored,
    #: and a request smaller than one piece is refused rather than silently
    #: doing nothing.
    #: The stack is locked and reread first: every move in the world comes
    #: through here, and whoever moves the same stack at the same time must
    #: see the remainder, not the snapshot (review 2026-08-23).
    await session.refresh(item, with_for_update=True)
    qty = min(to_units(goods.at_least_one(item.type_key, quantity)), item.amount)
    if qty >= item.amount:
        item.container_id = target.id
        item.outdoors = outdoors
        landed = item
    else:
        item.amount -= qty
        landed = Item(
            container_id=target.id,
            type_key=item.type_key,
            amount=qty,
            quality=item.quality,
            condition=item.condition,
            condition_cap=item.condition_cap,
            maker_identity_id=item.maker_identity_id,
            made_at=item.made_at,
            made_node_id=item.made_node_id,
            spoils_at=item.spoils_at,
            flavor=item.flavor,
            roles_filled=item.roles_filled,
            fineness=item.fineness,
            variety_id=item.variety_id,
            vigor=item.vigor,
            charge=item.charge,
            charged_at=item.charged_at,
            recipe_key=item.recipe_key,
            outdoors=outdoors,
        )
        session.add(landed)
    await session.flush()
    #: What arrived joins what already lies here, if they are the same thing
    #: (D-214). Hence the amount moved is read off `qty` and not off the stack:
    #: the stack may have just grown by everything it swallowed.
    await stack_up(session, landed)
    return qty / AMOUNT_SCALE


#: Everything a thing is described by. Two stacks are the same thing only when
#: all of it matches -- and that is what makes folding them lossless (D-214):
#: there is nothing left over to average away, shorten or forget.
#:
#: What is not here is not an oversight. Being worn, harnessed, rigged or
#: worked at belongs to machines, tools, gear and wagons, and none of those
#: fold at all -- so a fold can never take a thing out from under its use.
#: The one exception is work on a loose stack, and that is guarded below.
SAMENESS = (
    "type_key",
    "quality",
    "condition",
    "condition_cap",
    "maker_identity_id",
    "made_at",
    "made_node_id",
    #: Which surface of a node it lies on (D-244). Two heaps of the same ore,
    #: one on the floor of the house and one in the yard, are **not** the same
    #: thing: folding them would move half a heap indoors, out of the rain and
    #: out of the reach of a collapse.
    "outdoors",
    "spoils_at",
    "flavor",
    "roles_filled",
    "fineness",
    "variety_id",
    "vigor",
    "charge",
    "charged_at",
    "recipe_key",
)


async def stack_up(session: AsyncSession, item: Item) -> Item:
    """Fold what already lies here into the stack that has just arrived (D-214).

    Called wherever matter lands in a container: mined, harvested, found, made,
    bought, taken out of a chest, handed over. **The arrival is the stack that
    survives** -- so whoever asked for the move still holds a live thing when
    this returns, and the twins it swallowed are the ones that go.

    Only the loose kinds fold at all (`goods.stackable`), and only into a stack
    nothing tells them apart from: different quality stays different stacks.
    Reading those together is the client's work -- the list groups by thing and
    says how much there is in total -- not a reason to average the numbers here.
    """

    if not goods.stackable(item.type_key):
        return item
    #: The arrival may be brand new: without a flush it has neither an id to
    #: tell itself apart by nor the fields the table fills in.
    await session.flush()
    #: Twins are locked: the merge deletes them, and a stack being taken
    #: from by another transaction must not vanish under its hands.
    rows = (
        (
            await session.execute(
                select(Item)
                .where(
                    Item.container_id == item.container_id,
                    Item.type_key == item.type_key,
                    Item.id != item.id,
                )
                .order_by(Item.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    twins = [other for other in rows if _same(other, item)]
    if not twins:
        return item
    #: A stack being repaired or taken apart stays where it is: the batch finds
    #: its target by id, and swallowing it would leave the work without a thing.
    pinned = set(
        (
            await session.execute(
                select(CraftBatch.target_item_id).where(
                    CraftBatch.target_item_id.in_([twin.id for twin in twins]),
                    CraftBatch.state != BatchState.DONE,
                )
            )
        )
        .scalars()
        .all()
    )
    for twin in twins:
        if twin.id in pinned:
            continue
        item.amount += twin.amount
        await session.delete(twin)
    await session.flush()
    return item


def _same(one: Item, other: Item) -> bool:
    """Whether nothing at all tells two stacks apart (D-214)."""
    return all(_alike(getattr(one, field), getattr(other, field)) for field in SAMENESS)


def _alike(one: Any, other: Any) -> bool:
    """Equality that does not trip over the road a number took to get here.

    The same quality arrives as `12.5` on one path and as `Decimal("12.50")`
    off the database on another, and in Python those two are not equal.
    """
    #: A bool **is** an int in Python, and `Decimal("False")` is not a number
    #: at all: without this line the first boolean field in `SAMENESS` takes
    #: every fold in the world down with it.
    if isinstance(one, bool) or isinstance(other, bool):
        return one is other
    numbers = (int, float, Decimal)
    if isinstance(one, numbers) and isinstance(other, numbers):
        return Decimal(str(one)) == Decimal(str(other))
    return one == other


async def grant_item(
    session: AsyncSession,
    container: Container,
    type_key: str,
    *,
    amount: float = 1,
    quality: float | None = None,
    origin: str,
    maker_identity_id: uuid.UUID | None = None,
    made_node_id: uuid.UUID | None = None,
) -> Item:
    """Put an item into a container.

    `origin` is mandatory and lands in the event: any appearance of matter in
    the world must have a named ground -- mining, harvest, craft, a debugging
    script. There is no anonymous arrival (pillar P1).
    """

    #: Matter arrives in whole pieces where the thing is counted (D-212): three
    #: quarters of an ingot is no ingot, and the fourth quarter is not ours to
    #: give. Less than one piece is a refusal rather than a stack of nothing --
    #: the table forbids an empty stack, and an integrity error is a worse way
    #: to learn that.

    amount = goods.at_least_one(type_key, amount)
    item = Item(
        container_id=container.id,
        type_key=type_key,
        amount=to_amount(amount),
        quality=None if quality is None else Decimal(str(quality)),
        maker_identity_id=maker_identity_id,
        made_at=datetime.now(UTC) if maker_identity_id else None,
        made_node_id=made_node_id,
    )
    session.add(item)
    await session.flush()
    await events.record(
        session,
        EventKind.ITEM_CREATED,
        actor_identity_id=maker_identity_id,
        item_id=str(item.id),
        type_key=type_key,
        amount=amount,
        quality=quality,
        origin=origin,
    )
    #: The event is written before the fold and about the arrival alone: the
    #: journal says what came into the world, not what the stack grew to (D-214).
    return await stack_up(session, item)
