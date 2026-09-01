# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

""" "Roof" -- the E1 mining mechanic (D-143).

Three buttons, one hidden number, two or three forks per session. Dig, set a
support, leave; plus a pace lever. Roof stability is never shown to the player
-- a sign string goes out, and it lies by `mine.sign_noise`.

A vein holds solid rock or liquid (D-252): the liquid one is not worked by
hand at all -- oil is pumped by the rig (`engine.rig`), and a session on it
refuses at the door.

## Where each part of it lives

The file grew past what one file should hold, and it was three subjects all
along -- so it is three now, and this one is the door:

* `_base` -- the words and the formulas: the refusals, the `Sight`, swing
  arithmetic, the sign and its noise, the crowd, the depletion tiers, the
  session container. The derivation of every formula against D-143 is in its
  docstring;
* `face` -- the shift itself: `start`, `swing`, `timber`, `set_pace`,
  `leave`, `abandon`, `sight`, the vault's tool requirement and the prison
  workoff (D-174);
* `collapse` -- the bad ending: the lost haul, the wear, the wound and the
  death rolls (D-111, D-213).

The door publishes what the world outside the package actually asks for.
`deplete` is public on purpose: the rig eats the same vein by the same rule,
and a second copy of the tiers would drift from this one.
"""

from src.engine.mining._base import (  # noqa: F401
    TIMBER,
    MiningError,
    NoStrength,
    NotHere,
    NoTimber,
    NoTool,
    SessionClosed,
    Sight,
    VeinDepleted,
    VeinLiquid,
    active,
    crowd_factor,
    deplete,
    pace_factor,
    remember_roof,
    roof_of,
    session_container,
    sign_of,
    starting_roof,
    swing_cost,
    swing_hours,
)
from src.engine.mining.face import (  # noqa: F401
    abandon,
    leave,
    set_pace,
    sight,
    start,
    swing,
    timber,
)
from src.models.mining import MiningSession, Pace, SessionState  # noqa: F401
