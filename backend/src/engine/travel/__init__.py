# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
"""Transit between nodes (D-045, D-097, D-107).

The map is a **weighted graph**, not a grid: hence chokepoints, bridges and
passes worth fighting over. One can move only along an existing edge and only
on foot: this world has no teleport, neither for people nor for things.

## Where transit time comes from

**Surface decides everything.** `road.*_multiplier` are given as time
multipliers relative to the reference road: offroad is two to three times
longer, a paved highway faster. So transit time is the edge's own time times
the surface:

    time = base_seconds * road.<surface>_multiplier

An edge's own time is its **length** (D-319): the metres between the two
places on the globe -- an arc of the sphere on a planet, a straight line on a
flat inside -- walked at `travel.walk_speed_kmh`. `connect` derives it from
the places when nobody names it, and a ship's corridors name theirs (D-240).
Stored in seconds so that a step across the quarter and a crossing of the
steppe do not live in different units.

## Time is distance (D-319)

Nothing else prices a transit. The ring of a city stands `map.city_step_m`
from its printer, so the quarter is seconds; a found node stands as far from
the node it was explored from as the scout aimed (`biome.reach_m`, D-321), so
the wild is minutes step by step and hours over a day's finds -- and that is
the whole geography, read off the map instead of stored in a node. The
frontier step and growth of D-180 are gone: a run is priced as the walk of
its metres (`pay_for_road`), and there is no distance from civic land to keep
in step.

## The road costs stamina (D-147)

Time is a poor price: close the tab and you have arrived. So a transit has a
second price, and the body pays it:

    spend = travel.stamina_per_hour * road hours * satiety

The spend goes **by time**, not by number of transits: otherwise a step across
the quarter would cost as much as a crossing of the steppe, and geography would
turn inside out. The number is small -- an hour of walking is several times
cheaper than an hour at the face: the road tires but does not replace work.

With a convoy the spend is multiplied by `transport.stamina_k` = 0: the
vehicle carries, not the legs.

## A convoy changes both speed and the map itself (D-107, D-157)

A harnessed vehicle (`engine.transport`) does three things at once: carries
cargo in the hold, goes `transport.speed_k` times faster than on foot -- and
**narrows the graph**. Offroad lets no vehicle through at all, a heavy one
needs a paved highway, so autopath with a convoy is built over passable edges,
and a route that runs into the impassable stops at the last node -- the same
place it stops for lack of strength and at customs.

Hence the consequence all this was made for: **the road is a precondition of
trade, not a convenience.**

Written off **up front**, like batch materials: one cannot set out on a road
there is not enough strength for. On autopath this means the route breaks off
where strength sufficed -- the body stays in a node rather than dropping in the
middle of a leg.

## The graph changes in both directions (D-201)

Until the spaceship the map only grew: exploration added nodes with edges
(D-152), a road changed an edge's surface and overgrew without maintenance
(D-158), but no edge ever disappeared.

A ship is a **group of nodes of this same graph** with exactly one connector
node facing outwards, and docking is one edge between that connector and the
spaceport. So undocking is the removal of that one edge, and a flight is the
absence of it -- not a state of the body. `connect` and `disconnect` are the
whole of it; nothing else in the graph moves.

Two rules follow, and they hold for the whole map rather than for space alone:

* **an edge is removed, not flagged.** "The edge is there but you may not walk
  it" would be a second state to account for in routing, in the map's memory,
  in chat and in the search itself. An undocked ship is unreachable for exactly
  the reason any disconnected piece of the map is: there is no path;
* **an edge nobody walks on** -- otherwise a transit hangs between a node that
  is no longer adjacent and a body with nowhere to arrive. Undocking waits for
  the gangway to clear.

An autopath tail is a different matter: a route laid before the edge went away
is cut off at the node the body reached, the same way it is cut off by a
customs refusal or by lack of strength. A route is a plan, not a promise.

## A city has no gate (D-319)

D-206 gave a city exactly one door on foot -- the node marked `exit` -- and
`connect` refused an edge between the walls and the wild anywhere else. That
rule is withdrawn: the surface is one level of the graph, a city is a mark on
a group of nodes with a visible border, and an edge outward is lawful from any
node of it. What the gate used to carry moved to the border itself: customs
is settled on the transition between cities (D-123, below), and a siege
stands on every edge of the border rather than on one node.

The **spaceport** is still a machine and not a property: what a place is, is
set by what stands in it (D-176). Ship groups couple to the node a `shipyard`
stands in by one edge (D-201), a city builds itself a port and loses it with
the machine -- and since D-319 a port takes as many hulls as fit on its open
ground (`ship.hull_footprint`, `estate.hulls_footprint`).

## The border is settled at departure (D-123)

Duty, ban and duty-free norm live in `engine.customs`; here stands the single
point where a body changes city. Settled **before** leaving: not enough for the
duty -- the transit does not start at all, and no debt arises.

## While walking -- you are absent

In-person actions are closed, every one: mining, craft, loading, buying,
copying a recipe. Remote (orders, account, correspondence) works --
information travels over the Net, matter requires presence (D-044, D-047).

That is the price of the road: while you are on the way, the lot gets bought
and the price beaten down. Knowing the price is not getting the goods.
"""

from src.engine.travel._base import (  # noqa: F401
    AlreadyGoing,
    Asleep,
    EdgeInUse,
    Exit,
    Imprisoned,
    InTransit,
    NoEdge,
    NoRoute,
    NoStrength,
    NotGoing,
    TravelError,
    _edge_between,
    current,
    edge_seconds,
    has_transport,
    require_here,
    stamina_cost,
    surface_multiplier,
    walk_seconds,
)
from src.engine.travel.map import (  # noqa: F401
    connect,
    disconnect,
    exits,
    neighbours,
)
from src.engine.travel.walk import (  # noqa: F401
    arrive,
    depart,
    pay_for_road,
    route,
    turn_back,
)
