# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Who may enter a location, and who only passes through (D-199, D-204).

A plot belonged to its holder, yet anyone could walk in. You cannot work with
somebody else's hands and cannot take somebody else's things -- but standing in
the middle of somebody's yard and watching everything done there was allowed. A
house without a door is not a house.

The rules are four, and they are all here:

* **the door is a property of the plot, not of the city** (D-199). Land outside
  a city has no owner (D-198), so it has no door either -- there is nothing to
  shut and nobody to shut it; and a city's own location -- its core with the
  printer, its market, its administration -- has none either, whoever holds the
  title to it. A city location is entered by everyone;
* **shutting stops entry, not passage** (D-204). A route goes straight through a
  shut location: the refusal comes to whoever tries to arrive, not to whoever
  builds a path. Otherwise one holder's will could cut a neighbour off from
  their own home, and the graph would fall apart at the map's weakest place;
* **the lists are two.** The white one gets into a shut location, the black one
  gets in nowhere. Where they contradict each other, black beats white -- one
  line, and shorter than the old rule where a single roster flipped its meaning
  with the gate. The holder always enters;
* the door stops arrivals, never departures. Shutting it on a guest inside
  would be a way to take a body away, and death with no way out is forbidden
  (P6). A guest walks out on their own.

Whether a name in a list is *fair* is not the engine's business, and it is
nobody else's either: entry is the holder's to decide and there is no court
above that decision. A door is not a wrong to be undone -- it is what holding a
plot means.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine.errors import Refusal
from src.models.identity import Body, Identity
from src.models.world import Node, NodePass, is_plot, storey_of


class AccessError(Refusal):
    pass


class NotYours(AccessError):
    """Only the holder runs the door: it is a property right, not a whim."""


class Barred(AccessError):
    """Entry is refused for this one: the road ends before the fence."""


def _held(node: Node) -> bool:
    """Whether there is anybody at all whose door this is.

    Two facts, not one. A holder there must be -- and the location must be a
    **plot**, one of those the authority hands out inside its rings (D-089).
    The gate is a property of the plot, not of the city (D-199): the core with
    the printer, the market, the administration are the city's own places, and
    a title over one of them is a title, not a door. Otherwise one allotment
    signed by one office would shut the capital's centre to everybody -- and
    the printer people come back to life at is in it.

    Outside a city there is no title at all (D-198), so a node with a holder
    and no city over it is a hull's compartment or a floor of a house: those
    are their holder's whole, and the plot mark says nothing about them.
    """
    if node.owner_identity_id is None:
        return False
    if node.owner_city_id is not None:
        return is_plot(node)
    return True


def has_door(node: Node) -> bool:
    """Whether this location has a door at all -- to shut, to list, to refuse at.

    The one place the question is answered. It used to be answered twice: the
    window that draws the gate and the two list fields asked "is it mine and is
    it ground", and the engine asked something else -- so on a city location
    still standing in somebody's name the holder was shown a switch and two
    fields where every button refuses.
    """
    return storey_of(node) is None and _held(node)


async def require_holder(session: AsyncSession, node: Node, identity: Identity) -> None:
    #: The door belongs to the place, and a floor of a house is not one (D-247):
    #: the way in is the plot below, and shutting it shuts the stairs with it.
    #: A second door upstairs would be one the guest feels and the holder never
    #: sees -- no window shows it, and nothing would open it again. A storey
    #: only: a compartment aboard is a room of a hull that belongs to one person
    #: whole, and its door was never the plot's (D-202).
    if storey_of(node) is not None:
        raise NotYours(key="access-door-downstairs")
    if not _held(node):
        #: Two different "no holder" cases, and they are worth telling apart:
        #: civic land is regulated by citizenship and duties, land outside a
        #: city is not privatized at all (D-198).
        raise NotYours(
            key="access-no-holder",
            land="city" if node.owner_city_id is not None else "wild",
        )
    if node.owner_identity_id != identity.id:
        raise NotYours(key="access-not-yours")


async def set_gate(session: AsyncSession, node: Node, identity: Identity, *, closed: bool) -> Node:
    """Shut the location for entry, or open it. Visible from outside: not a trap.

    Passage is not touched by this: through a shut location one still walks (D-204).
    """
    await require_holder(session, node, identity)
    node.gated = closed
    await session.flush()
    return node


async def listed(
    session: AsyncSession, node: Node, *, allowed: bool | None = None
) -> list[uuid.UUID]:
    """Names in the location's lists: one list, or both when not asked."""
    query = select(NodePass.identity_id).where(NodePass.node_id == node.id)
    if allowed is not None:
        query = query.where(NodePass.allowed == allowed)
    rows = await session.execute(query)
    return [row[0] for row in rows]


async def roster(session: AsyncSession, node: Node, *, allowed: bool = True) -> list[str]:
    """One list by names: the holder manages people, not identifiers."""
    names = await session.execute(
        select(Identity.name)
        .join(NodePass, NodePass.identity_id == Identity.id)
        .where(NodePass.node_id == node.id, NodePass.allowed == allowed)
        .order_by(Identity.name)
    )
    return [row[0] for row in names]


async def add(
    session: AsyncSession,
    node: Node,
    identity: Identity,
    who: Identity,
    *,
    allowed: bool = True,
) -> None:
    """Name a person in a list. The holder is not named: they always enter.

    A name already in the other list moves over: one person is in one list, and
    the two lists never argue about the same name.
    """
    await require_holder(session, node, identity)
    if who.id == identity.id:
        raise AccessError(key="access-self-in-list")
    row = (
        await session.execute(
            select(NodePass).where(NodePass.node_id == node.id, NodePass.identity_id == who.id)
        )
    ).scalar_one_or_none()
    if row is None:
        session.add(NodePass(node_id=node.id, identity_id=who.id, allowed=allowed))
    else:
        row.allowed = allowed
    await session.flush()


async def remove(session: AsyncSession, node: Node, identity: Identity, who: Identity) -> None:
    await require_holder(session, node, identity)
    await session.execute(
        delete(NodePass).where(NodePass.node_id == node.id, NodePass.identity_id == who.id)
    )
    await session.flush()


async def may_enter(session: AsyncSession, node: Node, identity_id: uuid.UUID) -> bool:
    """Whether this person may **stop** in the location -- enter it, not pass it.

    Nobody's and civic land is open: the door is a right of a private holder,
    and city land is regulated by citizenship and duties, not by a list.
    """
    if not _held(node):
        return True
    if node.owner_identity_id == identity_id:
        return True

    rows = (
        (
            await session.execute(
                select(NodePass.allowed).where(
                    NodePass.node_id == node.id, NodePass.identity_id == identity_id
                )
            )
        )
        .scalars()
        .all()
    )
    #: Black beats white: named in the black list -- no entry, shut or open.
    if any(not allowed for allowed in rows):
        return False
    #: A shut location lets in the white list; an open one lets in everybody
    #: whom the black list does not name.
    return bool(rows) if node.gated else True


async def require_entry(session: AsyncSession, node: Node, body: Body) -> None:
    """Refuse a road that ends at somebody else's fence.

    Only arrival is refused: passage through the same location is free, and the
    route through it is built (D-204).
    """
    if await may_enter(session, node, body.identity_id):
        return
    raise Barred(key="access-barred", node=node.name)
