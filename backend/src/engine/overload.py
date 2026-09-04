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

## The other side of the same hole (D-306)

D-265 and D-268 both say it outright -- an overloaded body does not exist any
more -- and both meant the doors a thing **comes in** through. The limit can also
**fall** while the hands stay as full as they were: an exoskeleton is taken
off, or a lighter frame put on in its place, and a hundred kilograms that only
the frame could lift stay in the pocket and walk out of the node. Then the
carry limit is again a door for some things and a decoration for others -- the
very state D-265 was written against, reached from the other side (report
2026-09-04). So `shed` is the same fall, asked by whoever lowers the limit.

Which stack falls is the one thing the two doors cannot share. Nothing has
just arrived, so "what arrived falls" has nothing to name: the heaviest goes
first, until what is left fits. What is **worn** never falls -- it is on the
body rather than in the hands, and taking one thing off must not silently
strip another.

What this does **not** close: the frame's lift needs a charged battery in the
hands (D-268), and that battery leaves the hands by other doors -- put down,
given away, sold, spent as a recipe's input -- or simply runs dry in the tick.
Every one of those lowers the limit the same way. Whether the load should fall
there too is a question about dropping cargo in the middle of a road, not
about a one-click exploit, and it is open (OQ-122).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import estate, events, gear, goods, storage, world
from src.models.event import EventKind
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Node
from src.units import AMOUNT_SCALE, amount_float

log = logging.getLogger(__name__)

#: Below a thousandth an excess is the arithmetic's dust, not a piece owed.
#: Public because whoever lowers a limit compares the excess before and after
#: against it (`gear._settle`), and two thresholds for one question would drift.
DUST = 1 / AMOUNT_SCALE


async def settle_load(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    items: Sequence[Item],
) -> float:
    """After things landed in the hands past any door: what does not fit falls.

    `items` are the things that just arrived -- they are what falls, in the
    order given, never what was carried before them. Returns the kilograms
    that fell.
    """
    if not items:
        return 0.0
    return await _fall(session, constants, catalog, body, items)


async def shed(session: AsyncSession, constants: Constants, catalog: Catalog, body: Body) -> float:
    """The limit fell under a load that did not change: what no longer fits falls.

    Asked by whoever lowers it -- taking off an exoskeleton, putting a lighter
    frame on in its place (D-306). Nothing arrived, so nothing is named as what
    falls: the heaviest stack goes first and the rest keeps its place, and what
    is worn is not touched at all.

    Returns the kilograms that fell.
    """
    pocket = await world.body_container(session, body)
    worn = {thing.id for thing in (await gear.equipped(session, body)).values()}
    carried = [thing for thing in await world.contents(session, pocket) if thing.id not in worn]
    #: Heaviest stack first: the biggest heap is the one the frame was for, and
    #: it is the one that empties the excess in the fewest pieces. By id after
    #: the mass, so two identical stacks fall in a settled order.
    carried.sort(
        key=lambda thing: (
            -gear.mass_of(catalog, thing.type_key, amount_float(thing.amount)),
            thing.id,
        )
    )
    if not carried:
        return 0.0
    return await _fall(session, constants, catalog, body, carried)


async def _fall(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    items: Sequence[Item],
) -> float:
    """The fall itself: read the excess under the body's row, then move matter.

    The body's row is taken for the transaction: the load is read and then
    matter is moved on it, and two arrivals at once must not both find room
    that only one of them has.
    """
    await session.execute(select(Body.id).where(Body.id == body.id).with_for_update())
    carries = await gear.load_of(session, constants, catalog, body)
    limit = await gear.capacity(session, constants, catalog, body)
    excess = carries - limit
    if excess <= DUST:
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
    yard = await world.node_container(session, node)

    fallen = 0.0
    for item in items:
        if excess <= DUST:
            break
        unit = gear.mass_of(catalog, item.type_key, 1.0)
        if unit <= 0:
            #: Weightless things -- energy, coin -- never overload anybody.
            continue
        have = amount_float(item.amount)
        #: Whole pieces of a counted thing, the excess mass of a measured one --
        #: and never more than arrived.
        if goods.counted(item.type_key, catalog):
            quantity = min(have, float(math.ceil(excess / unit - DUST)))
        else:
            quantity = min(have, excess / unit)
        if quantity <= 0:
            continue
        fell = await world.move_stack(session, item, yard, quantity, outdoors=not inside)
        mass = unit * fell
        excess -= mass
        fallen += mass
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

    if fallen > 0 and fallen / constants[R.BUILD_FLOOR_PER_M2] > area["free"]:
        #: Loud on purpose: the floor was full and the things lay down anyway.
        #: Whoever reads the log is meant to ask how the body got that heavy.
        log.error(
            "floor overfull at %s: %.1f kg fell with %.1f m2 free (body %s, %.1f/%.1f kg)",
            node.key,
            fallen,
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
            mass=fallen,
            free=area["free"],
            roofed=inside,
        )
    return fallen
