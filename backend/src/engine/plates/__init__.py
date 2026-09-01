# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Eruptions: the planet redraws its own map (D-197, D-233).

Pyroxis was promised a moving map from the first day, and this is that promise
in code. An eruption is the same move the world already knows -- exploring
grows the graph, a road left unmaintained falls back to offroad -- only the
planet makes it instead of a person.

## What it does, and what it deliberately does not

* **Edges are redrawn, nodes are not.** Lava fills a pass, a cooled flow lies
  across a rift as a bridge. A node is where people stand and things lie
  (D-192), and it stays; a node is born of scouting and of nothing else (D-098).
* **Veins move.** In the shaken nodes a share of them goes out
  (`pyroxis.vein_relocate_share`) and as many light up next door. This is the
  measure against a staked claim: the vein leaves the monopolist by itself,
  with nobody's ill will and nobody's complaint to a court.
* **The Anvil Plateau is never shaken.** The one place on Pyroxis anything
  stands on is the one place the planet leaves alone (D-197).
* **What lies under the open sky burns.** Goods left in a field die with the
  ground they lie on -- the only loss of property here, and it is announced
  `pyroxis.eruption_warning` before it happens.
* **Nothing built is destroyed.** The world is eternal and there are no wipes
  (D-007): a base taken by lava is a wipe for one person, and the grudge would
  outlive any good the dynamics did.

## The two rules D-233 added

* **A node with people or property in it is never sealed.** There is always
  somewhere to walk. But **an edge may break under someone walking it**, and
  such a passage ends in death with the pocket lost for ever -- a sanctioned
  sink of matter, and the risk one takes by walking far from the ship.
* **Docking is untouchable.** The connector-to-node edge and the node a ship
  stands in are outside the draw: tearing a ship loose, or pulling the rock out
  from under it, would kill a crew by an event rather than by a mistake.

## What will not be here

The **forecast** D-197 once planned -- a seismologist's trade, sold days ahead
of the free signal -- is **cancelled** (D-235). The paid layer needed a
profession, an instrument and a market of information, and bought only "do not
lose a week of work"; the free signal buys a life, and that is the half the
world owes anybody. An eruption stays a thing one prepares for in general, not
a thing one buys the date of.

## The rooms

`clock` is the job chain and the orchestrating `erupted` handler, where the
package's one lock order is written down; `fire` burns what lies under the
open sky; `ways` tears and lays edges and kills whoever a way breaks under;
`veins` moves the veins and closes the faces at them; `_base` is the floor --
the exempt ground, the surface, the graph read whole.
"""

from src.engine.plates._base import (  # noqa: F401
    ANVIL,
    _adjacency,
    _connected,
    _exempt,
    _surface,
)
from src.engine.plates.clock import (  # noqa: F401
    _choose,
    ensure_scheduled,
    erupted,
    schedule,
    shaking,
    warned,
)
from src.engine.plates.fire import (  # noqa: F401
    _burn,
    _consume,
)
from src.engine.plates.veins import (  # noqa: F401
    _close_faces,
    _move_veins,
)
from src.engine.plates.ways import (  # noqa: F401
    _anchor,
    _bridge,
    _edge_between,
    _kill_on,
    _may_lose,
    _redraw,
)
