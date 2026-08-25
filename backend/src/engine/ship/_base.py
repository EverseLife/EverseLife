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
`ship.route_window_hours` and `ship.route_apart_hours` keyed by the pair of
planets -- exactly as a vehicle's capacity comes by its name (D-090). A
second-class engine appears in the vault and flies without a release.

## A passage costs what the sky costs today

The two vault numbers are the **ends** of a route, not its price: planets go
round the star at their own periods, so the way between any two of them
stretches and shrinks by itself. In conjunction Terra and Aurora are ten hours
apart, in opposition two days -- and everything between is the sky's doing, not
a setting. Hence the rule the whole of space trade rests on: **a passage is
planned.** Windows come round every two to five weeks of real time, and setting
out at the wrong hour costs four to five times over, in hours and in fuel
alike.

The time is settled once, at casting off, and never recomputed: a sky turning
under a ship already under way would make the passage longer than the one paid
for.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine.errors import Refusal
from src.models.ship import Ship
from src.models.world import Node
from src.units import (
    AMOUNT_SCALE,
)

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
`ship.route_window_hours` and `ship.route_apart_hours` keyed by the pair of
planets -- exactly as a vehicle's capacity comes by its name (D-090). A
second-class engine appears in the vault and flies without a release.

## A passage costs what the sky costs today

The two vault numbers are the **ends** of a route, not its price: planets go
round the star at their own periods, so the way between any two of them
stretches and shrinks by itself. In conjunction Terra and Aurora are ten hours
apart, in opposition two days -- and everything between is the sky's doing, not
a setting. Hence the rule the whole of space trade rests on: **a passage is
planned.** Windows come round every two to five weeks of real time, and setting
out at the wrong hour costs four to five times over, in hours and in fuel
alike.

The time is settled once, at casting off, and never recomputed: a sky turning
under a ship already under way would make the passage longer than the one paid
for.
"""


#: Thing classes from the vault (D-202, D-215): behaviour binds to a class,
#: never to an item name -- a second kind of foundation or fuel is data.
#: The class a node aboard is laid from.
FOUNDATION = "Основа корабля"


#: The class of machines a ship couples to and is laid down at.
SPACEPORT = "Верфь"


#: The class of machines that decide how many people the ship holds.
LIFE_SUPPORT = "Жизнеобеспечение"


#: The class of what a passage burns.
FUEL = "Корабельное топливо"


#: The class of vessels the engines draw from (D-230). Fuel in a canister
#: aboard is cargo, not a reserve: a passage burns tanks only.
TANK = "Топливный бак"


#: The class of the console a ship is commanded from (D-230). Casting off and
#: a passage are ordered standing at it, and it answers only in a room aboard:
#: bolted to a yard on the ground it is furniture.
BRIDGE = "Рубка"


#: The node property marking a node as being aboard. A property rather than a
#: fifth planet: the list of planets drags its own day length and environment
#: wear behind it, a property drags nothing (D-201).
ABOARD = "борт"

#: A planet property (D-233): a ship lands in **any** surface node of it, and
#: there is no spaceport anywhere on it. Written on the planet's node by the
#: seed, like its climate (D-231): what a planet is, is a fact of the world.
#:
#: Pyroxis is the one such planet, and for a reason: nothing is built there
#: (D-230), so there is nothing to put a yard into. What follows is the whole
#: character of the place -- the only infrastructure of Pyroxis is a ship's
#: hull, and a crew whose ship has left is standing on bare rock.
OPEN_LANDING = "посадка везде"


#: Amounts split into thousandths, so "was there enough" must tolerate the last
#: digit -- otherwise exactly enough fuel turns out to be short.
_EPS = 1 / AMOUNT_SCALE


def _gangway_seconds(constants: Constants, berth: int) -> float:
    """How long the gangway takes to walk: `ship.berth_seconds` per berth.

    A yard's berths are numbered, and the walk to one is as long as its number:
    the ship at the first berth is a second from the yard, the one at the fifth
    is five. So a busy port is a slower port, and that is the whole cost of
    somebody else being there before you.
    """
    return berth * constants[R.SHIP_BERTH_SECONDS]


async def _free_berth(session: AsyncSession, port: Node) -> int:
    """The lowest berth free at this port.

    **Lowest**, not next: casting off leaves a hole, and the next arrival fills
    it rather than walking past it to the end of the pier. A port that has seen
    a hundred ships come and go still boards the next one in a second.
    """
    taken = set(
        (
            await session.execute(
                select(Ship.berth).where(Ship.docked_node_id == port.id, Ship.berth.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    place = 1
    while place in taken:
        place += 1
    return place


class ShipError(Refusal):
    pass


class NotAboard(ShipError):
    """The body is not aboard. A ship is commanded from inside, not from the pier."""


class NoConsole(ShipError):
    """No working console here: a ship is commanded from its bridge, not from any room."""


class NotYours(ShipError):
    """Somebody else's ship. Shares between builders are a contract, not the
    engine's arithmetic (D-116)."""


class NoFoundation(ShipError):
    """No foundation in hand: a ship is materials, not an intention."""


class NoPort(ShipError):
    """No spaceport here: there is nothing to couple to."""


class NotEnoughThrust(ShipError):
    """Thrust against mass is below `ship.min_thrust_ratio`: it does not tear off."""


class NoLifeSupport(ShipError):
    """More people aboard than the life support holds."""


class NoFuel(ShipError):
    """Not enough fuel for the passage."""


class InFlight(ShipError):
    """The ship is undocked already: there is no edge to remove twice."""


class Docked(ShipError):
    """The ship is in port. Undock first -- the gangway is not flown away with."""


class TooFar(ShipError):
    """The weakest engine's class is below the route's (D-037, D-054)."""
