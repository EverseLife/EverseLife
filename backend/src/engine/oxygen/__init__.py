# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
"""Oxygen: the second scale of survival, and only where there is no air
(D-233, D-234).

Warmth (`engine.frost`) is the first scale and this is the second, deliberately
built to the same shape so that two scales do not become two mechanics: a
property of the **planet** decides whether the question arises at all, and on
Terra and Aurora it never does. There is air there, the reading is empty, and
nothing in this module is ever asked.

Where there is none -- in flight and on Pyroxis -- two things breathe, and they
breathe from different places:

* **a hull** breathes off the life support's line (D-288). Oxygen is a liquid
  (D-230) and exists only inside a vessel, so the ship's reserve is what lies
  in the vessels the **life support** reaches: any vessel installed aboard by
  default -- a tank, a canister, a cylinder put up in a compartment the way
  furniture is -- or the ones the owner named when a line was drawn
  (`ship.lines`). A cylinder in the hands, on the floor or packed in a chest
  is luggage: nothing aboard breathes it, and the word for that is on the
  thing itself. The crew draws `oxygen.crew_draw` an hour a head, and the
  system **only drinks**: it makes nothing. Air is made by an electrolyser --
  at a port's grid, where it is the spaceport's oxygen pump, or aboard by
  hand or by an automat (D-253) -- and poured into the vessels the line
  stands on. There is no number of people a system holds: the draw is the
  ceiling, the way mass is the hold's (D-202), and the console shows the
  hours. Without a system the hull breathes for nobody and does not cast off;
* **a body outside** breathes a cylinder, and only through a suit. A cylinder
  in the bag gives nothing by itself: the suit is what connects the body to it
  (D-234), and a bare body on an airless node dies however many cylinders it
  carries. Outside the draw is `oxygen.body_draw` -- five times the hull's,
  because the work is harder and the suit leaks.

## Why there is no reserve on the body

Because a reserve on the body would be a second place to keep the same thing.
The cylinder is the reserve, it is a stack of a liquid like any other, it can
be filled, carried, dropped and traded, and the engine keeps no copy of how
full it is. What the body keeps is a **stamp** -- the moment its breathing was
last settled -- the way `body.warmth_at` is a stamp for the cold.

## Dying is arithmetic, never a surprise

The engine refuses the step onto an airless node without a suit and without a
cylinder with something in it (D-233): death by ignorance in one click is not
this world's way. After that the countdown is on the screen the whole time --
the cylinder's units and the ship's tanks are both ordinary readings -- and
what kills is the mistake somebody watched, not the door.

One settling of grace is deliberate: a stretch the oxygen only half covered
drains the reserve to nothing and kills nobody. It is the next stretch, begun
with nothing at all, that ends the body. Otherwise a tick landing a second
after the last unit was spent would be indistinguishable from suffocation.
"""

from src.engine.oxygen._base import (  # noqa: F401
    AIR,
    AIRLESS,
    ASPHYXIA,
    SUIT,
    Breath,
    NoAir,
    OxygenError,
    airless_planets,
    free_air,
    sealed,
)
from src.engine.oxygen.breath import (  # noqa: F401
    require_air,
    settle,
    tick_bodies,
    tick_ships,
)
from src.engine.oxygen.gauge import (  # noqa: F401
    gauge,
    view,
)
from src.engine.oxygen.supply import (  # noqa: F401
    breathable_stacks,
    carried,
    cylinders,
    hull_draw,
    reserve,
    suited,
    systems_of,
)
