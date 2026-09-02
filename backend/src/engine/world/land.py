# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The ground the world stands on: nodes, where they hang, who holds them.

One subject, because a node is all three things at once and they are never
asked apart. A node is born with an area and a place on the map (D-237); where
it hangs -- a plot in a city, a room off a corridor, a planet on its orbit
(D-045) -- is written into the same row; and a holder is the same row changing
hands. The vein is here for the same reason: it is a property of the ground,
not a thing lying on it, and it is created with the node it lies under.

Nothing here knows about people or about what stands in a node. That is the
whole point of the cut: land exists before anybody is printed onto it, so this
module is the one the other two are written on top of.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import remember
from src.engine import events, places
from src.engine.errors import Refusal
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.inventory import Container, ContainerKind
from src.models.world import PLOT, Layer, Node, Planet, Vein
from src.units import amount as to_amount

#: Where a planet stands on the space layer (D-045). Radius and period are
#: **display** numbers: orbits are not to scale (10-world/06), and the period
#: says how often a launch window comes round rather than anything about a
#: planet's astronomy. The phase is where the planet stood at the world's
#: epoch, so every client draws one and the same sky.
ORBIT = "orbit"
ORBIT_RADIUS = "radius"
ORBIT_PERIOD = "period"
ORBIT_PHASE = "phase"
#: Drawn but not yet playable (D-104). Aquatica is on the map from the first
#: day so that a player sees where they cannot go -- the vault asks for exactly
#: that: unreachable routes are shown, marked as unreachable.
DEFERRED = "deferred"

#: The place signs the public map may show (D-238): an allowlist, never
#: "everything true". A boolean property added tomorrow must not leak to the
#: unauthenticated internet silently, and what only `look` should say to
#: whoever stands in the node stays with `look`. Deliberately narrow: the
#: node-type glyphs the client draws, and nothing else.
PUBLIC_SIGNS = ("precursors", "stones", "woods", "meadow", PLOT)


def public_signs(node: Node) -> list[str]:
    """The node's place signs as the public map serves them."""
    held = node.properties or {}
    return [name for name in PUBLIC_SIGNS if held.get(name) is True]


#: The place mark whose value is a word rather than a flag: a node's water is
#: `river` or `none` (D-126), and both are non-empty. Truthiness alone -- which
#: is what a place check is for every other mark -- would call a dry waste
#: watered, so the one word that means water is named here.
WATER = "water"
RIVER = "river"
#: The same property saying there is none. A word, not an absence: a node
#: whose water was never rolled and one rolled dry read alike to `has_place`.
NO_WATER = "none"


def has_place(node: Node | None, mark: str) -> bool:
    """Whether the node carries this place mark (D-177, D-254).

    One question with one answer, asked by everything that binds work to the
    land: the felling operation, and every find of the walk over a plot. It
    was two before, and the second one was wrong -- `water` is the only mark
    stored as a word, so a bare `properties.get("water")` said yes to `none`.
    """
    if node is None:
        return False
    held = (node.properties or {}).get(mark)
    if mark == WATER:
        return held == RIVER
    return bool(held)


async def epoch(session: AsyncSession) -> datetime | None:
    """When the world began: the birth of its first node.

    The world is eternal and has no wipes (D-007), so that moment never moves
    -- which is what makes it usable as the origin of every count that must
    agree between the server and every client: the planet's clock (D-029) and
    the angle a planet stands at on its orbit.
    """

    #: Once per command: the clock and the orbit both ask, and the answer
    #: never changes. The index on `created_at` makes the one ask cheap.
    async def find() -> datetime | None:
        return await session.scalar(select(func.min(Node.created_at)))

    return await remember(session, ("epoch",), find)


def orbit_of(node: Node) -> dict[str, float] | None:
    """The node's orbit for the client, or None if the node does not go round anything.

    The data keys are the world's own ("радиус", "период", "фаза"), the wire
    keys are the code's. The translation lives here alone: two places for it
    would drift apart on the first added field.
    """
    circle = (node.properties or {}).get(ORBIT)
    if not isinstance(circle, dict):
        return None
    return {
        "radius": float(circle[ORBIT_RADIUS]),
        "period_days": float(circle[ORBIT_PERIOD]),
        "phase": float(circle[ORBIT_PHASE]),
    }


