# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The operator's board: a programme written and stopped, and the view that
tells what the machine is doing and why not.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import events, travel, world
from src.engine.automat._base import RecipeUnknown, _knows, _machine_here, _programmable, of_item
from src.engine.automat.run import advance
from src.engine.automat.wire import _drop_wires
from src.models.automat import Automat as AutomatRow
from src.models.automat import AutomatLink
from src.models.event import EventKind
from src.models.identity import Body
from src.models.inventory import Item
from src.models.world import Node


async def program(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    recipe_key: str,
    *,
    now: datetime | None = None,
) -> AutomatRow:
    """Load a recipe into the machine. In person, on own ground, out of own knowledge."""
    moment = now or datetime.now(UTC)
    node = await _machine_here(session, body, item)
    proc = _programmable(constants, catalog, item, recipe_key)

    #: Out of the owner's own knowledge (D-253): choosing is loading, nothing
    #: is carried or inserted -- but the machine is not a free library, and
    #: an operation (smelting) is everyone's, as at the furnace itself.
    if proc.needs_recipe and not await _knows(session, body, proc.output):
        raise RecipeUnknown(key="auto-recipe-unknown", goods=proc.output)

    row = await of_item(session, item)
    if row is None:
        row = AutomatRow(
            item_id=item.id,
            node_id=node.id,
            owner_identity_id=body.identity_id,
            backlog=Decimal(0),
            counted_at=moment,
        )
        session.add(row)
    else:
        #: The old programme is worked to this moment first: hours lived under
        #: it must not produce under the new one.
        await advance(session, constants, row, catalog=catalog, now=moment)
        #: A change of programme drops the started piece: its inputs were
        #: never consumed (the backlog is time), so nothing is lost but time.
        row.backlog = Decimal(0)
        row.owner_identity_id = body.identity_id
        if row.node_id != node.id:
            #: The machine moved houses: its wires pointed at the old floor,
            #: and a wire between nodes is not a thing (D-047).
            await _drop_wires(session, item.id)
        row.node_id = node.id
    row.recipe_key = proc.output
    await session.flush()

    await events.record(
        session,
        EventKind.AUTOMAT_PROGRAMMED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        automat=str(row.id),
        machine=item.type_key,
        recipe=proc.output,
    )
    return row


async def stop(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    item: Item,
    *,
    now: datetime | None = None,
) -> AutomatRow | None:
    """Take the programme off. The machine stays; the row goes with it.

    A row is the working state of a programmed machine and nothing else
    (D-253): without one the machine is a thing again -- it does not wear by
    the clock and does not cost the tick a lock. The wires stay: they are
    keyed by the machine itself, and the picture outlives the programme.
    """
    moment = now or datetime.now(UTC)
    node = await _machine_here(session, body, item)
    row = await of_item(session, item)
    if row is None:
        return None
    await advance(session, constants, row, now=moment)
    await session.delete(row)
    await session.flush()

    await events.record(
        session,
        EventKind.AUTOMAT_STOPPED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        machine=item.type_key,
    )
    return None


async def view(session: AsyncSession, catalog: Catalog, body: Body) -> dict:
    """The automats standing where the body stands: machine, programme, backlog.

    A read: nothing is advanced and nothing is written (the tick does that).
    The numbers are as of the last tick, and that is what the console says.
    """
    await travel.require_here(session, body)
    rows = (
        (
            await session.execute(
                select(AutomatRow).where(AutomatRow.node_id == body.node_id).order_by(AutomatRow.id)
            )
        )
        .scalars()
        .all()
    )
    #: The wires of the floor: keyed by the machines standing here, so a
    #: wire between two unprogrammed machines is part of the picture too.
    node = await session.get(Node, body.node_id)
    #: The floor as it stands (`world.node_yard`), not made on the way: this is
    #: a read, and `node_container` gives a yard to whatever node has none --
    #: an INSERT behind a glance at the console (review 2026-08-23, wave 1
    #: item 4). No yard, nothing standing, and the two answer alike.
    yard = await world.node_yard(session, node)
    standing = (
        set()
        if yard is None
        else set(
            (await session.execute(select(Item.id).where(Item.container_id == yard.id)))
            .scalars()
            .all()
        )
    )
    wires = (
        (await session.execute(select(AutomatLink).where(AutomatLink.from_item_id.in_(standing))))
        .scalars()
        .all()
    )
    #: The machine's kind and place the client already has from `look` --
    #: only what it cannot derive travels (D-225): the address, the
    #: programme, the work in flight, and the wires (addressed by the same
    #: item ids the commands take).
    return {
        "machines": [
            {
                "item": str(row.item_id),
                "recipe": row.recipe_key,
                "backlog": float(row.backlog),
                "counted_at": row.counted_at.isoformat(),
            }
            for row in rows
        ],
        "links": [
            {"from": str(wire.from_item_id), "to": str(wire.to_item_id)}
            for wire in wires
            if wire.to_item_id in standing
        ],
    }
