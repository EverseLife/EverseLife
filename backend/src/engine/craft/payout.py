# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""craft: where a batch pays out (D-209, D-230, D-265).

Three doors hand matter out of a batch: the yield of a make and the return of
a recycling (`batch.finish`), and the giveback of a batch swept away with its
job dead (`queue._abandon`, D-217). All three land it the one way. A liquid is
poured, never handed over (D-230): into the vessels in the master's hands when
they stand at the machine, then into those at the machine, and what fits
nowhere spills -- said in the journal, because matter that vanished in silence
is a bug report waiting to happen. What arrived dry in the hands is gathered
for the carry rule (D-265), which the door applies once to the whole payout.

One liquid goes another way, and it is the make's alone: aboard a hull, the
yield of a machine on the lines pours into the vessels of its outlet and a
byproduct into its vent (D-340), both in `batch.finish` -- only their spills
are said here, in the same words.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog
from src.engine import events, liquid
from src.engine.world import node_container
from src.models.craft import CraftBatch
from src.models.event import EventKind
from src.models.identity import Body
from src.models.inventory import Container, Item
from src.models.world import Node


async def vessels_reach(
    session: AsyncSession, batch: CraftBatch, where: Container
) -> list[Container]:
    """Where a liquid the batch pays out may be poured: the hands first when
    the master is at the machine, then the place itself. Away from the bench
    the hands are out of reach, and only what stands at the machine takes it."""
    yard = await node_container(session, await session.get(Node, batch.node_id))
    if where.id == yard.id:
        return [yard]
    return [where, yard]


async def settle(
    session: AsyncSession,
    catalog: Catalog,
    batch: CraftBatch,
    body: Body,
    laid: Sequence[Item],
    within: Sequence[Container],
) -> list[Item]:
    """Pour what has just been laid down, if it is a liquid, and say what spilled.

    Returns the dry things among `laid` that landed in the master's hands --
    the ones the carry rule weighs. A liquid is in a vessel by now or spilled,
    and what landed beside the machine (`within` of the yard alone: the master
    is away) is nobody's load.
    """
    arrived: list[Item] = []
    for piece in laid:
        #: Named before the pour: what spills is deleted with its row.
        name = piece.type_key
        spilled = await liquid.settle(session, catalog, piece, within)
        if spilled > 0:
            await say_spilled(session, batch, body, name, spilled)
        elif len(within) > 1 and not liquid.is_liquid(catalog, name):
            arrived.append(piece)
    return arrived


async def say_spilled(
    session: AsyncSession, batch: CraftBatch, body: Body, name: str, spilled: float
) -> None:
    """Say in the journal that a batch's liquid found no vessel and spilled."""
    await events.record(
        session,
        EventKind.STORAGE_SPILLED,
        actor_identity_id=body.identity_id,
        node_id=batch.node_id,
        type_key=name,
        amount=spilled,
    )
