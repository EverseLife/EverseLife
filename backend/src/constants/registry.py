# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Registry of constants the engine depends on.

Every quantity is declared **once** and from then on taken only from the
registry. There must be no more keys than the code currently uses: the
registry is not a copy of `constants.json` but a list of what the engine
really relies on.

The declarations live in sections beside this module, cut by roadmap stage
when the registry passed the eight hundred lines the quality bar allows
(2026-09-13), so that it is visible what each stage wired up:

* `registry_map` -- the world: the day, the sphere, the ground, the relief,
  the biomes, the season and the weather;
* `registry_body` -- the body: stamina, travel and transport, carrying,
  wounds, food, speech, death, the device fee;
* `registry_work` -- work: mining, craft, quality, wear, the rig, the
  automats, energy, foraging;
* `registry_farm` -- the farm: the bed, its care, its pests, its seed;
* `registry_city` -- the city: market, coin, law, bank, land and buildings;
* `registry_space` -- space: ships, orbits, Pyroxis, Aurora, oxygen and frost.

This module is the door and declares nothing itself. Every section is
star-imported here, so the engine reads `R.KEY` without knowing which section
holds it and `declared()` finds every spec in one namespace. Nothing else
imports a section. A star import loses a spec without a sound in two ways --
a section the door does not import, and a name two sections both declare --
and `tests/test_constants.py` refuses both.
"""

from __future__ import annotations

from src.constants.registry_body import *  # noqa: F403 -- the door re-exports every section
from src.constants.registry_city import *  # noqa: F403
from src.constants.registry_farm import *  # noqa: F403
from src.constants.registry_map import *  # noqa: F403
from src.constants.registry_space import *  # noqa: F403
from src.constants.registry_work import *  # noqa: F403
from src.constants.spec import Spec


def declared() -> tuple[Spec, ...]:
    """Everything the sections declare -- the source for the startup check.

    Sorted by name, so the check reads the same set in the same order however
    the sections are imported.
    """
    return tuple(
        value
        for name, value in sorted(globals().items())
        if not name.startswith("_") and isinstance(value, Spec)
    )
