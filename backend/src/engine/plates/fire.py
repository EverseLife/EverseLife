# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The fire: what lies under the open sky dies with the ground it lies on.

The one door matter leaves the world by during an eruption. The rift under a
walker uses the same door (`ways._kill_on`): matter leaves whole, or it
leaves half-way and haunts the schema as orphans.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.engine import world
from src.models.inventory import Container, ContainerKind, Item
from src.models.world import Node
from src.units import amount_float


async def _burn(session: AsyncSession, shaken: list[Node]) -> float:
    """What lies under the open sky burns with the ground (D-197).

    There is no warehouse in the fields, and that is the point: hauling is
    always part of the work here, and the logistics of Pyroxis are dear by the
    world's build rather than by anybody's tariff.

    Taken under a lock, and re-read after it: somebody carrying a sack out of
    the node in the last minute of the window is doing exactly what the window
    is for, and their sack must not be burned out of their hands.
    """
    yards = [(await world.node_container(session, node)).id for node in shaken]
    lying = (
        (
            await session.execute(
                select(Item)
                .where(Item.container_id.in_(yards))
                .order_by(Item.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    #: Only what is still here: somebody carrying a sack out in the last minute
    #: of the window is doing exactly what the window is for.
    here = [thing for thing in lying if thing.container_id in yards]
    return await _consume(session, here)


async def _consume(session: AsyncSession, things: list[Item]) -> float:
    """Take these things out of the world, and whatever is inside them with them.

    A chest is a thing with a container of its own, and deleting the chest
    alone would leave its goods alive in a place that no longer exists -- the
    same orphan `estate.upkeep` clears when a house falls. Used by the fire in
    the fields and by the rift under a walker alike: matter leaves the world by
    one door, or it leaves it half-way.

    **All the way down.** A chest goes inside a chest (`storage.admits` allows
    it), and one level of unpacking would delete the inner chest while its own
    container went on holding goods with no owner -- the same orphan, one floor
    lower. The loop runs until a layer brings back no new box.
    """
    gone = 0.0
    opened: set[uuid.UUID] = set()
    emptied: list[Container] = []
    inner: list[Item] = []
    layer = list(things)
    while layer:
        boxes = (
            (
                await session.execute(
                    select(Container).where(
                        Container.kind == ContainerKind.STORAGE,
                        Container.owner_id.in_([thing.id for thing in layer]),
                    )
                )
            )
            .scalars()
            .all()
        )
        layer = []
        for box in boxes:
            if box.id in opened:  # pragma: no cover -- a box cannot own itself
                continue
            opened.add(box.id)
            #: Under the lock and reread after it, exactly like the things lying
            #: on the ground above. A chest in a field is open to anybody
            #: (`station.may_build` gives the wild to everybody), so somebody may
            #: be taking a sack out of it in the last minute of the window --
            #: doing precisely what the window is for. Without the lock the
            #: delete would queue behind their update and take the sack **out of
            #: their hands** the moment it landed there.
            held = (
                (
                    await session.execute(
                        select(Item)
                        .where(Item.container_id == box.id)
                        .order_by(Item.id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                )
                .scalars()
                .all()
            )
            for thing in held:
                if thing.container_id != box.id:  # pragma: no cover -- carried out in time
                    continue
                #: A chest among them is opened on the next lap. Nothing is
                #: deleted while the walk is on: a delete flushed between two
                #: laps would take its own container's owner out from under the
                #: query looking for it.
                layer.append(thing)
                inner.append(thing)
            emptied.append(box)
    for thing in [*things, *inner]:
        gone += amount_float(thing.amount)
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
