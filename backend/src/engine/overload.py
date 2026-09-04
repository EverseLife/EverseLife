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

The frame's lift needs a charged battery in the hands (D-268), and the charge
leaves them by many doors -- drunk to the bottom by the tick, or the cell put
down, given away, sold, spent as a recipe's input. Every one of those lowers
the limit the same way, and one sweep answers them all: the tick finds the
wearer without a charge and sheds there (`gear.wear_exoskeletons`). So the
frame is worn under the load and taken off under it, and there is no third
state where the hands hold what nothing lifts.
"""

from __future__ import annotations

import logging
import math
import uuid
from collections.abc import Collection, Sequence

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


async def shed(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    *,
    floor: float = 0.0,
    spare: Collection[uuid.UUID] = (),
) -> float:
    """The limit fell under a load that did not change: what no longer fits falls.

    Asked by whoever lowers it -- taking off an exoskeleton, putting a lighter
    frame on in its place, or the tick finding a wearer without a charge
    (D-306). Nothing arrived, so nothing is named as what falls: the heaviest
    stack goes first and the rest keeps its place.

    `floor` is an excess this door does not answer for: dressing and undressing
    pass the overload they found, so an act that added four kilograms takes
    four and not the fifty somebody else's door let in. `spare` is what must
    not fall whatever its weight -- the very thing just taken off, which D-306
    keeps in the hands.

    **Three things never fall.** What is **worn** -- it is on the body rather
    than in the hands, and taking one thing off must not silently strip
    another. What was just taken off (`spare`). And, where there is no air to
    breathe (D-233, D-234), the vessels the body breathes from: a fall that
    takes the cylinder off a body standing on Pyroxis is a death sentence
    carried out while the player is offline, and no carry limit is worth that.
    Air is safe to except: nothing but air goes into a breathing cylinder, so
    the exception carries no ore.

    Returns the kilograms that fell.
    """
    from src.engine import oxygen  # noqa: PLC0415 -- lazy: breaks oxygen -> gear -> overload

    pocket = await world.body_container(session, body)
    on_body = await gear.equipped(session, body)
    keep = {thing.id for thing in on_body.values()} | set(spare)
    node = await session.get(Node, body.node_id) if body.node_id is not None else None
    contents = list(await world.contents(session, pocket))
    fills = await storage.contents_of(
        session, [one for one in contents if storage.is_vessel(catalog, one.type_key)]
    )
    if node is not None and not await oxygen.free_air(session, node):
        breath = {one.id for one in await oxygen.cylinders(session, body)}
        keep |= {
            vessel for vessel, drops in fills.items() if any(one.id in breath for one in drops)
        }

    carried = [thing for thing in contents if thing.id not in keep]
    if not carried:
        return 0.0
    weights = weigh(catalog, carried, fills)
    #: Heaviest stack first: the biggest heap is the one the frame was for, and
    #: it is the one that empties the excess in the fewest pieces. By id after
    #: the mass, so two identical stacks fall in a settled order.
    carried.sort(key=lambda thing: (-weights[thing.id], thing.id))

    #: **What cannot fall is a floor the shedding does not go under** (D-306).
    #: Worn gear can weigh more than the bare hands together -- a heavy frame
    #: and a suit do -- and then the excess is the gear's own, and the pocket
    #: cannot answer it: emptying it would take the food, the tool and the air
    #: and leave the body over the limit anyway, every tick and for ever. So
    #: nothing is taken and the state is shouted: gear a bare pair of hands
    #: cannot hold is a question for the vault's numbers (D-065, OQ-128).
    load = await gear.carried_mass(session, catalog, body)
    limit = await gear.capacity(session, constants, catalog, body, on_body)
    #: Measured the way the fall measures: matter, not the felt load, so that a
    #: pack does not make the two answers different kilograms (`gear.matter_over`).
    stuck = gear.matter_over(constants, catalog, on_body, load - sum(weights.values()), limit)
    if stuck > floor + DUST:
        log.error(
            "nothing to shed for body %s: %.1f kg would still be over %.1f kg allowed",
            body.id,
            stuck,
            limit,
        )
        return 0.0
    return await _fall(session, constants, catalog, body, carried, weights, floor=floor)


def weigh(
    catalog: Catalog, items: Sequence[Item], fills: dict[uuid.UUID, list[Item]]
) -> dict[uuid.UUID, float]:
    """What each stack weighs as the **load** counts it, kg.

    A vessel weighs its fill too (D-230, `gear.load_of`), and the tare alone is
    what the falls used to read: a plastic canister of two and a half kilograms
    holding forty sorted as the lightest thing in the hands and, when it did
    fall, was subtracted from the excess as two and a half -- so the ore kept
    going after it and the hands were emptied of everything.
    """
    return {
        one.id: gear.mass_of(catalog, one.type_key, amount_float(one.amount))
        + sum(
            gear.mass_of(catalog, drop.type_key, amount_float(drop.amount))
            for drop in fills.get(one.id, ())
        )
        for one in items
    }


async def fills_of(
    session: AsyncSession, catalog: Catalog, items: Sequence[Item]
) -> dict[uuid.UUID, list[Item]]:
    """What is poured into each vessel among these things, in one reading."""
    return await storage.contents_of(
        session, [one for one in items if storage.is_vessel(catalog, one.type_key)]
    )


async def _fall(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    items: Sequence[Item],
    weights: dict[uuid.UUID, float] | None = None,
    *,
    floor: float = 0.0,
) -> float:
    """The fall itself: read the excess under the body's row, then move matter.

    The body's row is taken for the transaction: the load is read and then
    matter is moved on it, and two arrivals at once must not both find room
    that only one of them has.

    `floor` is an excess this fall is not answering for: what somebody else's
    door let in stays where it is (D-306).
    """
    await session.execute(select(Body.id).where(Body.id == body.id).with_for_update())
    worn = await gear.equipped(session, body)
    load = await gear.carried_mass(session, catalog, body)
    carries = gear.packed(constants, catalog, worn, load)
    limit = await gear.capacity(session, constants, catalog, body, worn)
    #: Matter, not the felt excess: things fall by what they weigh on the
    #: ground, and under a pack the two are different kilograms. `floor` is
    #: measured the same way (`gear._over`), so the two subtract honestly.
    excess = gear.matter_over(constants, catalog, worn, load, limit) - floor
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

    #: The mass the load is counted by, so that what falls subtracts from the
    #: excess exactly what it added to it -- a vessel's fill included (D-230).
    weights = weights or weigh(catalog, items, await fills_of(session, catalog, items))
    fallen = 0.0
    for item in items:
        if excess <= DUST:
            break
        have = amount_float(item.amount)
        unit = weights[item.id] / have if have > 0 else 0.0
        if unit <= 0:
            #: Weightless things -- energy, coin -- never overload anybody.
            continue
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
