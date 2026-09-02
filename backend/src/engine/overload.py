# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What does not fit in the hands falls underfoot (D-265).

The carry limit (D-146) stands at every door a thing is *taken* through --
the ground, a chest, a parcel, the counter, the hopper -- and at none of the
doors a thing *arrives* through on its own: a batch pays out into the
master's hands, the alpha printer prints into them. Until this module the
hands simply took it, and a body walked off with a nine-hundred-kilogram
station it could never have picked up (playtest 2026-09-02).

The rule is the plain one: what arrived past the limit falls to the surface
underfoot -- the floor of the house or the open ground -- in whole pieces if
it is counted, by the excess mass if it is measured. The floor's own budget
(D-192) is no door here: matter cannot vanish because the room is full, so
it lies there anyway and the floor is **overfull** -- written to the journal
and shouted in the log, because every such case is a question for somebody
to look into, not a state the game meant to reach.
"""

from __future__ import annotations

import logging
import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import estate, events, gear, goods, storage, world
from src.models.event import EventKind
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Node
from src.units import amount_float

log = logging.getLogger(__name__)

#: Below this a float excess is the arithmetic's dust, not a piece owed.
_DUST = 1e-9


async def settle_load(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body, item: Item
) -> float:
    """After a thing landed in the hands past any door: what does not fit falls.

    `item` is the thing that just arrived -- it is the one that falls, never
    what was carried before it. Returns the kilograms that fell. The body's
    row is taken for the transaction: the load is read and then matter is
    moved on it, and two arrivals at once must not both find room that only
    one of them has.
    """
    await session.execute(select(Body.id).where(Body.id == body.id).with_for_update())
    carries = await gear.load_of(session, catalog, body)
    limit = await gear.capacity(session, constants, catalog, body)
    excess = carries - limit
    if excess <= _DUST:
        return 0.0
    unit = gear.mass_of(catalog, item.type_key, 1.0)
    if unit <= 0:
        #: Weightless things -- energy, coin -- never overload anybody.
        return 0.0
    have = amount_float(item.amount)
    #: Whole pieces of a counted thing, the excess mass of a measured one --
    #: and never more than arrived.
    if goods.counted(item.type_key, catalog):
        quantity = min(have, float(math.ceil(excess / unit - _DUST)))
    else:
        quantity = min(have, excess / unit)
    if quantity <= 0:
        return 0.0

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body without a node is a bug
        return 0.0
    #: The surface a person would name without thinking (`storage.drop`):
    #: the floor under a roof, the ground where there is none.
    inside = await storage.surface_of(session, node, None)
    area = (
        await estate.space(session, constants, node)
        if inside
        else await estate.yard(session, constants, node)
    )
    mass = unit * quantity
    overfull = mass / constants[R.BUILD_FLOOR_PER_M2] > area["free"]

    yard = await world.node_container(session, node)
    fell = await world.move_stack(session, item, yard, quantity, outdoors=not inside)
    await events.record(
        session,
        EventKind.ITEM_FELL,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        type_key=item.type_key,
        amount=fell,
        roofed=inside,
        carries=carries,
        limit=limit,
    )
    if overfull:
        #: Loud on purpose: the floor was full and the thing lay down anyway.
        #: Whoever reads the log is meant to ask how the body got that heavy.
        log.error(
            "floor overfull at %s: %.1f kg of %s fell with %.1f m2 free (body %s, %.1f/%.1f kg)",
            node.key,
            mass,
            item.type_key,
            area["free"],
            body.id,
            carries,
            limit,
        )
        await events.record(
            session,
            EventKind.STORAGE_OVERFULL,
            actor_identity_id=body.identity_id,
            node_id=node.id,
            type_key=item.type_key,
            mass=mass,
            free=area["free"],
            roofed=inside,
        )
    return unit * fell
