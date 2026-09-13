# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The sky, simulated (D-289).

Five bodies pull a hull all the way: the star and the four planets on the
circles the seed laid. A hull in space is a state -- a place, a speed, the
moment they were true -- flown by an integrator; a hull moored to a planet's
orbital node runs on an analytic parking circle and is not integrated at
all. The autopilot plans a passage with a Lambert arc and flies it by
re-solving the arc every step from where the hull actually is; the tanks pay
as the engines burn, and when they run dry the hull coasts -- for as long as
it takes somebody to bring it fuel, or until the coast ends on a body or out
of the system, which the forecast names to the hour.

The package is arithmetic only: states in, states out, nothing read from a
row. `engine.ship.sim` is the floor above it that owns the rows, the fuel
and the journal.

    _base     -- the system of bodies and the parking circle
    field     -- the pull and the Runge-Kutta integrator, batched
    plan      -- the slider's preview: two-body arcs, priced at both ends
    guide     -- the helm's burn for one step, and the capture
    forecast  -- where inertia leads, and when
    lambert   -- Lambert's problem over a batch of rows
    choice    -- which of the priced passages one hull is offered (D-341)
    flyby     -- a passage bent round a third world, searched by conics (D-341)
    shoot     -- the flyby refined and corrected in the whole sky
    assist    -- the helm through a flyby
"""

from src.sky._base import (  # noqa: F401
    DV_EPS,
    STAR,
    TIME_EPS,
    Body,
    Drifter,
    Rows,
    Star,
    System,
    Target,
    bearing,
    capture_of,
    circle_of,
    circle_rate,
    circle_speed,
    park_of,
    parking,
    place,
    place_any,
    shape_of,
    star_circle,
    system_of,
)
from src.sky.assist import Leg, Route, correct, steer_pass  # noqa: F401
from src.sky.choice import choices  # noqa: F401
from src.sky.field import advance, pull, sample  # noqa: F401
from src.sky.forecast import (  # noqa: F401
    CRASH,
    ESCAPE,
    STABLE,
    Fate,
    coast_to,
    ground_of,
    inertia,
)
from src.sky.guide import (  # noqa: F401
    BURN,
    CAPTURE,
    COAST,
    Helm,
    brake_days,
    eject_wait,
    holding,
    steer,
)
from src.sky.plan import (  # noqa: F401
    Pass,
    Sample,
    approach_quote,
    circle_quote,
    escape_dv,
    preview,
    routes,
    search_days,
)
