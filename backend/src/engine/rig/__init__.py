# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Drilling rig: continuous mining without the player (D-115).

The endgame of mining and, after the automatic machine, the second transition
from labour to capital. Built so as **not to kill the live miner**: the
machine loses to a human on every measure but one -- it does not sleep.

| | Human | Rig |
|---|---|---|
| Output | `mining.iron_per_hour` | `rig.output_per_hour`, noticeably less |
| Quality | by the vein, up to its richness | not above `rig.quality_cap` |
| Eats the vein | by what is mined | twice (`rig.depletion_multiplier`) |
| Requires presence | constantly | only to empty the hopper |

Craft mining remains the way to get **good ore**, the rig the way to get
**a lot of average**.

## Four obligations, and all of them require people

**Fuel.** `rig.fuel_per_hour` of coal from the node where the rig stands.
Ran out -- it stopped: hence a standing contract with a coal hauler rather
than "free ore".

**Emptying.** The hopper holds `rig.hopper_capacity` **hours of work**. Full
-- the rig stands until the owner (or their carter) comes and takes it. On
foot: matter moves only physically (D-047).

**Maintenance.** `rig.wear_per_day` of wear per day. An abandoned one falls
apart, and it is repaired by the same repair as any thing.

**Standing.** It works only put up on its vein (D-278, D-314). Taken down,
dropped by a demolition or fallen with its owner, it drills nothing: the row
waits for the machine and dies with it.

## Lock order

The rig row, then its vein, then the machine and the fuel of its yard in one
statement by id (`_held`), and the node last and unasked: the second write of
the row re-checks its keys and holds the node `FOR KEY SHARE`, which is why
the plot's holders take it `FOR NO KEY UPDATE` (`estate.hold_ground`). It is
the order of the fire and of a falling house: an eruption takes a field's
veins and then what lies in it (`plates.clock`), a fall the plot and then
what it buries (`estate.upkeep._bury`). `tick_rigs` holds every rig of the
world in one transaction, so it takes all the veins and then all the machines
and fuel before the first pass (`_hold_the_world`): one rig at a time, the
order held within a rig and not across two.

The doors keep it. `empty_hopper` takes the row, the vessels a liquid pours
into (`_hold_vessels`) and settles through `advance`; `station.take` takes
the node, the row (`hopper_left`) and then the machine; `place` the row, the
vein (`FOR KEY SHARE`) and then the machine. A first placement has no row to
lock; a rig stood up and taken down again between that empty select and the
machine's lock trips the unique `rig.item_id` rather than making a second row.

## Where each part of it lives

The file grew past what one file should hold, and it was three subjects all
along -- so it is three rooms on a floor now, and this one is the door:

* `_base` -- the words: the thing class, what it burns, the refusals, the
  hopper's capacity and the fuel lying in a yard;
* `run` -- the clock: one rig's pass (`advance`), the world's tick
  (`tick_rigs`) and the locks both take (`_held`, `_hold_the_world`);
* `hands` -- the machine at hand: standing it up (`place`), emptying the
  hopper (`empty_hopper`, `_hold_vessels`) and what the taking-down door asks
  of it (`hopper_left`);
* `board` -- the read of the scene (`status`), which never settles a rig.

Each room asks only those below it (pinned by import-linter):
`_base` <- `run` <- `hands`, and `_base` <- `board`. The door publishes what
the world outside the package asks for, and the private names the race tests
hold or name (`_coal_available`, `_held`, `_hold_the_world`, `_hold_vessels`).
A pause set on the door with `_slow` reaches every room that took the name; a
test that patches one side of a race by hand patches where its caller reads
the name -- the door for a call from outside (`place`, which the command
calls through the door), the room for a call from inside (`advance` and
`_coal_available`, which the pass reads off `run`).

## What is not here yet

* **City licence and mining tax** (D-115): the rig occupies a node and is
  subject to the city -- from E3, together with the city itself;
* **Deep mines** with their energy draw (`energy.deep_mine_draw`): that is a
  separate mechanic, not a property of the rig.
"""

from src.engine.rig._base import (  # noqa: F401
    RIG,
    HopperNotEmpty,
    NoRig,
    NoRoom,
    NotYours,
    RigError,
    _coal_available,
    hopper_capacity,
)
from src.engine.rig.board import status  # noqa: F401
from src.engine.rig.hands import (  # noqa: F401
    _hold_vessels,
    empty_hopper,
    hopper_left,
    place,
)
from src.engine.rig.run import (  # noqa: F401
    _held,
    _hold_the_world,
    advance,
    tick_rigs,
)
