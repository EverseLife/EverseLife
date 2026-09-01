# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The airless vocabulary and floor: which planets have no sky, where air is
free and where a hull is sealed, and every refusal breathing can make.
Asks nobody above itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import remember
from src.engine import ship as vessels
from src.engine.errors import Refusal
from src.models.ship import Ship
from src.models.world import Node, Planet
from src.units import (
    AMOUNT_SCALE,
)

#: The planet's own property, written into its node on the space layer by the
#: seed (D-234) -- beside «мерзлота» and «пекло», and read the same way.
AIRLESS = "airless"

#: What the journal names as the cause of this death (`BODY_DIED.cause`) --
#: a payload key, never a sentence (D-251).
ASPHYXIA = "asphyxia"

#: What is breathed. A single name rather than a class, because it is a single
#: substance: D-215 binds behaviour to classes so that a second stove or a
#: second engine is data, and there is no second air.
AIR = "oxygen"

#: What the life support turns into air, together with charge. Both come from
#: the vault's recipe for «Кислород», never from a number here.
WATER = "water"

ENERGY = "energy"

#: The class that connects a body to a cylinder. Without one worn, a cylinder
#: is luggage (D-234).
SUIT = "spacesuit"

#: Amounts split into thousandths, so "was there enough" must tolerate the last
#: digit -- otherwise exactly enough oxygen turns out to be short.
_EPS = 1 / AMOUNT_SCALE


class OxygenError(Refusal):
    pass


class NoAir(OxygenError):
    """Nothing to breathe where the step leads, and nothing to breathe it from."""


@dataclass(frozen=True, slots=True)
class Breath:
    """What one settling of a body's breathing did."""

    #: Units of oxygen the body can still reach after the settling.
    left: float
    #: Hours of the elapsed stretch nothing covered. Above zero means the body
    #: was breathing vacuum, and that is what kills.
    uncovered: float


# --- the planet and the node --------------------------------------------------


async def airless_planets(session: AsyncSession) -> frozenset[Planet]:
    """Which planets have no air of their own. Four rows, one reading."""

    async def read() -> frozenset[Planet]:
        spheres = (
            (
                await session.execute(
                    select(Node).where(Node.key.in_([planet.value for planet in Planet]))
                )
            )
            .scalars()
            .all()
        )
        return frozenset(
            sphere.planet for sphere in spheres if (sphere.properties or {}).get(AIRLESS)
        )

    return await remember(session, ("airless_planets",), read)


async def free_air(session: AsyncSession, node: Node) -> bool:
    """Whether one simply breathes here, with nothing to spend and nothing to wear.

    Ground: the planet's own air, and Terra and Aurora both have it (D-232 --
    a leaky dome is not a vacuum). Aboard: the air is free while the hull sits
    at a port of a planet that has some, because then the hatch may as well be
    open. Undocked -- in flight -- there is nothing outside to open onto, and
    the hull is on its own however Terran the port it left was.

    An orbital node carries the planet it belongs to (D-245) and has none of
    its air: it is the void with a name on it, and stepping out onto one is a
    spacewalk whatever hangs below.
    """
    if vessels.is_orbit(node):
        return False
    airless = await airless_planets(session)
    if not vessels.is_aboard(node):
        return node.planet not in airless
    ship = await vessels.of_node(session, node)
    if ship is None:  # pragma: no cover -- an aboard node always has its ship
        return False
    return not await sealed(session, ship)


async def sealed(session: AsyncSession, ship: Ship) -> bool:
    """Whether this hull has to make its own air.

    Under way, moored in orbit, or down on an airless world: the hatch opens
    onto something breathable in exactly one case, and this is the other three.
    """
    if ship.docked_node_id is None:
        return True
    port = await session.get(Node, ship.docked_node_id)
    if port is None or vessels.is_orbit(port):
        return True
    return port.planet in await airless_planets(session)
