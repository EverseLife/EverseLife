# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""craft: knowledge carriers (D-209).

Split out of `engine/craft.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, current
from src.constants import registry as R
from src.engine import events
from src.engine.craft._base import CraftError, Unmakeable, blank_of, carrier_names
from src.engine.craft._internal import _knows, _num
from src.engine.craft.batch import _pay_copy
from src.engine.world import body_container, learn
from src.models.event import EventKind
from src.models.identity import Body, BodyState, Identity, Knowledge
from src.models.inventory import Item


async def read_carrier(
    session: AsyncSession, catalog: Catalog, body: Body, item: Item
) -> Knowledge | None:
    """Copy the recipe off a carrier in the hands into the identity.

    The carrier is not spent: one carrier teaches many (03-crafting). What is
    spent is stamina, the same as at a library shelf (D-148) -- reading is work
    wherever the text lies. Works anywhere the body is: a carrier is in the
    hands, and the hands are always with you.
    """
    if body.state is not BodyState.ALIVE:
        raise CraftError(key="craft-dead-reads")
    inventory = await body_container(session, body)
    if item.container_id != inventory.id:
        raise CraftError(key="craft-carrier-not-in-hands")
    if item.type_key not in carrier_names(catalog) or not item.recipe_key:
        raise Unmakeable(key="craft-carrier-blank")
    recipe = catalog.recipes.recipe(item.recipe_key).type_key
    if await _knows(session, body, recipe):
        return None
    identity = await session.get(Identity, body.identity_id)
    if identity is None:  # pragma: no cover
        raise CraftError(key="craft-body-without-identity")
    await _pay_copy(current(), body)
    await session.flush()
    learned = await learn(session, identity, recipe)
    await events.record(
        session,
        EventKind.CARRIER_READ,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(item.id),
        recipe=recipe,
    )
    return learned


async def wipe_carrier(session: AsyncSession, catalog: Catalog, body: Body, item: Item) -> Item:
    """Erase a carrier: the recipe is gone from it, the blank is back in the hands.

    Nothing else about the thing changes -- its quality, mark and wear stay:
    it is the same piece of memory, empty again.
    """
    if body.state is not BodyState.ALIVE:
        raise CraftError(key="craft-dead-wipes")
    inventory = await body_container(session, body)
    if item.container_id != inventory.id:
        raise CraftError(key="craft-carrier-not-in-hands")
    if item.type_key not in carrier_names(catalog):
        raise Unmakeable(key="craft-wipe-not-a-carrier")
    was = item.recipe_key
    item.type_key = blank_of(catalog, item.type_key)
    item.recipe_key = None
    #: Erasing wears the memory as writing does; at zero the blank is dead --
    #: it can still be sold or melted down, but not written on (D-209).
    if item.quality is not None:
        scale = current()[R.QUALITY_SCALE]
        item.quality = _num(scale.clamp(float(item.quality) - current()[R.CARRIER_WIPE_WEAR]))
    await session.flush()
    await events.record(
        session,
        EventKind.CARRIER_WIPED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        item_id=str(item.id),
        recipe=was,
    )
    return item
