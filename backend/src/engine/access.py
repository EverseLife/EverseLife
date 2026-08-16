"""The gate of one's own yard: who may enter a plot (D-199).

A plot belonged to its holder, yet anyone could walk in. You cannot work with
somebody else's hands and cannot take somebody else's things -- but standing in
the middle of somebody's yard and watching everything done there was allowed. A
house without a door is not a house.

The rules are three, and they are all here:

* the gate belongs to the plot and to its holder. Land outside a city has no
  owner (D-198), so it has no gate either -- there is nothing to shut and
  nobody to shut it;
* the roster is one. With the gate open it reads as a blacklist, with the gate
  closed as a whitelist. Two lists would need rules for their contradictions,
  and every such rule is one more thing to learn;
* the gate stops arrivals, never departures. Shutting it on a guest inside
  would be a way to take a body away, and death with no way out is forbidden
  (P6). A guest walks out on their own.

Whether a name in the roster is *fair* is not the engine's business: it carries
out the holder's will, and injustice is contested by a suit (D-166).
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.identity import Body, Identity
from src.models.world import Node, NodePass


class AccessError(Exception):
    pass


class NotYours(AccessError):
    """Only the holder runs the gate: it is a property right, not a whim."""


class Barred(AccessError):
    """The gate is shut for this one: the road ends before the fence."""


def _held(node: Node) -> bool:
    """Whether there is anybody at all whose gate this is."""
    return node.owner_identity_id is not None


async def require_holder(session: AsyncSession, node: Node, identity: Identity) -> None:
    if not _held(node):
        #: Two different "no holder" cases, and they are worth telling apart:
        #: civic land is regulated by citizenship and duties, land outside a
        #: city is not privatized at all (D-198).
        raise NotYours(
            "это городская земля: вход на неё решают гражданством и пошлиной, "
            "а не воротами двора"
            if node.owner_city_id is not None
            else "у этой земли нет хозяина: за городом ворот не ставят (D-198)"
        )
    if node.owner_identity_id != identity.id:
        raise NotYours("двор не ваш: воротами распоряжается хозяин")


async def set_gate(
    session: AsyncSession, node: Node, identity: Identity, *, closed: bool
) -> Node:
    """Open or close the yard. Visible from outside: a shut gate is not a trap."""
    await require_holder(session, node, identity)
    node.gated = closed
    await session.flush()
    return node


async def listed(session: AsyncSession, node: Node) -> list[uuid.UUID]:
    rows = await session.execute(
        select(NodePass.identity_id).where(NodePass.node_id == node.id)
    )
    return [row[0] for row in rows]


async def roster(session: AsyncSession, node: Node) -> list[str]:
    """The roster by names: the holder manages people, not identifiers."""
    names = await session.execute(
        select(Identity.name)
        .join(NodePass, NodePass.identity_id == Identity.id)
        .where(NodePass.node_id == node.id)
        .order_by(Identity.name)
    )
    return [row[0] for row in names]


async def add(
    session: AsyncSession, node: Node, identity: Identity, who: Identity
) -> None:
    """Name a person in the roster. The holder is not named: they always enter."""
    await require_holder(session, node, identity)
    if who.id == identity.id:
        raise AccessError("себя в списке не держат: хозяин входит всегда")
    if who.id in await listed(session, node):
        return
    session.add(NodePass(node_id=node.id, identity_id=who.id))
    await session.flush()


async def remove(
    session: AsyncSession, node: Node, identity: Identity, who: Identity
) -> None:
    await require_holder(session, node, identity)
    await session.execute(
        delete(NodePass).where(
            NodePass.node_id == node.id, NodePass.identity_id == who.id
        )
    )
    await session.flush()


async def may_enter(session: AsyncSession, node: Node, identity_id: uuid.UUID) -> bool:
    """Whether this person may set foot on the plot.

    Nobody's and civic land is open: the gate is a right of a private holder,
    and city land is regulated by citizenship and duties, not by a roster.
    """
    if not _held(node):
        return True
    if node.owner_identity_id == identity_id:
        return True

    named = identity_id in await listed(session, node)
    #: The gate turns the meaning of the roster over: closed -- only the named
    #: enter, open -- everyone but the named.
    return named if node.gated else not named


async def require_entry(session: AsyncSession, node: Node, body: Body) -> None:
    """Refuse a road that ends at somebody else's fence."""
    if await may_enter(session, node, body.identity_id):
        return
    raise Barred(
        f"«{node.name}» — чужой двор, и хозяин вас туда не пускает. "
        "Спор о входе решается иском (D-166)"
    )
