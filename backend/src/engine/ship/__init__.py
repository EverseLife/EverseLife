# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The ship: a group of nodes coupled to a spaceport by one edge (D-201, D-202).

A ship is **not a thing standing in a node** and not a layer of its own. Its
rooms are ordinary nodes of the same graph: with their own area, their own
chat, their own machines and their own edges. Outwards the group faces through
exactly one **connector** -- the node laid first -- and docking is one edge
between that connector and the spaceport.

Hence the whole of space is two operations on one edge:

    docking    = travel.connect(port, connector)
    undocking  = travel.disconnect(port, connector)

and the flight is the state of having no such edge. A body aboard needs no
"in flight" flag: there is simply nowhere to step off to.

## Why it is a subgraph and not a vehicle

A vehicle is harnessed to and carries cargo in a hold (`engine.transport`). One
does not walk inside a vehicle -- while inside a ship people must walk: to the
bridge, to the hold, to the engine room. Two models of one object would have
diverged the day somebody asked where a person flying to Aurora actually is.
As a subgraph the answer is the same as everywhere else: in a node.

## The ship grows by a node at a time

One comes to a spaceport and lays a foundation, giving up an **Основа узла
корабля**. The first node appears -- the base, the connector and the docking
point at once. The same action from any node aboard lays one more, joined to
the one it was laid from. A ship is therefore built the way a city is settled,
and its shape is somebody's decision rather than a recipe's.

A node aboard is a **building** from the first second: machines take area
(D-106), so a hull section has `ship.node_area` of it. What the ship can do is
set by what stands in it -- engines, navigation, life support are machines, not
lines of a recipe.

## Speed is thrust against mass

    ratio = sum of thrust of the engines / (mass of the nodes + everything aboard)
    hours = table time * ship.reference_ratio / ratio

Below `ship.min_thrust_ratio` the ship **does not undock at all** -- it does not
"fly slowly", it does not tear off, and that is known before the attempt rather
than after. Faster than `ship.route_min_share` of the table it does not go: a
speed ceiling, otherwise it is enough to hang engines on a single node.

There is no capacity number anywhere. Overload shows itself as a longer passage
and, in the limit, as a ship that stays in port -- which reads better than
"capacity exceeded". What a crew member carries in their own hands is not
weighed: a pocket against a hull is rounding.

## Two things an undocked ship must never become

A ship with no edges cannot be reached: fuel cannot be brought to it and nobody
aboard can walk off. So casting off is refused without fuel for at least the
way back into the very port being left -- the cheapest passage there is. A trap
with no way out is not built in this world (pillar P6), and this is the only
one a ship could have created.

The other one is a second way in. The connector must stay alone, so
exploration from aboard is refused as well (`engine.explore`): a find arrives
with an edge from the node it was made from, and an edge out of a hull would
quietly weld the ship to a wild node past the inspection at the gangway.

## What the engine keeps no list of

Neither engines nor routes. Thrust and class come by the item's name from
`ship.thrust` and `ship.engine_class`, passage times from
the orbits of the two planets and the flight time chosen (D-271), not a table of
planets -- exactly as a vehicle's capacity comes by its name (D-090). A
second-class engine appears in the vault and flies without a release.

## Legs by the hour, and the sky between them (D-245, D-289, D-354)

A journey from a pad to another world is a climb, a crossing and a descent.
The climb and the descent are legs by the hour -- the atmosphere, which the
sky does not fly -- priced by the planet's own pull (`planet.mass` over
`planet.radius` squared, D-320); the descent is the cheaper of the two,
because coming down the weight one climbed against is on the ship's side.
Between them the hull is a body in the sky with its own place and speed:
there is no node above a planet any more (D-354). "In orbit" is what the sky
says of a hull that coasts on a closed orbit round a planet; the climb ends
on such an orbit, over the meridian of the pad it left, and the descent
starts from one close enough in. The crossing is an order the helm flies.

