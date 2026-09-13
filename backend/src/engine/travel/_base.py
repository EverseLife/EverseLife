# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The road's vocabulary and floor: every refusal a way can make, the exit
mark and the reach, the price of an edge in seconds and stamina, and the
presence prologue (`require_here`, `current`). Asks nobody above itself.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import (
    climate,
    transport,
)
from src.engine.errors import Refusal, left_to_say
from src.models.identity import Body
from src.models.travel import Travel, TravelState
from src.models.world import Edge, Node, Surface
from src.units import METRES_PER_KM, SECONDS_PER_HOUR


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
    if surface is Surface.WILD:
        return constants[R.ROAD_WILD_MULTIPLIER]
    if surface is Surface.TRAIL:
        return constants[R.ROAD_TRAIL_MULTIPLIER]
    if surface is Surface.PAVED:
        return constants[R.ROAD_PAVED_MULTIPLIER]
    return constants[R.ROAD_ROAD_MULTIPLIER]


#: The off-road (D-338): the surfaces the season's snow lies on the way --
#: the wild and the trail, where no vehicle passes either
#: (`transport.passable`). A road is a road because it is kept clear.
OFF_ROAD = frozenset({Surface.WILD, Surface.TRAIL})


def snow_multiplier(constants: Constants, surface: Surface, snow: float) -> float:
    """How much longer the off-road is under the season's snow (D-338):
    `travel.snow_multiplier` under full cover, its share under less, and
    nothing at all on a road."""
    if surface not in OFF_ROAD:
        return 1.0
    return 1.0 + (constants[R.TRAVEL_SNOW_MULTIPLIER] - 1.0) * min(1.0, max(0.0, snow))


def edge_seconds(constants: Constants, edge: Edge, *, snow: float) -> float:
    """The time an edge takes: its metres at the walk, the surface's factor
    (D-107) and the snow on the way (D-338, `edge_snow`). `snow` is asked for
    on purpose: the walk and the route say the season's, and a caller that
    wants the edge's own time -- the map, the road window, a letter -- says
    nought where it can be read."""
    return (
        edge.base_seconds
        * surface_multiplier(constants, edge.surface)
        * snow_multiplier(constants, edge.surface, snow)
    )


def edge_snow(
    constants: Constants,
    edge: Edge,
    ends: tuple[Node | None, Node | None],
    origin: datetime | None,
    moment: datetime,
) -> float:
    """The snow an edge is walked through, nought to one (D-338): the mean of
    the season's snow on its two ends (`climate.snow_on`), the way running
    from the one to the other. A road is not asked at all -- the snow does not
    lengthen it -- and an end off the sphere has no ground to lie on."""
    if edge.surface not in OFF_ROAD:
        return 0.0
    lying = [climate.snow_on(constants, end, origin, moment) for end in ends if end is not None]
    return sum(lying) / len(lying) if lying else 0.0


def walk_seconds(constants: Constants, metres: float) -> float:
    """How long the road's reference walk over these metres takes (D-319).

    The surface's multiplier (D-107) is applied on top by `edge_seconds`, so
    this is the road's time: the wild is slower and the highway faster by their
    own factors, and the metres are the same metres.
    """
    pace = float(constants[R.TRAVEL_WALK_SPEED_KMH]) * METRES_PER_KM / SECONDS_PER_HOUR
    return max(1.0, metres / pace)


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


class Scouting(TravelError):
    """A run of the scout is under way: the road would be a second deed (D-211).

    The body does stand in its node for the whole run -- the map draws it on
    the way, the engine keeps it home (D-327) -- and this refusal is what makes
    that true: without it a scout could walk off and the two would part.
    """


async def require_here(session: AsyncSession, body: Body) -> None:
    """The presence check -- one for all in-person actions.

    The road must really cost time: otherwise leaving a node becomes free, and
    the geography all this was made for disappears. Sleep stands at the same
    door: a sleeper is unavailable for everything in-person (D-091) -- that is
    how hibernation pays for recovery.
    """
    if body.sleeping_since is not None:
        raise Asleep(key="travel-asleep")
    going = await current(session, body)
    if going is not None:
        raise InTransit(key="travel-in-transit", inner={"left": [left_to_say(going.arrives_at)]})


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
