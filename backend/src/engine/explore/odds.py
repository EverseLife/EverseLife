# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""explore: what the next run from here is worth.

Split out of `engine/explore.py` along its sections. Two forces set the
price of a search and neither is about the searcher (D-156, D-207):
**depletion** -- how many finds this node has already given -- and
**crowding** -- how many edges the find would hang next to. Both are read
here, and the forecast the player is shown and the roll the run is made by
come out of the same numbers: a promise and a price that disagreed would be
worse than either.
"""

from __future__ import annotations

import random

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current_catalog
from src.constants import registry as R
from src.engine import city as town
from src.engine import frost, ruins, travel
from src.engine.explore._base import (
    FOREST,
    FOUND_HERE,
    LOT,
    ROOM,
    SITE,
    VEIN,
    mineable,
)
from src.models.identity import Body
from src.models.world import Edge, Layer, Node
from src.units import MINUTES_PER_HOUR, PERCENT


def found_here(node: Node) -> int:
    """How many finds have already been made from this node (D-156)."""
    return int((node.properties or {}).get(FOUND_HERE, 0))


def chance(constants: Constants, node: Node) -> float:
    """The chance of a run from here, in percent. Falls with each find down to the floor.

    The floor exists so that a trodden place grows poorer rather than locked: a
    node one can no longer go into the field from is a dead end, and the map is
    eternal (D-007).
    """
    decline = constants[R.EXPLORE_FIND_DECAY] ** found_here(node)
    return max(constants[R.EXPLORE_FIND_FLOOR], constants[R.EXPLORE_FIND_CHANCE] * decline)


async def possible(session: AsyncSession, node: Node) -> tuple[str, ...]:
    """Which goals make sense in this node at all.

    The client draws its buttons from this rather than guessing by map layer,
    and `survey` refuses anything not in it: a goal that would be refused must
    not be offered, and one that is offered must not be refused.

    Inside a city of the Forerunners one opens their next room -- and at its
    **door** one may also set out for the ice, or there would be no way off the
    three cities the seed lays (D-232). Inside a city of people a lot is added
    to the open world rather than replacing it: a find beyond the walls ties
    itself to the gate wherever it was sought from (D-206).
    """
    #: Nothing grows where the ground bakes (D-231, D-233): a grove found on
    #: Pyroxis would be a place property nobody could explain, and felling it
    #: reads the same property the search would have written.
    beyond = (
        (SITE, VEIN)
        if await frost.climate_of(session, node) == frost.HEAT
        else (
            SITE,
            VEIN,
            FOREST,
        )
    )
    #: Inside a city of the Forerunners the search is for their next room
    #: (D-232): nothing is founded here and nothing is felled, and a frozen city
    #: hung on a corridor would walk around the gate rule (D-206).
    #:
    #: **Except at the door.** A city's pier is where the ice plains begin, and
    #: without this the whole of Aurora would end at three cities: the seed lays
    #: no wild node on the planet, a ship lands only at a pier, and a search for
    #: new cities would have nowhere to start from. From the door one goes out
    #: onto the ice; from a corridor one goes deeper in.
    if await ruins.city_of(session, node) is not None:
        return (ROOM, *beyond) if await travel.is_exit(session, node) else (ROOM,)
    #: Everywhere else the world beyond the walls is open from anywhere: a find
    #: made from inside a city ties itself to the gate, not to the node one set
    #: out from (D-206). A lot is the one goal that needs a city around it.
    if node.layer is Layer.CITY and await town.of_node(session, node) is not None:
        return (LOT, *beyond)
    return beyond


async def anchor_of(session: AsyncSession, origin: Node, goal: str) -> Node:
    """The node a find from here will hang on -- and whose crowding decides the chance.

    A plot lands inside the built-up area, on the very node it was sought from; a
    find beyond the walls hangs on the city's gate (D-206). So "how crowded is
    it here" is a question about the gate for the second case, and measuring the
    node one set out from would miss exactly the star the gate is growing.
    """
    if goal in (LOT, ROOM):
        return origin
    gate = await travel.gate_of(session, origin)
    return gate if gate is not None else origin


async def crowding(session: AsyncSession, constants: Constants, node: Node) -> float:
    """Chance multiplier for the crowding of the graph around this node (D-207).

    A find is an edge, and edges pile up where everybody wants to be: at the
    bioprinter, at the city gate. Thirty edges on one node is a place one can
    neither walk through nor look at, so the search there gets worse -- by the
    node's own degree and by its neighbours' extra edges.

    Neighbours are counted **without** the edge back here: a chain of nodes
    creates no crowding, a cluster does. Below `explore.crowding_floor` the
    multiplier does not fall: the map is eternal (D-007) and has no place one can
    never search from again.
    """
    edges = (
        (
            await session.execute(
                select(Edge).where(or_(Edge.node_a_id == node.id, Edge.node_b_id == node.id))
            )
        )
        .scalars()
        .all()
    )
    neighbours = {edge.node_b_id if edge.node_a_id == node.id else edge.node_a_id for edge in edges}
    degree = len(edges)

    #: The neighbours' degrees, in one query: every endpoint that falls inside the
    #: set is one edge of somebody in it.
    around = 0
    if neighbours:
        rows = (
            (
                await session.execute(
                    select(Edge).where(
                        or_(
                            Edge.node_a_id.in_(neighbours),
                            Edge.node_b_id.in_(neighbours),
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        incidences = sum(
            (edge.node_a_id in neighbours) + (edge.node_b_id in neighbours) for edge in rows
        )
        #: Minus the edges leading back here: those are already counted as `degree`.
        around = max(0, incidences - len(neighbours))

    crowd = degree + constants[R.EXPLORE_CROWDING_NEIGHBOUR_K] * around
    over = max(0.0, crowd - constants[R.EXPLORE_CROWDING_FREE])
    floor = constants[R.EXPLORE_CROWDING_FLOOR] / PERCENT
    return max(floor, constants[R.EXPLORE_CROWDING_DECAY] ** over)


def _cap(constants: Constants) -> float:
    """The run duration ceiling in minutes: depletion grows it no further."""
    return constants[R.EXPLORE_ATTEMPT_HOURS] * MINUTES_PER_HOUR


def minutes_of(constants: Constants, node: Node, dice: random.Random) -> float:
    """How long a run from here takes. Each find lengthens the next."""
    run = constants[R.EXPLORE_ATTEMPT_MINUTES]
    depletion = constants[R.EXPLORE_EFFORT_GROWTH] ** found_here(node)
    return min(_cap(constants), dice.uniform(run.min, run.max) * depletion)


def span(constants: Constants, node: Node) -> tuple[float, float]:
    """The shortest and the longest a run from here can turn out to be."""
    run = constants[R.EXPLORE_ATTEMPT_MINUTES]
    depletion = constants[R.EXPLORE_EFFORT_GROWTH] ** found_here(node)
    cap = _cap(constants)
    return min(cap, run.min * depletion), min(cap, run.max * depletion)


def stamina_for(constants: Constants, minutes: float) -> float:
    """The run's price in stamina: by time in the field, not per piece.

    `explore.attempt_stamina` is the price of a full-length run. A per-piece
    price would lock early runs with stamina exactly where D-156 unlocks them
    with time.
    """
    return constants[R.EXPLORE_ATTEMPT_STAMINA] * minutes / _cap(constants)


def price(constants: Constants, node: Node) -> float:
    """The most a run from here can cost in stamina -- the price of its longest length.

    The place's price, and the forecast and the door read it alike. The length
    is rolled at departure, so a run has no single price in advance, and of the
    possible thresholds only the ceiling is honest: it does not move between
    one press and the next, while a threshold at the roll would turn a second
    press into a second throw of the dice -- one would press until a short run
    came up.

    What the body does to this number is the body's own (`engine.food`,
    `engine.frost`): the door multiplies it by the cold and the last meal, the
    forecast cannot -- settling the cold writes, and a forecast may not.
    """
    return stamina_for(constants, span(constants, node)[1])


async def outlook(
    session: AsyncSession,
    constants: Constants,
    body: Body,
    *,
    goal: str = SITE,
    resource: str | None = None,
) -> dict | None:
    """What a run from here will cost -- before leaving.

    The price of exploration changes from place to place (D-156), and a price
    that cannot be seen in advance reads as engine randomness. Aiming is
    computed right here: a requested species is found the worse the rarer it is
    (D-151), and showing "90% chance" to someone going for gold would be a lie.

    Crowding is shown apart from the chance for the same reason (D-207): "here it
    is cramped" is a fact about the place the player can act on -- by walking a
    day out and setting off from the frontier -- and hiding it inside one number
    would leave only bad luck to blame.
    """
    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover -- a body always stands in a node
        return None
    short, long_ = span(constants, node)
    aim = aim_at(constants, current_catalog(), goal, resource)
    anchor = await anchor_of(session, node, goal)
    press = await crowding(session, constants, anchor)
    #: A worked-out city is the fourth thing that narrows the chance, and it
    #: narrows it to nothing (D-232). Left out, the button would promise ninety
    #: percent in a city where the true answer is none -- exactly the price in
    #: advance that D-156 exists for.
    wear = await wear_of(session, constants, node, goal)
    return {
        "explored": found_here(node),
        "minutes": {"min": short, "max": long_},
        #: The largest this place can ask, and what the door asks before the
        #: body's own cold and hunger are counted into it: the player must know
        #: the ceiling, not the average.
        "stamina": price(constants, node),
        "chance": chance(constants, node) * aim * press * wear,
        #: How much of the city is already open: a fact of the place the player
        #: can act on -- by walking to the next city (D-232).
        "worked_out": wear,
        #: By how much the species request narrowed the chance: the player sees
        #: not only "little" but why little (D-151).
        "aim": aim,
        #: And by how much the crowding of the place narrowed it (D-207), plus the
        #: node the find will hang on -- from the city that is the gate, not here.
        "crowding": press,
        "anchor": anchor.name if anchor.id != node.id else None,
        "resource": resource,
    }


async def wear_of(session: AsyncSession, constants: Constants, node: Node, goal: str) -> float:
    """How much a city already opened narrows the search in it (D-232).

    One place, asked by both the forecast and the departure: a promise and a
    price that disagreed would be worse than either.
    """
    if goal != ROOM:
        return 1.0
    city = await ruins.city_of(session, node)
    return 1.0 if city is None else ruins.worked_out(constants, city)


def aim_at(constants: Constants, catalog: Catalog, goal: str, requested: str | None) -> float:
    """Chance multiplier for aiming.

    A named species is found worse than an unnamed one, and exactly as many
    times worse as it is rarer: the share of its pace in `harvest.rates`
    relative to the fastest. No second rarity table -- it would diverge from
    the first (D-151).
    """
    #: Woods asked for are found as often as the world is wooded (D-191): one
    #: number rules both the chance random finds carry a forest and the price
    #: of aiming for one.
    if goal == FOREST:
        return constants[R.EXPLORE_FOREST_SHARE] / PERCENT
    if goal != VEIN or requested is None:
        return 1.0
    paces = constants[R.HARVEST_RATES]
    mining_ = [name for name in mineable(catalog) if float(paces.get(name, 0)) > 0]
    if requested not in mining_:
        return 1.0
    most_common = max(float(paces[name]) for name in mining_)
    return float(paces[requested]) / most_common
