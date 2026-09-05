# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Exploration: the generator of a planet's graph (D-321).

Nothing of a planet's surface is laid at the world's birth but its cities.
Everything else a scout finds: the scout aims at a point of the globe, the
landscape says whether the aim is lawful (`aim`), the run costs the walk of the
distance over wild ground and, when it is over, the cell is read off the hidden
field and becomes a node (`run`). The same cell is the same node for everybody
(`_base`): the lattice, not a row, is what the world agrees on.

The package is a stack: `_base` is the floor (the lattice and the refusals),
`aim` reads the world to judge a target, `run` writes it.
"""

from src.engine.explore._base import (  # noqa: F401
    Aim,
    AlreadyOut,
    Cell,
    CrossesWay,
    ExploreError,
    IntoWater,
    NoRoom,
    NotFromHere,
    NotLand,
    TooFar,
    TooNear,
    cell_of,
    key_of,
    lattice_deg,
    point_of,
)
from src.engine.explore.aim import area_of, check, crosses_water, radius_of  # noqa: F401
from src.engine.explore.run import (  # noqa: F401
    FORD,
    ROLE,
    complex_roll,
    knit,
    materialise,
    returned,
    survey,
)
