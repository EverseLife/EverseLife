# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What the client is told: the hull's gauge and the body's own view of its
air. Reads only.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import ship as vessels
from src.engine.oxygen._base import free_air, sealed
from src.engine.oxygen.supply import _liquids, carried, hull_draw, hull_output, suited
from src.models.identity import Body
from src.models.inventory import Item
from src.models.ship import Ship
from src.models.world import Node
from src.units import (
    ROUND_MASS,
    ROUND_RATIO,
)

# --- what the client is told ---------------------------------------------------


async def gauge(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    ship: Ship,
    *,
    crew: int,
    things: list[Item] | None = None,
) -> dict[str, object]:
    """The hull's atmosphere in one reading: the level, the water and the rate.

    Given as a level and a rate rather than as hours, so the client counts the
    hand itself and the figure never goes stale between pushes (D-226) -- the
    same shape the cold's reading has. A rate of zero on a sealed hull is a ship
    making exactly what it breathes; a negative one is the countdown.

    `things` is the hold when the caller has read it already: the console asks
    this of every hull it lists.
    """
    air, water = await _liquids(session, ship, things=things)
    shut = await sealed(session, ship)
    drawn = hull_draw(constants, crew) if shut else 0.0
    made = min(
        drawn,
        await hull_output(session, constants, catalog, ship, things=things, water=water),
    )
    return {
        #: What the hull holds and what the life support runs on: both are
        #: liquids in the same tanks (D-230), and both are mass on every passage.
        "units": round(air, ROUND_MASS),
        "water": round(water, ROUND_MASS),
        #: Whether the hull is breathing its own air at all: in port under a sky
        #: that has some, the hatch may as well be open and nothing is spent.
        "sealed": shut,
        "per_hour": round(made - drawn, ROUND_RATIO),
        #: The moment the tanks were last settled at, never the moment of the
        #: reading: the client counts down from what it is given, and "now"
        #: would hand it back the hour the tick has just charged.
        "at": ship.air_at.isoformat(),
    }


async def view(
    session: AsyncSession, constants: Constants, catalog: Catalog, body: Body, node: Node
) -> dict[str, object] | None:
    """What the player is told about the air. Empty where there is air.

    Shaped like the cold's reading (`frost.view`): a stamp, a rate and what is
    in reserve, so the client counts the hand itself and the number on screen
    never goes stale between pushes (D-226).
    """
    if await free_air(session, node):
        return None
    aboard = await vessels.of_node(session, node)
    if aboard is not None:
        crew = len(await vessels.crew_of(session, aboard))
        hull = await gauge(session, constants, catalog, aboard, crew=crew)
        return {
            "where": "aboard",
            "units": hull["units"],
            "per_hour": hull["per_hour"],
            "at": hull["at"],
            "suit": False,
        }
    wearing = await suited(session, catalog, body)
    return {
        "where": "suit",
        "units": round(await carried(session, body), ROUND_MASS),
        "per_hour": -constants[R.OXYGEN_BODY_DRAW] if wearing else 0.0,
        "at": body.air_at.isoformat(),
        #: Whether the cylinders are connected at all. Without a suit the
        #: reading is a bagful of useless bottles, and it must say so.
        "suit": wearing,
    }
