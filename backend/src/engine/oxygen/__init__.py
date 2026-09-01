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

* **a hull** breathes what stands in it. Oxygen is a liquid (D-230) and exists
  only inside a vessel, so the ship's reserve is what lies in any vessel
  **standing in a compartment** -- a tank, a canister, a bottle. Wider than the
  fuel a passage burns, and on purpose: the engines are plumbed to the tanks and
  reach nothing else, while the life support is a machine standing in a room,
  and what a crew carries to it, it uses. Narrower than the hold, and for the
  same reason: a canister packed into a chest is stowed cargo, and nothing
  rummages through luggage. The crew draws `oxygen.crew_draw` an hour a head,
  and
  the **life support makes air** to cover it: water out of the same tanks plus
  charge out of the batteries of its own node, by the vault's own recipe for
  «Кислород». One system covers as many people as it holds
  (`ship.life_support_crew`) -- more crew than that wants a second system, the
  same number that has always decided how many the ship may carry (D-202).
  What it makes is **breathed, never stored**: the vessels aboard are what the
  crew lives on when the water or the charge runs out, and filling them -- or a
  cylinder for going outside -- is deliberate work at an «Электролизёр», which
  is the very recipe this runs by;
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
    ENERGY,
    SUIT,
    WATER,
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
    hull_output,
    reserve,
    suited,
    water_aboard,
)