The reason the legs are priced at all is that they used to be **less** than
nothing: the gangway came off instantly and for nothing, and coming back
down onto the very pad just left cost a whole passage. Leaving a planet was
cheaper than returning to it, which is the wrong way round for every world
there is. And with one number for a whole planet there was nowhere to say
that Pyroxis is heavy.

## A passage costs what the sky costs today

The two vault numbers are the **ends** of a route, not its price: planets go
round the star at their own periods, so the way between any two of them
stretches and shrinks by itself. In conjunction Terra and Aurora are ten hours
apart, in opposition two days -- and everything between is the sky's doing, not
a setting. Hence the rule the whole of space trade rests on: **a passage is
planned.** Windows come round every two to five weeks of real time, and setting
out at the wrong hour costs four to five times over, in hours and in fuel
alike.

The hours are the order, not a table entry (D-289): the sky is simulated
under five bodies, the helm re-solves the passage from where the hull is
every tick, and the tanks pay as the engines burn. A hull that runs dry
coasts -- for ever, onto a body, or out of the system, and the forecast says
which and when -- until fuel reaches it and a new order is laid from there.

A package: one module per section of the old file; this file re-exports
the names so `from src.engine import ship` reads as before.
"""

from src.engine.ship import (
    course,  # noqa: F401
    fate,  # noqa: F401
    helm,  # noqa: F401
    sim,  # noqa: F401
)
from src.engine.ship._base import (  # noqa: F401
    _EPS,
    ABOARD,
    AIR,
    BRIDGE,
    CLIMB,
    DESCENT,
    FOUNDATION,
    FUEL,
    GROUND_BRIDGE,
    LIFE_SUPPORT,
    LOST,
    OPEN_LANDING,
    PASSAGE,
    SPACEPORT,
    TANK,
    Deaf,
    Docked,
    InFlight,
    NoArc,
    NoConsole,
    NoFoundation,
    NoFuel,
    NoLifeSupport,
    NoPort,
    NotAboard,
    NotEnoughThrust,
    NotYours,
    ShipError,
    TooFar,
    _free_berth,
    _gangway_seconds,
    hull_footprint,
)
from src.engine.ship.belonging import (  # noqa: F401
    aboard_of,
    crew_of,
    is_aboard,
    lock_crew,
    nodes_of,
    of_node,
    ships_of,
)
from src.engine.ship.building import (  # noqa: F401
    _add_node,
    _found_ship,
    _foundation_at_hand,
    _lay,
    _node_aboard,
    _planet_root,
    _spend,
    extend,
    found,
    keel_laid,
    moor_to,  # noqa: F401
)
from src.engine.ship.command import (  # noqa: F401
    _commanded_by,
    _has_bridge,
    _landable,
    _will_take,
)
from src.engine.ship.crossing import cancel, circle_star, fly  # noqa: F401
from src.engine.ship.feed import (  # noqa: F401
    BadVesselName,
    NoSuchPort,
    NotOnLine,
    name_vessel,
    set_lines,
)
from src.engine.ship.feed import view as lines_view  # noqa: F401
from src.engine.ship.flight import (  # noqa: F401
    arrived,
    ascend,
    land,
    recall,
)
from src.engine.ship.meet import dock, undock  # noqa: F401
from src.engine.ship.physics import (  # noqa: F401
    _sphere,
    _things,
    burn_checked,
    climb_hours,
    corridors,
    efficiency,
    engine_class,
    engines,
    engines_aboard,
    fall_hours,
    fuel_aboard,
    fuel_energy,
    fuel_for,
    fuel_stacks,
    fuel_worth,
    gravity,
    life_support,
    mass,
    mass_parts,
    orbits_of,
    passage_curve,
    passage_hours,
    ratio,
    sky_days,
    spend_fuel,
    thrust,
)
from src.engine.ship.shape import (  # noqa: F401
    BadName,
    OffTheGrid,
    arrange,
    rename,
)
from src.engine.ship.view import (  # noqa: F401
    beacon_lit,
    forecast,
    in_sight,
    landings,
    lands_anywhere,
    lit_ports,
    open_landings,
    passages,
    ports,
    profile,
)
