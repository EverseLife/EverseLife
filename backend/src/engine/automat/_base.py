# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The automat's vocabulary and floor: the machine class and its oils, every
refusal a programme can meet, and the guards -- which machine stands here,
what it may build, what its owner knows. Asks nobody above itself.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.engine import station, travel, world
from src.engine.craft import HANDS, Procedure, procedure
from src.engine.errors import Refusal
from src.models.automat import Automat as AutomatRow
from src.models.identity import Body, BodyState, Knowledge, KnowledgeKind
from src.models.inventory import Item
from src.models.world import Node
from src.units import (
    AMOUNT_SCALE,
)

#: The automat thing class (D-253): its members come from the vault, and the
#: node-editor window opens by this class on the client.
AUTOMAT = "automaton"

#: A piece is judged done at the world's own granularity (D-212): amounts
#: split into thousandths, and the last digit of a representation must not
#: hold a finished piece back.
_EPS = 1 / AMOUNT_SCALE

#: The lubricant thing class (D-253): what the machines drink, by the hour.
LUBE = "lube"


class AutomatError(Refusal):
    pass


class NotAnAutomat(AutomatError):
    """Not an automat: programs are loaded into the family of D-253 and nothing else."""


class NotCovered(AutomatError):
    """The recipe's station is not this automat's group (`auto.covers`)."""


class BarredInput(AutomatError):
    """The pyroxite tier is barred until its own station exists (OQ-106)."""


class NoStationBuilds(AutomatError):
    """A station is a build: its scale is set by hand, never by a machine (D-223)."""


class RecipeUnknown(AutomatError):
    """The owner does not know the recipe: the machine is not a free library (D-253)."""


class SelfLink(AutomatError):
    """A machine does not feed itself: the wire needs two ends (D-253)."""


async def of_item(session: AsyncSession, item: Item) -> AutomatRow | None:
    """The automat row of this machine, if it was ever programmed."""
    return (
        await session.execute(select(AutomatRow).where(AutomatRow.item_id == item.id))
    ).scalar_one_or_none()


# --- internal ----------------------------------------------------------------


async def _machine_here(session: AsyncSession, body: Body, item: Item) -> Node:
    """Alive, here, entitled, and this thing is an automat standing in this node."""
    if body.state is not BodyState.ALIVE:
        raise AutomatError(key="auto-dead-works")
    await travel.require_here(session, body)
    if item.type_key not in world.station_names(AUTOMAT):
        raise NotAnAutomat(key="auto-not-an-automat", goods=item.type_key)
    #: Put up, not lying (D-278): a machine on the floor as cargo works nothing.
    if not item.installed:
        raise NotAnAutomat(key="auto-not-installed", goods=item.type_key)
    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body without a node is a bug
        raise AutomatError(key="auto-body-off-node")
    yard = await world.node_container(session, node)
    if item.container_id != yard.id:
        raise AutomatError(key="auto-not-here")
    #: The same door as a chest's (D-181): whoever may dispose of the node
    #: programs its machines.
    if not await station.may_build(session, body, node):
        raise AutomatError(key="auto-not-entitled")
    return node


def _programmable(constants: Constants, catalog: Catalog, item: Item, recipe_key: str) -> Procedure:
    """The procedure, if this machine may run it at all (D-253).

    `procedure` itself refuses dishes and coins; here go the automat's own
    limits: no station builds, no pyroxite tier, and the station of the
    recipe must be this automat's group.
    """
    book = catalog.recipes
    canonical = book.resolve(recipe_key)
    #: An operation product (an ingot) has no recipe row -- and no kind to bar.
    found = next((r for r in book.recipes if r.type_key == canonical), None)
    if found is not None and found.kind is ItemKind.STATION:
        raise NoStationBuilds(key="auto-no-station-builds", goods=canonical)
    proc = procedure(catalog, canonical)
    barred = constants[R.AUTO_BARRED_INPUTS]
    for name in (*proc.inputs, canonical):
        if name in barred:
            raise BarredInput(key="auto-barred-input", goods=canonical)
    covers = constants[R.AUTO_COVERS]
    group = covers.get(proc.station or "", {})
    if item.type_key not in group:
        raise NotCovered(
            key="auto-not-covered",
            goods=item.type_key,
            station=proc.station or HANDS,
        )
    return proc


async def _knows(session: AsyncSession, body: Body, key: str) -> bool:
    stmt = select(Knowledge).where(
        Knowledge.identity_id == body.identity_id,
        Knowledge.kind == KnowledgeKind.RECIPE,
        Knowledge.key == key,
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None
