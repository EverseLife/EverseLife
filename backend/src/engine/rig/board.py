# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What stands in a node and in what condition: the rig's line of the
location scene (`status`).

Only reads (a `readonly` command asks it): the hopper as of the last tick,
the fuel lying in the yard. It never settles a rig -- that is the pass's
(`run`), and a scene that advanced the machine raced the tick for its row.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import world
from src.engine.rig._base import _coal_available, hopper_capacity
from src.models.inventory import Item
from src.models.rig import Rig as RigRow
from src.models.world import Node, Vein
from src.units import amount_float


async def status(session: AsyncSession, constants: Constants, node_id: uuid.UUID) -> list[dict]:
    """What stands in the node and in what condition -- for the location scene.

    A read: the hopper is shown as of the last tick (`counted_at`), the scene
    does not move the machine. Advancing here used to race the world tick
    and the emptying for the same row (review 2026-08-23).
    """
    rigs = (await session.execute(select(RigRow).where(RigRow.node_id == node_id))).scalars().all()
    if not rigs:
        return []
    #: One node for the whole list -- the rigs were selected by it. A read of
    #: the scene, so the yard is looked into and never made for the look.
    place = await session.get(Node, node_id)
    yard = None if place is None else await world.node_yard(session, place)
    coal_ = 0.0 if yard is None else await _coal_available(session, yard.id)
    out: list[dict] = []
    for rig in rigs:
        machine = await session.get(Item, rig.item_id)
        vein = await session.get(Vein, rig.vein_id)
        out.append(
            {
                "id": str(rig.id),
                #: The machine itself. Not the row's own state but the one fact
                #: the window cannot reach from here: whether the rig stands is
                #: read off this id among what stands (`bench`), and putting a
                #: lying one back up (D-314) needs the thing to name (D-225).
                "item": str(rig.item_id),
                #: And the vein it sits on. Two veins of one rock in a node
                #: make the resource ambiguous, so this is not derivable
                #: either (D-225) -- and standing a rig back up (D-314) puts it
                #: where it was, which is the only vein its loaded hopper may
                #: go back onto.
                "vein": str(rig.vein_id),
                "resource": vein.resource if vein else None,
                "hopper": float(rig.hopper),
                #: When the hopper was last counted: the world tick moves it.
                "counted_at": rig.counted_at.isoformat(),
                "capacity": hopper_capacity(constants),
                "full": float(rig.hopper) >= hopper_capacity(constants),
                "fuel": coal_,
                "hours_of_fuel": coal_ / constants[R.RIG_FUEL_PER_HOUR],
                "condition": float(machine.condition) if machine else 0.0,
                "vein_left": amount_float(vein.remaining) if vein else 0.0,
            }
        )
    return out
