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

An edge's own time is rolled when the map appears: inside a city from
`travel.city_step`, beyond the walls from the node's **distance** (D-180).
Stored in seconds so that a step across the quarter and a crossing of the
steppe do not live in different units.

## Distance: the farther from a city, the pricier the step (D-180)

Distance is a node property: how many transits it is from civic land. Built-up
area is 0, the first ring beyond the walls is 1, a find from a node of distance
`d` is `d + 1`. The transit length to it:

    base_seconds = travel.frontier_step * travel.frontier_growth ^ (d - 1)

The settled surroundings are thereby closer than the unexplored: the near mine
is walked to in twenty seconds, the far frontier requires an expedition.
Distance is stored in the node rather than computed over the graph: the map
grows in branches, and "how many steps to the nearest city" would have to be
recomputed on every find.

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
  it" would be a second state to account for in routing, in exploration, in
  chat and in the search itself. An undocked ship is unreachable for exactly
  the reason any disconnected piece of the map is: there is no path;
* **an edge nobody walks on** -- otherwise a transit hangs between a node that
  is no longer adjacent and a body with nowhere to arrive. Undocking waits for
  the gangway to clear.

An autopath tail is a different matter: a route laid before the edge went away
is cut off at the node the body reached, the same way it is cut off by a
customs refusal or by lack of strength. A route is a plan, not a promise.

## A city touches the outside world only through its exits (D-206)

The built-up area is a group of nodes joined by short edges, and until now
nothing said **where** that group meets everything beyond it. So an edge out of
the steppe could be welded to any node of it: a trail from the trading yard
straight into the wild made a second gate out of a market, and the route out of
the city stopped passing the gate at all.

A city therefore has exactly two doors, and both are nodes:

* **the gate** -- the node property `exit`. The one place one leaves the walls
  on foot, and hence the one place one arrives at from outside;
* **the spaceport** -- the node a `shipyard` machine stands in. Ship groups
  couple to it by one edge (D-201), and a ship is the only thing that arrives
  anywhere but the gate.

The rule lives in `connect`, because an edge is created nowhere else: an edge
between a city node and a node outside that city is allowed only at an exit.
Inside one city nothing is checked -- a street is not a border; outside every
city nothing is checked either -- wild land has no walls to have doors in.

The spaceport is a machine rather than a second property on purpose: what a
place is, is set by what stands in it (D-176). A city builds itself a port, and
loses it with the machine -- without a property to keep in step.

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
    EXIT,
    REACH,
    AlreadyGoing,
    Asleep,
    EdgeInUse,
    Exit,
    Imprisoned,
    InField,
    InTransit,
    NoEdge,
    NoRoute,
    NoStrength,
    NotAnExit,
    NotGoing,
    TravelError,
    _edge_between,
    current,
    edge_seconds,
    frontier_seconds,
    gate_of,
    has_transport,
    is_exit,
    reach_of,
    require_here,
    stamina_cost,
    surface_multiplier,
)
from src.engine.travel.map import (  # noqa: F401
    connect,
    disconnect,
    exits,
    neighbours,
    require_exit,
)
from src.engine.travel.walk import (  # noqa: F401
    arrive,
    depart,
    route,
    turn_back,
)
