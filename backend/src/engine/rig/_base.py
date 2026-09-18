# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The words of the drilling rig (D-115): the thing class, what it burns, its
refusals, and the two small reads every room asks -- how much its hopper
holds and how much fuel lies in a yard.

Asks nobody in the package: the pass (`run`), the hands at the machine
(`hands`) and the scene (`board`) all stand on this floor.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current_catalog
from src.constants import registry as R
from src.engine.errors import Refusal
from src.models.inventory import Item
from src.units import amount_float

#: The rig thing class (D-215). A ladder milestone: reachable by the end of E2.75.
RIG = "rig"


def _fuel_names() -> tuple[str, ...]:
    """What the rig burns: every material with a fuel value (D-215).

    People haul the fuel -- that is the whole enterprise. The rig is a motor,
    not a generator: it eats `rig.fuel_per_hour` units whatever the material.
    """
    return tuple(current_catalog().recipes.fuels()) or ("coal",)


class RigError(Refusal):
    pass


class NoRig(RigError):
    pass


class NoRoom(RigError):
    """Nowhere to pour (D-252): a liquid hopper empties only into vessels with room."""


class NotYours(RigError):
    """Somebody else's rig: the hopper is emptied by the owner or their carter by contract."""


class HopperNotEmpty(RigError):
    """The hopper still holds ore of the vein it stood on: a rig moves empty (D-314)."""


def hopper_capacity(constants: Constants) -> float:
    """Hopper capacity in ore units: the vault sets it in **hours of work**."""
    return constants[R.RIG_HOPPER_CAPACITY] * constants[R.RIG_OUTPUT_PER_HOUR]


async def _coal_available(session: AsyncSession, container_id: uuid.UUID) -> float:
    stacks = (
        (
            await session.execute(
                select(Item).where(
                    Item.container_id == container_id,
                    Item.type_key.in_(_fuel_names()),
                )
            )
        )
        .scalars()
        .all()
    )
    return sum(amount_float(stack.amount) for stack in stacks)