async def create_node(
    session: AsyncSession,
    key: str,
    name: str,
    *,
    planet: Planet = Planet.TERRA,
    area_m2: float,
    properties: dict[str, Any] | None = None,
    layer: Layer = Layer.CITY,
    parent: Node | None = None,
    anchor: Node | None = None,
) -> Node:
    """A new node of the world.

    `anchor` is the node this one is laid next to -- the node a scout left
    from, the corridor a room opened off, the port a keel was laid at. It
    decides where the new node stands on the map, once and for everybody
    (D-237, `engine.places`): without it the node lands at its group's origin,
    which is right only for the group's own first node.
    """
    node = Node(
        key=key,
        name=name,
        planet=planet,
        layer=layer,
        parent_id=None if parent is None else parent.id,
        area_m2=Decimal(str(area_m2)),
        properties=properties or {},
    )
    session.add(node)
    await session.flush()
    #: The place is given here and never again: a map that redrew itself would
    #: be the one thing in an eternal world that does (D-007, D-237).
    await places.assign(session, node, anchor=anchor or parent)
    #: The yard is born with the node, as the pocket is with the body:
    #: otherwise the first `look` at a new place creates it, and a read
    #: must not write (review 2026-08-23). Old nodes without one are still
    #: caught by `node_container`.
    session.add(Container(kind=ContainerKind.NODE, owner_id=node.id))
    await session.flush()
    return node


class LandError(Refusal):
    pass


async def hand_over(session: AsyncSession, node: Node, owner_id: uuid.UUID | None) -> None:
    """Give the plot a holder -- and with it the floors of the house on it (D-247).

    A storey is not held apart from the ground it stands on: it is not bought,
    not sold and not fenced on its own. Land changes hands in six places -- a
    purchase, an allotment, a deed sold, a plot ceded, a city founded, a wild
    node granted -- and every one of them must carry the whole house, or the
    seller keeps the workshop upstairs and the buyer cannot reach it.

    Written here rather than in `estate`: this is a fact about the node tree,
    and `estate` is what the node tree is read by.
    """
    #: The plot's row for the transaction. The floors are read **after** it is
    #: taken: a build finishing in another session opens its rooms with the
    #: holder it read before this one wrote, and without the lock the plot would
    #: go to the buyer while the workshop upstairs stayed with the seller.
    await session.execute(
        select(Node)
        .where(Node.id == node.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    node.owner_identity_id = owner_id
    rooms = (
        (
            await session.execute(
                select(Node).where(Node.parent_id == node.id, Node.layer == Layer.LOCATION)
            )
        )
        .scalars()
        .all()
    )
    for room in rooms:
        room.owner_identity_id = owner_id
    await session.flush()


async def grant_node(session: AsyncSession, node: Node, owner: Identity) -> Node:
    """Hand a plot to a person: title plus the deed for it (D-116, D-198).

    Land outside a city is not taken by anybody -- there used to be
    `claim_node`, which took a wild node on foot and issued a deed for it. That
    let the first comer lock up a grove, a meadow or a stony slope whole, and
    the foraging on it (D-196, D-210): somebody else's place gives no work.
    Title is issued by a city and only by a city, so the plot arrives here
    already civic -- through purchase (`estate.buy`) or the founding of a city.

    Working on nobody's land stays open to everyone: build, fell, gather, drop
    things on the ground. The ban is on the title, not on the labour.

    **Whether the node is a plot is the caller's to have asked** (D-281). A
    city's own location -- its core, its market, its administration -- is not
    handed to a person at all, and both roads to here refuse one before they
    call: `city.allot` and `estate.buy`. The question is not repeated here
    because there is no third road: the seed and the tests are what is left,
    and neither is a player. A new caller answers it first.
    """
    from src.engine import estate  # noqa: PLC0415 -- lazy: breaks the import cycle with estate

    if node.owner_city_id is None:
        raise LandError(key="land-outside-city")
    if node.owner_identity_id is not None:
        raise LandError(key="land-already-owned")

    await hand_over(session, node, owner.id)
    await estate.issue_deed(session, node, owner.id)

    await events.record(
        session,
        EventKind.LAND_CLAIMED,
        actor_identity_id=owner.id,
        node_id=node.id,
    )
    return node


async def create_vein(
    session: AsyncSession,
    node: Node,
    resource: str,
    *,
    richness: float,
    remaining: float,
) -> Vein:
    vein = Vein(
        node_id=node.id,
        resource=resource,
        richness=Decimal(str(richness)),
        remaining=to_amount(remaining),
    )
    session.add(vein)
    await session.flush()
    return vein
