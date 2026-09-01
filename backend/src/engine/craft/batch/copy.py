# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Copying a recipe at the shelf (D-148): the body locked for the whole
copy, the stamina paid once, the knowledge written once.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current
from src.constants import registry as R
from src.engine import travel
from src.engine import world as world_engine
from src.engine.craft._base import (
    CraftError,
    NoLibrary,
    NoStrength,
)
from src.engine.craft._internal import (
    _knows,
)
from src.engine.world import learn
from src.models.identity import Body, Identity, Knowledge
from src.models.world import Node


async def copy_recipe(
    session: AsyncSession, catalog: Catalog, body: Body, key: str
) -> Knowledge | None:
    """Copy a recipe from the Library.

    Free of money, unconditional and without citizenship -- and **does not work
    remotely**: the Library's only restriction is geographic (D-053).

    But not for nothing: copying costs `craft.copy_stamina` stamina (D-148).
    The body pays, not the account -- and knowledge stays a public good while no
    longer being a "learn the whole list in one go" button.
    """
    constants = current()
    await travel.require_here(session, body)
    node = await session.get(Node, body.node_id)
    #: The library is a machine (D-176): recipes are taken where it stands.
    if node is None or not await world_engine.is_library(session, node):
        raise NoLibrary(key="craft-no-library")

    recipe = catalog.recipes.recipe(key)
    #: A library holds what was put into it (D-068, D-209): the capital's has
    #: the base set, a city's has what people brought. What is not on the shelf
    #: is not here to copy -- go where it is, or bring it.
    from src.engine import library  # noqa: PLC0415 -- lazy: breaks the import cycle with library

    if not await library.has(session, node, recipe.type_key):
        raise NoLibrary(key="craft-library-lacks", recipe=recipe.type_key)
    identity = await session.get(Identity, body.identity_id)
    if identity is None:  # pragma: no cover
        raise CraftError(key="craft-body-without-identity")

    await _lock_body(session, body)

    #: What is already known is not rewritten: the same body does not pay twice.
    #: Under the lock, or the "twice" is exactly what happens: two sockets of one
    #: identity both find the recipe unknown, both pay, and only the first of
    #: them learns anything -- the second `learn` sees the committed row and
    #: returns nothing, having charged for it.
    if await _knows(session, body, recipe.type_key):
        return None

    _pay_copy(constants, body)
    return await learn(session, identity, recipe.type_key)


async def _lock_body(session: AsyncSession, body: Body) -> None:
    """Take the body's row before the reads that decide the payment.

    Stamina is on the same list as money and remainders (CLAUDE.md): read
    outside a lock, two sockets of one identity both find the reserve enough
    and both write their own remainder -- one copy paid for two. The lock also
    has to cover the knowledge check, or the same pair pays twice for one
    recipe. `mining.swing` carries the full account of the pattern, including
    why the flush comes before the reread.
    """
    await session.flush()
    await session.refresh(body, with_for_update=True)


def _pay_copy(constants: Constants, body: Body) -> None:
    """Copying costs stamina, at a library shelf and off a carrier alike (D-148).

    The caller holds the body's row (`_lock_body`) -- this only spends it.
    """
    spend = constants[R.CRAFT_COPY_STAMINA]
    if spend > float(body.stamina):
        raise NoStrength(key="craft-no-strength", need=spend, have=float(body.stamina))
    body.stamina = Decimal(str(float(body.stamina) - spend))
