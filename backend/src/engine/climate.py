# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The place's climate as farming reads it (D-261).

Exploration has written every found site a mean `temperature` and
`precipitation` since D-126; the planet adds the diurnal swing
(`planet.temp_swing`), and the planetary day (`time.day_*`) sets the phase:
noon is the peak, midnight the floor. Light is a 0-3 scale that breathes the
same day: 3 in the open, a step less under the woods, a step less again where
the ground is built over (`farm.shade_built_share`), nought at night.

The phase counts from the world's epoch (`world.epoch`, D-029) -- the same
origin the client's planetary clock ticks from, so the server's "now" and the
drawn hand never disagree.

The sowing gate compares the culture's requirements against the node's
**daily band**, not the moment of sowing: a crop lives through every hour of
its cycle, so what must fit is the whole swing. A node without a temperature
-- old ones, homes, a ship's hydroponics bay -- carries no gate: absence of a
record is not a climate.

Nothing here writes: the current temperature is a pure function of the clock,
so a read stays a read (the quality bar's "look does not write").
"""

from __future__ import annotations

import math
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import estate, world
from src.models.world import Node, Planet
from src.units import PERCENT, SECONDS_PER_HOUR

#: The light scale's ceiling: an open clearing at noon. Matches the catalog's
#: `requires.light` 1-3, with nought left for the night.
FULL_LIGHT = 3

#: The place mark exploration writes for a forest (`explore._base.WOODS`,
#: D-191). Named here like farm's WATER: importing explore for one word would
#: put a whole subsystem on this module's import path.
WOODS = "woods"

#: The vault's day length per planet (OQ-028). One map here rather than four
#: lookups in callers: the registry key is picked by the node's planet.
_DAY_OF = {
    Planet.TERRA: R.TIME_DAY_TERRA,
    Planet.AQUATICA: R.TIME_DAY_AQUATICA,
    Planet.PYROXIS: R.TIME_DAY_PYROXIS,
    Planet.AURORA: R.TIME_DAY_AURORA,
}


def day_hours_of(constants: Constants, planet: Planet) -> float:
    return constants[_DAY_OF[planet]]


def swing_of(constants: Constants, planet: Planet) -> float:
    """The diurnal temperature swing around the node's mean (D-261)."""
    return float(constants[R.PLANET_TEMP_SWING].get(planet.value, 0.0))


def mean_temperature(node: Node) -> float | None:
    """The node's mean temperature, if exploration ever wrote one."""
    raw = (node.properties or {}).get("temperature")
    try:
        return None if raw is None else float(raw)
    except (TypeError, ValueError):  # pragma: no cover -- properties are engine-written
        return None


def precipitation(node: Node) -> float:
    """The node's rainfall on the 0-100 scale. Absent reads as dry."""
    raw = (node.properties or {}).get("precipitation")
    try:
        return max(0.0, float(raw or 0))
    except (TypeError, ValueError):  # pragma: no cover
        return 0.0


def day_phase(
    constants: Constants, planet: Planet, origin: datetime | None, moment: datetime
) -> float:
    """Where in the planetary day the moment falls: 0 is midnight, 0.5 noon.

    Counted from the world's epoch so the server and the client's clock agree
    on the hour; a world with no epoch yet has no first node and nothing to
    farm, and reads as its own midnight.
    """
    if origin is None:
        return 0.0
    day_seconds = day_hours_of(constants, planet) * SECONDS_PER_HOUR
    return ((moment - origin).total_seconds() % day_seconds) / day_seconds


def is_day(constants: Constants, planet: Planet, origin: datetime | None, moment: datetime) -> bool:
    """The lit half of the planetary day: the middle two quarters."""
    return 0.25 <= day_phase(constants, planet, origin, moment) < 0.75


def day_index(
    constants: Constants, planet: Planet, origin: datetime | None, moment: datetime
) -> int:
    """Which calendar day of the planet the moment falls in, counted from the
    world's epoch (D-263).

    The farm round goes by this number, not by an interval: one day -- one
    round, at any hour of it, so the care window never drifts away from a
    player's own rhythm. A world with no epoch has nothing to farm and lives
    in its day nought.
    """
    if origin is None:
        return 0
    day_seconds = day_hours_of(constants, planet) * SECONDS_PER_HOUR
    return int((moment - origin).total_seconds() // day_seconds)


def temperature_now(
    constants: Constants, node: Node, origin: datetime | None, moment: datetime
) -> float | None:
    """The node's temperature at the moment: mean minus swing at midnight, plus at noon."""
    mean = mean_temperature(node)
    if mean is None:
        return None
    swing = swing_of(constants, node.planet)
    phase = day_phase(constants, node.planet, origin, moment)
    return mean - swing * math.cos(2 * math.pi * phase)


async def daylight(session: AsyncSession, constants: Constants, node: Node) -> int:
    """The node's light at the height of day, 0-3 (D-261).

    The woods take a step, and so does ground built over past
    `farm.shade_built_share`: clearings and meadows keep the whole sky.
    """
    light = FULL_LIGHT
    if world.has_place(node, WOODS):
        light -= 1
    area = float(node.area_m2 or 0)
    if area > 0:
        built = await estate.built_area(session, node, ground=True)
        if built / area * PERCENT >= constants[R.FARM_SHADE_BUILT_SHARE]:
            light -= 1
    return max(0, light)


async def light_now(
    session: AsyncSession,
    constants: Constants,
    node: Node,
    origin: datetime | None,
    moment: datetime,
) -> int:
    """The light this very moment: the day's level, or nought at night."""
    if not is_day(constants, node.planet, origin, moment):
        return 0
    return await daylight(session, constants, node)
