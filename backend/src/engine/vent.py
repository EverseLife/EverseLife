# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Vent gas: where the gas a machine gives off goes (D-340).

A vent gas is a liquid of the vault flagged `vent` (`RecipeBook.vent`) --
hydrogen, the byproduct of electrolysis, is the first. It has no use yet, and
it is dangerous where there is air to burn it with. So it is never released
into the air, and it never lies loose either (D-230). One rule, the same on
the ground and aboard:

1. **First into the vessels assigned to it.** Aboard, the vessels on the vent
   port's line, in order (`ship.lines`). On the ground there are no lines.
2. **The rest by what is outside** (`sink`):

   * *no air outside* -- a sealed hull (under way, in orbit, down on an
     airless world), a ground node of an airless world, an orbit: the gas is
     let out without a word. There is nothing there to burn or blow up with
     (`VOID`), and this is the owner's "into the line's vessels, else
     overboard" of 2026-09-13;
   * *air outside* on the ground: the gas burns in a flare stack standing in
     the same node (`FLARE`). The flare eats nothing, draws no current and
     serves every machine of its node;
   * *air outside* aboard -- a hull landed under a sky with air: a hull has
     no flare, and only its line vessels take the gas.

3. **Nowhere at all -- the machine does not work.** A manual batch is refused
   at the door, an automat stands with a reason, and both are decided before
   anything is made: never "made, then released into the air".

A vessel holding a vent gas is emptied the same way (`empty`): overboard or
outside where there is no air, into the flare on the ground under air.

Asks the oxygen floor whether there is air outside (`oxygen._base.free_air`:
the very question a body breathing asks) and nothing above the engine's
floors; `craft`, `automat` and the commands ask it.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog
from src.engine import events, liquid, stock, storage, travel, world
from src.engine.errors import Refusal

#: The oxygen's floor, not its door: whether there is air outside is the
#: floor's question, and the package door would pull breathing, the gauge and
#: the hydroponic beds in behind every batch that only asks about the sky.
from src.engine.oxygen._base import free_air
from src.engine.world import FLARE_STACK
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.models.world import Node, is_aboard
from src.units import amount_float

#: Where a vent gas goes that no vessel took: out, where there is no air --
#: keys of the wire and the journal, never words.
VOID = "void"
#: ... or into the node's flare stack, where there is.
FLARE = "flare"


class VentError(Refusal):
    pass


class NowhereToVent(VentError):
    """Air outside, and nothing here to burn the gas in."""


def gases_of(catalog: Catalog, output: str) -> dict[str, float]:
    """The vent gases a unit of this output gives off, per unit. Empty for
    most things: only a byproduct flagged `vent` is one."""
    book = catalog.recipes
    return {name: per for name, per in book.byproduct_of(output).items() if book.is_vent(name)}


async def flare_in(session: AsyncSession, node: Node) -> Item | None:
    """A flare stack put up in this node, or nothing. A read: a node nobody
    put anything into has no yard, and no yard has no flare."""
    yard = await world.node_yard(session, node)
    if yard is None:
        return None
    return (
        await session.execute(
            select(Item)
            .where(
                Item.container_id == yard.id,
                Item.type_key.in_(world.station_names(FLARE_STACK)),
                #: Put up, not lying (D-278): a flare in parts burns nothing.
                Item.installed.is_(True),
            )
            .limit(1)
        )
    ).scalar_one_or_none()


async def sink(session: AsyncSession, node: Node | None) -> str | None:
    """Where the gas no vessel took goes from this place: `VOID`, `FLARE`, or
    nowhere (`None`) -- and then the machine does not work.

    The flare counts on the ground only: a hull under a sky with air has none,
    and a flare stack standing in a compartment serves nothing (it cannot be
    made there either, `craft.place`).
    """
    if node is None:
        return None
    if not await free_air(session, node):
        return VOID
    if is_aboard(node):
        return None
    return FLARE if await flare_in(session, node) is not None else None


async def empty(
    session: AsyncSession, catalog: Catalog, body: Body, vessel: Item
) -> tuple[str, float, str]:
    """Empty a vessel of its vent gas: out where there is no air, into the
    node's flare where there is. Returns the gas, how much, and where it went.

    Within reach like a pour (`liquid.within_reach`): in the hands, or standing
    here and the body may dispose of the place. The vessel's row is locked
    before the stacks in it -- the order a pour, a batch and an automat take
    them in -- and the stacks are written off under their own lock, so two
    hands emptying one cylinder take what is in it once between them.
    """
    if body.state is not BodyState.ALIVE:
        raise VentError(key="liquid-dead-pours")
    await travel.require_here(session, body)
    if not storage.is_vessel(catalog, vessel.type_key):
        raise liquid.NotVessel(key="liquid-not-a-vessel", vessel=vessel.type_key)
    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body always stands in a node
        raise VentError(key="liquid-body-off-node")
    pocket = await world.body_container(session, body)
    await liquid.within_reach(session, catalog, body, node, pocket, vessel)

    where = await sink(session, node)
    await liquid.lock_vessels(session, [vessel])
    inside = await storage.inside(session, vessel, create=False)
    held = [] if inside is None else list(await world.contents(session, inside))
    if not held:
        raise VentError(key="liquid-source-empty", vessel=vessel.type_key, named="false")
    kept = next((one for one in held if not catalog.recipes.is_vent(one.type_key)), None)
    if kept is not None:
        #: Only a vent gas goes out this way; anything else is poured into
        #: another vessel (D-230), and the refusal says what is in this one.
        raise VentError(key="liquid-not-vent", vessel=vessel.type_key, have=kept.type_key)
    if where is None:
        raise NowhereToVent(
            key="liquid-vent-nowhere",
            vessel=vessel.type_key,
            goods=held[0].type_key,
            aboard="true" if is_aboard(node) else "false",
        )
    gas = held[0].type_key
    stacks = await stock.locked_stacks(session, inside.id, (gas,))
    total = sum(stack.amount for stack in stacks)
    taken = amount_float(await stock.consume(session, stacks, total))
    if taken <= 0:  # pragma: no cover -- emptied by a race between the read and the lock
        raise VentError(key="liquid-source-empty", vessel=vessel.type_key, named="false")
    await events.record(
        session,
        EventKind.STORAGE_VENTED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        item_id=str(vessel.id),
        type_key=gas,
        amount=taken,
        way=where,
    )
    return gas, taken, where
