# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The road's vocabulary and floor: every refusal a way can make, the exit
mark and the reach, the price of an edge in seconds and stamina, and the
presence prologue (`require_here`, `current`). Asks nobody above itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import (
    explore,
    ship,
    transport,
    world,
)
from src.engine.errors import Refusal, left_to_say
from src.models.identity import Body
from src.models.travel import Travel, TravelState
from src.models.world import Edge, Node, Surface
from src.units import SECONDS_PER_HOUR


class TravelError(Refusal):
    pass


class NoEdge(TravelError):
    """No edge. Nobody walks in a straight line in this world."""


class NoRoute(NoEdge):
    """No path at all: the nodes are not connected by edges even through other nodes."""


class InTransit(TravelError):
    """The body is in transit. Matter requires presence, and there is no presence now."""


class EdgeInUse(TravelError):
    """People are walking the edge right now: it is not removed from under them (D-201).

    The gangway is not pulled from under a walker. Undocking waits, and that is
    the only precondition the removal of an edge has.
    """


class NotAnExit(TravelError):
    """An edge across a city's boundary at a node that is not a door (D-206).

    A city meets everything beyond it at the gate and at the spaceport, and
    nowhere else. A road laid into the middle of the built-up area would be a
    second gate made out of whatever node it happened to touch.
    """


class AlreadyGoing(TravelError):
    pass


class Imprisoned(TravelError):
    """Imprisonment: the body is held to the node until the term (D-095, D-166)."""


class NoStrength(TravelError):
    """Not enough strength for the road. Eat or sleep first (D-147)."""


@dataclass(frozen=True, slots=True)
class Exit:
    """Where one can go from here and how much time it costs."""

    edge_id: uuid.UUID
    node_id: uuid.UUID
    key: str
    name: str
    surface: Surface
    seconds: float
    #: Surface condition, 0..100 (D-158): a road without maintenance overgrows,
    #: and the player must see that in advance -- the convoy will stop where it overgrew.
    condition: float


def surface_multiplier(constants: Constants, surface: Surface) -> float:
    """Time multiplier by surface. The road is the reference (D-107)."""
    if surface is Surface.TRAIL:
        return constants[R.ROAD_TRAIL_MULTIPLIER]
    if surface is Surface.PAVED:
        return constants[R.ROAD_PAVED_MULTIPLIER]
    return constants[R.ROAD_ROAD_MULTIPLIER]


def edge_seconds(constants: Constants, edge: Edge) -> float:
    return edge.base_seconds * surface_multiplier(constants, edge.surface)


#: The node property "distance" (D-180): how many transits it is from civic
#: land. Built-up area has none at all, and that is the same as zero.
REACH = "distance"


def reach_of(node: Node) -> int:
    """The node's distance. Civic land and everything created before D-180 -- zero."""
    return int((node.properties or {}).get(REACH, 0) or 0)


#: The node property marking the city's gate (D-097, D-206): the one node of
#: the built-up area a road from beyond the walls may be tied to.
EXIT = "exit"


async def is_exit(session: AsyncSession, node: Node) -> bool:
    """Whether the node is one of the city's two doors (D-206).

    The gate is a property of the node, the spaceport is a machine standing in
    it: what a place is, is set by what stands in it (D-176), so a city gets a
    port by building one and loses it with the machine.
    """
    if (node.properties or {}).get(EXIT):
        return True

    return await world.has_station(session, node, ship.SPACEPORT)


async def gate_of(session: AsyncSession, node: Node) -> Node | None:
    """The gate of the city this node stands in. Outside a city -- nothing.

    This is where a road from beyond the walls is tied: exploration lays its
    trail from here rather than from the node the scout set out from (D-206).
    """

    city = await town.of_node(session, node)
    if city is None:
        return None
    return await town.gate(session, city)


def frontier_seconds(constants: Constants, reach: int) -> float:
    """Transit length to a node of this distance (D-180).

    The first ring beyond the walls costs `travel.frontier_step`, each next one
    `travel.frontier_growth` times more than the previous. The settled
    surroundings are thereby closer than the unexplored, and that is the only
    reason a near resource is hauled daily and a far one by expedition.
    """
    step = constants[R.TRAVEL_FRONTIER_STEP]
    growth = constants[R.TRAVEL_FRONTIER_GROWTH]
    return step * growth ** max(0, reach - 1)


async def has_transport(session: AsyncSession, body: Body) -> bool:
    """Whether the body drives a convoy. The vehicle is **harnessed**, not in the pocket (D-157).

    Previously a wagon was looked for in the hands, and that was nonsense: a
    wagon is heavier than the carry limit and is not taken in hand at all. It
    can be pulled only when harnessed, and the harness is the only sign by
    which the road tells a carter from a walker.
    """

    return await transport.harnessed(session, body) is not None


def stamina_cost(constants: Constants, seconds: float, *, transport: bool) -> float:
    """What a road of this length costs the body.

    The spend is computed by time, not by number of transits (D-147): otherwise
    a step across the quarter would cost as much as a crossing of the steppe.
    """
    spend = constants[R.TRAVEL_STAMINA_PER_HOUR] * seconds / SECONDS_PER_HOUR
    if transport:
        spend *= constants[R.TRANSPORT_STAMINA_K]
    return spend


async def current(session: AsyncSession, body: Body) -> Travel | None:
    """This body's ongoing transit, if any."""
    stmt = select(Travel).where(Travel.body_id == body.id, Travel.state == TravelState.GOING)
    return (await session.execute(stmt)).scalars().first()


class Asleep(TravelError):
    """The body sleeps. The same unavailability as the road, only voluntary."""


class InField(TravelError):
    """The body is exploring: it left on its own and returns on schedule or by cancel."""


async def require_here(session: AsyncSession, body: Body) -> None:
    """The presence check -- one for all in-person actions.

    The road must really cost time: otherwise leaving a node becomes free, and
    the geography all this was made for disappears. Sleep stands at the same
    door: a sleeper is unavailable for everything in-person (D-091) -- that is
    how hibernation pays for recovery. Exploration stands at it too (D-152):
    the scout leaves in person, and while in the field is not in the node.
    """
    if body.sleeping_since is not None:
        raise Asleep(key="travel-asleep")
    going = await current(session, body)
    if going is not None:
        raise InTransit(key="travel-in-transit", inner={"left": [left_to_say(going.arrives_at)]})

    run = await explore.pending(session, body)
    if run is not None:
        raise InField(key="travel-in-field", inner={"left": [left_to_say(run.run_at)]})


class NotGoing(TravelError):
    """The body is not on the road: there is nothing to turn back from."""


async def _edge_between(session: AsyncSession, one: uuid.UUID, other: uuid.UUID) -> Edge | None:
    stmt = select(Edge).where(
        or_(
            (Edge.node_a_id == one) & (Edge.node_b_id == other),
            (Edge.node_a_id == other) & (Edge.node_b_id == one),
        )
    )
    return (await session.execute(stmt)).scalars().first()
