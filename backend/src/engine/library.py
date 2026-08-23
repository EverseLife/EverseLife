# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The library holds what was put into it (D-053, D-068, D-209).

Two rules from the vault, and the second is what this module adds to the
first:

* **anyone may take** -- free, unconditional, without citizenship, but only
  in person (`craft.copy_recipe`);
* **anyone may give** -- a written carrier brought to a library becomes part
  of it for good, and the giver's name stays with the recipe.

A library's list is its own. The capital's is laid down at genesis -- the base
set the Forerunners left; one a city builds starts empty and fills as people
bring knowledge. That is where the delivery business lives (03-crafting): the
recipe is free, but the road to a library that has it is not.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog
from src.engine import events, travel
from src.engine import world as world_engine
from src.engine.errors import Refusal
from src.engine.world import body_container
from src.models.event import EventKind
from src.models.identity import Body, BodyState, Identity
from src.models.inventory import Item
from src.models.library import LibraryEntry
from src.models.world import Node


class LibraryError(Refusal):
    pass


class NotHere(LibraryError):
    """No library in the node, or the thing is not in the hands."""


class NotACarrier(LibraryError):
    """Only a written carrier is put into a library."""


class AlreadyThere(LibraryError):
    """The recipe already lies here: the carrier stays with whoever brought it."""


async def entries(session: AsyncSession, node: Node) -> list[LibraryEntry]:
    """What this library holds, by recipe name."""
    stmt = (
        select(LibraryEntry)
        .where(LibraryEntry.node_id == node.id)
        .order_by(LibraryEntry.recipe.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def has(session: AsyncSession, node: Node, recipe: str) -> bool:
    stmt = select(LibraryEntry.id).where(
        LibraryEntry.node_id == node.id, LibraryEntry.recipe == recipe
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def stock(
    session: AsyncSession, node: Node, recipes: Iterable[str]
) -> int:
    """Lay down entries without a contributor: the base set of a genesis library.

    Idempotent -- the seed's catch-up calls it on every start, and what is
    already on the shelf is not laid down twice.
    """
    present = {entry.recipe for entry in await entries(session, node)}
    added = 0
    for recipe in recipes:
        if recipe in present:
            continue
        session.add(LibraryEntry(node_id=node.id, recipe=recipe))
        added += 1
    if added:
        await session.flush()
    return added


async def contribute(
    session: AsyncSession, catalog: Catalog, body: Body, item: Item
) -> LibraryEntry:
    """Give a written carrier to the library one stands in.

    The carrier stays in the library for good -- "given cannot be ungiven" --
    and the giver's name is bound to the entry forever. What is already on the
    shelf is not taken: the second carrier would add nothing, and refusing it
    keeps a thing worth money in its owner's hands rather than swallowing it.
    """
    from src.engine import craft

    if body.state is not BodyState.ALIVE:
        raise LibraryError("мёртвое тело ничего не приносит")
    await travel.require_here(session, body)
    node = await session.get(Node, body.node_id)
    if node is None or not await world_engine.is_library(session, node):
        raise NotHere("Библиотеки здесь нет: рецепт приносят в неё ногами")

    inventory = await body_container(session, body)
    if item.container_id != inventory.id:
        raise NotHere("этой вещи нет в руках")
    if item.type_key not in craft.carrier_names(catalog) or not item.recipe_key:
        raise NotACarrier("в библиотеку кладут записанный носитель — предмет «Рецепт»")
    recipe = catalog.recipes.recipe(item.recipe_key).name
    if await has(session, node, recipe):
        raise AlreadyThere(f"«{recipe}» в этой библиотеке уже есть: носитель остаётся у вас")

    entry = LibraryEntry(
        node_id=node.id, recipe=recipe, contributor_identity_id=body.identity_id
    )
    session.add(entry)
    #: The carrier is the library's now: not on the floor to be picked up, not
    #: in a chest -- on the shelf, and the shelf is the entry.
    await session.delete(item)
    await session.flush()
    await events.record(
        session,
        EventKind.LIBRARY_CONTRIBUTED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        recipe=recipe,
        item_id=str(item.id),
    )
    return entry


async def contributors(
    session: AsyncSession, rows: Iterable[LibraryEntry]
) -> dict[uuid.UUID, str]:
    """Names for the entries' contributors, in one query."""
    ids = {entry.contributor_identity_id for entry in rows if entry.contributor_identity_id}
    if not ids:
        return {}
    found = await session.execute(select(Identity).where(Identity.id.in_(ids)))
    return {identity.id: identity.name for identity in found.scalars().all()}
