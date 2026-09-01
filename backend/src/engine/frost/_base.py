# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The climate's vocabulary and floor: the marks a planet carries, the
classes that make warmth, and every refusal the cold knows. Asks nobody
above itself.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.base import remember
from src.engine.errors import Refusal
from src.models.world import Node, Planet

#: The planet's own property, written into its node on the space layer by the
#: seed (D-231). A planet without either is livable ground and asks nothing.
FROST = "frost"

HEAT = "heat"

#: Thing classes with heat behaviour (D-215). The engine keeps no list of
#: stoves: a second heater is a line in the vault.
#: The plant heats **its node and every neighbour**, the heater only its own,
#: and both eat the city pool. The brazier is carried, burns fuel and needs no
#: grid -- the warmth of a camp and the first spark of a dead city.
PLANT = "heat_plant"

HEATER = "heater"

BRAZIER = "brazier"

#: A one-off handful of hours, the thing one walks into the cold with.
WARMER = "warmer"


class FrostError(Refusal):
    pass


class Frozen(FrostError):
    """The node is cold: what does not burn its own fuel does not work here."""


class NotWarmer(FrostError):
    """Not a warmer. What warms is decided by the vault, not by the engine."""


# --- the planet and the node --------------------------------------------------


async def climate_of(session: AsyncSession, node: Node) -> str | None:
    """«мерзлота», «пекло» -- or nothing, where the ground is livable.

    The property belongs to the **planet**, and a planet is an ordinary node of
    the space layer whose key is the planet's own name (`seed_parts.system`). Asked
    of the planet rather than of a constant on purpose: a climate is a fact of
    the world, and the world is in the database.
    """
    weather = await _planet_marks(session)
    return weather.get(node.planet)


async def _planet_marks(session: AsyncSession) -> dict[Planet, str | None]:
    """Every planet's climate, in one reading -- there are four of them."""

    async def read() -> dict[Planet, str | None]:
        spheres = (
            (
                await session.execute(
                    select(Node).where(Node.key.in_([planet.value for planet in Planet]))
                )
            )
            .scalars()
            .all()
        )
        found: dict[Planet, str | None] = {}
        for sphere in spheres:
            marks = sphere.properties or {}
            for weather in (FROST, HEAT):
                if marks.get(weather):
                    found[sphere.planet] = weather
        return found

    return await remember(session, ("planet_climate",), read)
