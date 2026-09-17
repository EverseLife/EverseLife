# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What the world takes: a thing leaves whole, or it leaves half-way.

A thing is more than its row. It may own a container -- the inside of a
chest (D-181), the hold of a cart (D-157) -- and it may be held by a body's
harness. Deleting the row alone leaves the rest behind: goods alive for ever
in a place that no longer exists, or a harness pointing at nothing, which the
database refuses (`fk_harness_item_id_item`), taking down with it the whole
transaction that ended the thing -- the fire over a field, the fall of a
house, the finish of a batch.

`destroy` is the door such an ending goes through: the fire (D-197), the rift
under a walker (D-233), the roof that falls (D-244, D-247), what a death does
not leave lying (D-012), a thing taken apart. It does not ask whether the
ending is allowed, nor whether what lies inside should have been poured out
first -- whether a full chest may be taken apart at all is the caller's
question (OQ-177), and it has been answered by the time a thing reaches here.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import stock
from src.models.inventory import INSIDE_KINDS, Container, Item
from src.models.travel import Harness
from src.units import amount_float


async def destroy(session: AsyncSession, things: Sequence[Item]) -> dict[str, float]:
    """Take these things out of the world, whatever is inside them with them.

    Returns how much went, by kind -- the insides counted along with the
    things, because they are gone just the same.

    **All the way down.** A chest goes inside a chest (`storage.admits`
    allows it), a chest rides in a cart (D-313), and one level of unpacking
    would delete the inner box while its own container went on holding goods
    with no owner -- the same orphan, one floor lower. The walk runs until a
    layer brings back no new box.

    **The harness lets go.** A vehicle may be pulled by a body (D-157), and
    the harness points at the vehicle's row. The body stays where it is: it
    pulled a thing, and the thing is gone.

    **Everything under its lock, reread after it.** The things themselves
    are taken in id order (`stock.lock_items`) -- held already by a caller
    that had to ask where they lie first (the yard, the pocket, the floor), and
    taken here for one that did not, a batch's target. One gone meanwhile is
    simply not there to end. What lies inside them is locked here too, and
    for the reason the fire gave first: a chest in a field is open to anybody
    (`station.may_build` gives the wild to everybody), and somebody may be
    taking a sack out in the last minute -- doing precisely what the window
    before an eruption is for. Without the lock the delete would queue behind
    their update and take the sack **out of their hands** the moment it
    landed there. It is the second line: the doors that take a sack out of a
    chest or a hold hold the chest or the vehicle first (`storage._allowed`,
    `transport._pulled_here`), and so meet the thing's own lock above before
    they ever reach this one.

    **And it stands for growth: nothing reaches it on its own today.** Both
    ends of the crossing were walked on 2026-09-17. Every caller of this door
    holds the box's row before it gets here -- the fire and the falling roof
    take everything in the place under `stock.lock_items` (`plates.fire._burn`,
    `estate.upkeep._bury`), the rift and a death take the pocket the same way
    (`plates.ways._kill_on`, `death.die`), and a batch's target comes in under
    its own (`craft.batch.work._target`). Every door into a box takes that same
    row: the two named above, the machine's store (`agro.hands._store`), a pour
    and a vent (`liquid.lock_vessels`). A cart's spill is queued behind the
    vehicle's row by the arrival that writes it (`transport.follow`), and a
    dead machine's bunker is emptied only once the machine's row is committed
    gone (`agro.run._gone`). So the first line catches all of them, and the
    proof is the mutation: take this `with_for_update` away and the whole race
    suite stays green.

    Two doors do reach a box without its row -- the breath drawn out of a
    cylinder in the hands (`oxygen.breath.settle`, which holds the body's row)
    and the water a hand pours on a bed (`farm._consume`) -- and one destroyer
    meets them without holding the body either: a canister taken apart in the
    same pocket. It costs nothing so far, because those two **spend** the air
    rather than carry it out: with the lock or without it the stack ends gone
    either way, which is why that crossing cannot pin this line either.

    So it is pinned by a test that takes the **first** line away instead:
    `test_races_fire.py::test_the_eruption_does_not_burn_what_was_taken_out_of_a_chest`
    runs twice, and its second case rereads the chest without locking it, which
    leaves the fire alone with this lock and turns its removal red. When a real
    caller does appear -- one that ends a chest or a hold without holding its
    row, or a door that carries a sack out of one without holding it -- the
    test to write is that pair for real, and this counterfactual case can go.
    """
    held_things = await stock.lock_items(session, things)
    opened: set[uuid.UUID] = set()
    emptied: list[Container] = []
    everything = list(held_things)
    layer = list(held_things)
    while layer:
        boxes = [
            box
            for box in (
                await session.execute(
                    select(Container).where(
                        Container.kind.in_(INSIDE_KINDS),
                        Container.owner_id.in_([thing.id for thing in layer]),
                    )
                )
            )
            .scalars()
            .all()
            if box.id not in opened
        ]
        if not boxes:
            break
        ids = {box.id for box in boxes}
        opened |= ids
        emptied.extend(boxes)
        held = (
            (
                await session.execute(
                    select(Item)
                    .where(Item.container_id.in_(ids))
                    .order_by(Item.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
        #: Only what is still inside: a sack carried out while this waited on
        #: its row is in somebody's hands now. A chest among what is left is
        #: opened on the next lap. Nothing is deleted while the walk is on: a
        #: delete flushed between two laps would take its own container's
        #: owner out from under the query looking for it.
        layer = [thing for thing in held if thing.container_id in ids]
        everything.extend(layer)

    if everything:
        await session.execute(
            delete(Harness).where(Harness.item_id.in_([thing.id for thing in everything]))
        )
    gone: dict[str, float] = {}
    for thing in everything:
        gone[thing.type_key] = gone.get(thing.type_key, 0.0) + amount_float(thing.amount)
        await session.delete(thing)
    #: In two flushes, and not for tidiness: with one, the delete of a box went
    #: to the database ahead of the delete of what lay in it, and the database
    #: refused it (`fk_item_container_id_container`). The order is not left to
    #: be inferred -- what is inside goes, then the box that held it.
    await session.flush()
    for box in emptied:
        await session.delete(box)
    await session.flush()
    return gone
