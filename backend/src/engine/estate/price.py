# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""estate: land price (D-089).

Split out of `engine/estate.py` along its sections (review 2026-08-23, wave 3).
The sale and the land tax went on to rooms of their own when this passed the
eight hundred lines the quality bar allows (`sale`, `tax`, 2026-09-19): what
stays is the distance to a city's printer and the price it gives.
"""

from __future__ import annotations

import uuid
from collections import deque

from sqlalchemy import ColumnElement, and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.db.base import remember
from src.engine import city as town
from src.engine import events, travel, world
from src.engine.estate._base import BadName, EstateError, NotForSale, NotOwner
from src.models.city import City, Power
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.world import ABOARD, Edge, Node
from src.runtime import LAND_ABOUT_LIMIT, LAND_NAME_LIMIT
from src.units import (
    PERCENT,
    money,
)


async def center_of(session: AsyncSession, city: City) -> Node | None:
    """The city's centre: the bioprinter it grew from (D-023, D-089, D-208).

    Every city counts from its **own** printer, not from the capital's: the
    rate is announced at the bioprinter (D-220), and a city has one of its own
    or it is not a city.

    Which node that is, the city answers itself (`city.core`), and it is asked
    rather than worked out again here. A second way of naming the centre is a
    second answer waiting to happen -- and the written distance is measured
    **to** this node, so two names for the centre would mean measuring the city
    over and over, each reader disagreeing with what the last one wrote.
    """

    return await remember(session, ("center_of", city.id), lambda: town.core(session, city))


async def forget_distances(session: AsyncSession) -> None:
    """Drop what can be measured again: the graph itself has changed.

    Called where an edge appears or goes, and nowhere else -- `travel.connect`
    and the undocking that removes a gangway. A trail laid by a scout may
    shorten the way to the centre for a whole quarter, so measuring is not
    patched here -- it is dropped, and the next world tick takes it again
    (`measure_cities`). Until it does, whoever asks a price works the distance
    out and writes nothing: a read does not write, and the answer is the same
    answer.

    **A city with no printer is left alone** (D-307). Dropping is a way of
    saying "measure this again", and there is nothing to measure a printerless
    city with: `nodes_from_center` would fall back to nought, which is the
    centre's own rate -- the dearest in town -- for every plot of a city that
    just lost its centre. That is precisely what D-307 forbids, and until this
    line it was one eruption away: Pyroxis lays an edge on every eruption
    (`plates._bridge`, through `connect` like everything else) and the deploy
    seed lays them too, so "what was measured while the printer stood" survived
    only until the next tremor. So the drop asks, city by city, whether there
    is a centre to measure from, and passes over the cities that have none.

    The tearing side of an eruption deletes its edges itself and does not come
    here, and that is not an omission: what is measured is the way to a city's
    centre, and Pyroxis has no cities and never will (D-230, D-233). No node of
    it carries a distance to drop.

    One statement per city, and this happens when a road is laid or a ship
    casts off, not in the course of a day's play. A world with nothing measured
    in it leaves by the first question -- and that is the whole of seeding,
    where every second call lays an edge and there is not a number yet to drop.
    """
    anything = await session.scalar(select(Node.id).where(Node.center_steps.is_not(None)).limit(1))
    if anything is None:
        return

    cities = (await session.execute(select(City))).scalars().all()
    for city in cities:
        if await center_of(session, city) is None:
            continue
        await session.execute(
            update(Node)
            .where(_owned(city), Node.center_steps.is_not(None))
            .values(center_node_id=None, center_steps=None)
        )


async def note_new_place(session: AsyncSession, one: Node, other: Node) -> None:
    """A place just joined to the map takes its distance from what it joined to.

    This is the whole of measuring, in play. The map grows only at its edges: a
    scout hangs a node nothing led to yet, and no road is ever laid between two
    places already on it -- so a new plot is exactly one step further from the
    printer than the place it was found from, and nothing else moves. Walking
    the graph for that would be answering a question the map has already
    answered (D-220).

    Only the built-up area is counted (`built_up`): beyond the walls the land
    is nobody's and pays nothing (D-198), and a ship is a dead end of its own
    (D-201, D-202). And only from an anchor that has a distance itself -- an
    old world whose nodes were never measured is measured once, by the walk
    below, and grows by this rule from then on.
    """

    for anchor, fresh in ((one, other), (other, one)):
        if fresh.center_steps is not None or anchor.center_steps is None:
            continue
        if not await world.is_built_up(session, fresh):
            continue
        if (fresh.properties or {}).get(ABOARD) or (anchor.properties or {}).get(ABOARD):
            continue
        fresh.center_node_id = anchor.center_node_id
        fresh.center_steps = anchor.center_steps + 1
        await session.flush()
        return


async def steps_from(session: AsyncSession, center: Node) -> dict[uuid.UUID, int]:
    """How many nodes each place is from this centre. **Reads only.**

    The walk itself, told apart from writing it down (`measure_city`), because
    the two have different callers: the tick writes, and a read -- the plot
    screen, the price under the buy button -- only asks. Filling the cache from
    inside a read is what this split is for: `look` is declared readonly, and
    for a while it wrote a row for every plot of a city nobody had measured yet
    (`db/readonly`, CLAUDE.md "чтение не пишет").

    Remembered for the command (`db.base.remember`): the plot screen asks the
    price and the day's tax of one place, and both want the same walk. The
    memory dies on any write, so an edge laid in the same command is not
    answered from before it -- and by the same token it is **no help to a
    writer**: the day's levy transfers money for every plot in turn, and each
    transfer throws the memory away. That is why the levy measures first
    rather than leaning on this.
    """

    return await remember(session, ("steps_from", center.id), lambda: _walk_from(session, center))


#: The map as a walk sees it: who is next to whom, and what is afloat.
Graph = tuple[dict[uuid.UUID, list[uuid.UUID]], set[uuid.UUID]]


async def _graph(session: AsyncSession) -> Graph:
    """Read the map once. Two queries, and both are the whole of the cost here."""

    edges = (await session.execute(select(Edge))).scalars().all()
    neighbours: dict[uuid.UUID, list[uuid.UUID]] = {}
    for edge in edges:
        neighbours.setdefault(edge.node_a_id, []).append(edge.node_b_id)
        neighbours.setdefault(edge.node_b_id, []).append(edge.node_a_id)

    #: The walk stops at the gangway. A ship moored in the port is a whole
    #: little map of its own, and none of it is land: to a ship's node only a
    #: ship's node is ever joined, so no road leads back out through a hull and
    #: no distance is ever wanted for one. Without this the walk wandered the
    #: cabins of every ship in port, and the "farther than any road" number
    #: moved with the shipping.
    afloat = set(
        (await session.execute(select(Node.id).where(Node.properties.has_key(ABOARD)))).scalars()
    )
    return neighbours, afloat


def _walk(graph: Graph, center: uuid.UUID) -> dict[uuid.UUID, int]:
    """The walk itself: no database, so a tick measuring ten cities reads the
    map once and walks it ten times."""

    neighbours, afloat = graph
    steps = {center: 0}
    queue: deque[uuid.UUID] = deque([center])
    while queue:
        here = queue.popleft()
        for neighbour in neighbours.get(here, ()):
            if neighbour not in steps and neighbour not in afloat:
                steps[neighbour] = steps[here] + 1
                queue.append(neighbour)
    return steps


async def _walk_from(session: AsyncSession, center: Node) -> dict[uuid.UUID, int]:
    return _walk(await _graph(session), center.id)


def _owned(city: City) -> ColumnElement[bool]:
    """Which nodes are this city's -- and "this city's" must mean exactly what
    `city.of_node` means, in the same order: land the city holds, its own
    delegate node, and what hangs off that node while no other city holds it.

    Measuring every node the walk reached instead would have the two cities of
    one road overwrite each other's measurements turn by turn. Written once
    because the tick asks the same question the writer answers: what is behind
    must be the very set that gets written.
    """

    return or_(
        Node.owner_city_id == city.id,
        Node.id == city.node_id,
        and_(Node.parent_id == city.node_id, Node.owner_city_id.is_(None)),
    )


async def measure_city(
    session: AsyncSession, center: Node, city: City, graph: Graph | None = None
) -> dict[uuid.UUID, int]:
    """Write down what the walk found, for the whole city at once.

    A write, and called from write contexts only -- `measure_cities` below, and
    through it the world tick and the day's levy. Whoever reads a distance gets
    it from `steps_from` and leaves nothing behind. `graph` is the map already
    in hand, when there is one: a tick measuring several cities reads it once
    for all of them.
    """

    steps = _walk(graph, center.id) if graph else await steps_from(session, center)
    #: No road to the node -- the land lies beyond the farthest ring the city
    #: reaches, and it is counted as further than any of them.
    beyond = len(steps)
    mine = (await session.execute(select(Node).where(_owned(city)))).scalars().all()
    for plot in [*mine, center]:
        plot.center_node_id = center.id
        plot.center_steps = steps.get(plot.id, beyond)
    await session.flush()
    return steps


async def nodes_from_center(session: AsyncSession, node: Node, city: City) -> int:
    """The plot's distance from its city's centre -- in nodes from the bioprinter.

    Land value falls with each node from the printer (D-220). Measured by
    edges, not by the "ring" property: the property is a record at generation,
    edges are how people really walk the city.

    Read from the node, walked for only when what is written there was measured
    to another centre or dropped by a change in the graph (`models/world.Node`).

    **And the walk writes nothing.** This is the plot screen's road as much as
    the levy's, and `look` is a read (CLAUDE.md): filling the cache here wrote
    a row per plot from inside a command declared readonly, which raised on a
    developer copy and passed silently in production. The cache is filled by
    the tick (`measure_cities`) and grown at the edges by `note_new_place`;
    until it has been, the number is worked out afresh -- the same number, at
    the price of one walk per command.
    """
    center = await center_of(session, city)
    if center is None:
        #: The printer is gone from the core -- carried out, or never put back.
        #: What was measured while it stood stays (D-307): the land did not
        #: move, and the last rate the city announced is the last one it
        #: announced. The alternative was to call the distance nought, and a
        #: city that lost its machine would start charging every plot the
        #: centre's own rate -- the dearest in town, for the place that just
        #: lost its centre. Put the machine down somewhere else and the city is
        #: measured from there instead, all of it at once: the centre is the
        #: machine and not a mark on the map (D-208), so moving it is a move of
        #: the city's own, and every rate in town follows.
        #:
        #: A plot nobody had measured by then has nothing to keep, and the
        #: engine does not invent it a distance: it stands at nought until a
        #: printer stands in the city again and the tick measures it. Nought is
        #: the **centre's** own rate, the dearest in town, so the only city this
        #: may befall is one that never had its distances taken at all and lost
        #: its printer before the first tick after that. A city measured once
        #: keeps what it has: a new road no longer drops it either, because a
        #: drop means "measure this again" and there is nothing to measure it
        #: with (`forget_distances`, D-307).
        return node.center_steps if node.center_steps is not None else 0
    if node.center_node_id == center.id and node.center_steps is not None:
        return node.center_steps

    steps = await steps_from(session, center)
    return steps.get(node.id, len(steps))


async def measure_cities(session: AsyncSession) -> int:
    """Measure every city that has none, and write it down. World tick.

    What must tick, ticks in a worker job (CLAUDE.md): the distance to a
    centre is a cache, and the two things that empty it -- a new world, and a
    road laid anywhere (`forget_distances`) -- are both write moments nobody
    is standing at. So the tick picks the work up rather than the next player
    to open a plot screen.

    One question per city while there is nothing to do, and one reading of the
    map when there is -- however many cities are behind. And it matters that
    the tick writes rather than a reader: what was measured while the printer
    stood is what a city keeps when the printer is carried away (D-307), and
    leaving that to whoever happened to look first made a city's rates depend
    on whether anybody had.
    """

    #: City by city, and each answered with one question: **is anything of
    #: this city measured to the wrong centre, or not measured at all**. Both
    #: halves matter and neither covers the other. A plot the growing rule
    #: refuses (a node that is not city land, an anchor with no distance of its
    #: own) stays NULL behind a measured centre, and every read of it would
    #: walk the graph again for ever. And a printer put down in a new place
    #: leaves every number in the city measured to the old one -- non-NULL,
    #: every one, so a question about NULLs alone would call the city done and
    #: leave it counting from a machine that is no longer there (D-307).
    cities = (await session.execute(select(City))).scalars().all()
    graph: Graph | None = None
    measured = 0
    for city in cities:
        center = await center_of(session, city)
        #: No printer, no centre to count from, and nothing to write: what was
        #: measured while it stood is what the city keeps (D-307).
        if center is None:
            continue
        behind = await session.scalar(
            select(Node.id)
            .where(
                _owned(city),
                or_(
                    Node.center_steps.is_(None),
                    Node.center_node_id.is_distinct_from(center.id),
                ),
            )
            .limit(1)
        )
        if behind is None:
            continue
        #: The map is read once for however many cities are behind, and not at
        #: all for a world where none are.
        if graph is None:
            graph = await _graph(session)
        await measure_city(session, center, city, graph)
        measured += 1
    return measured


async def price_of(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    city: City,
    node: Node,
) -> int:
    """The plot price in minor units: city rate x decay x area.

    The rate at the centre is set by the city via the code-law `land_price`
    (TC/m2); with each node from the bioprinter the price falls by
    `land.decay_per_node` -- the same decay the land tax follows, because both
    say the same thing about the same place (D-220).
    """

    rate = town.law_number(constants, catalog, city, "land_price")
    if rate <= 0:
        raise NotForSale(key="estate-land-no-price")
    decline = 1 - constants[R.LAND_DECAY_PER_NODE] / PERCENT
    steps = await nodes_from_center(session, node, city)
    per_metre = rate * (decline**steps)
    return max(1, money(per_metre * float(node.area_m2)))


async def may_name(session: AsyncSession, body: Body, node: Node) -> bool:
    """Whether this body may name the node (D-178).

    The owner disposes of their own land, of civic land -- the authority with
    the `land` right: the same one it hands out that land with (D-089).
    Unowned land bears no name.
    """

    if node.owner_identity_id is not None:
        return node.owner_identity_id == body.identity_id
    if node.owner_city_id is None:
        return False
    city = await town.by_id(session, node.owner_city_id)
    return city is not None and await town.may(session, body.identity_id, city, Power.LAND)


#: What an owner may nail on the node as its map mark (D-238). A closed list
#: on purpose: the world's own signs -- the Forerunners, a settlement -- must
#: not be forgeable, so neither `ruins` nor `city` is offered.
EMBLEMS = frozenset(
    {
        "house",
        "field",
        "woods",
        "meadow",
        "stones",
        "workshop",
        "market",
        "warehouse",
        "food",
        "water",
        "markup",
    }
)

#: The property the mark lives under. A string value, so `look`'s boolean
#: feature derivation never picks it up.
EMBLEM_PROPERTY = "emblem"


#: The property the place's own words live under (D-238). A string value,
#: like the emblem: `look`'s boolean feature derivation never picks it up.
ABOUT_PROPERTY = "description"


async def describe(session: AsyncSession, body: Body, node: Node, text: str | None) -> Node:
    """Write the place's description, or wipe it (D-238).

    The same right and the same spot as the nameplate and the emblem: the
    owner disposes of their land, the authority with the `land` right -- of
    civic land, and the words are written in person.
    """

    if body.state is not BodyState.ALIVE:
        raise EstateError(key="estate-about-dead")
    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise EstateError(key="estate-about-on-foot")
    if not await may_name(session, body, node):
        raise NotOwner(key="estate-about-not-yours")

    words = (text or "").strip()
    if len(words) > LAND_ABOUT_LIMIT:
        raise BadName(key="estate-about-too-long", limit=LAND_ABOUT_LIMIT)

    was = node.properties.get(ABOUT_PROPERTY)
    #: Reassigned whole rather than mutated, like the emblem: the JSON column
    #: does not watch its insides.
    fresh = {key: value for key, value in node.properties.items() if key != ABOUT_PROPERTY}
    if words:
        fresh[ABOUT_PROPERTY] = words
    node.properties = fresh
    await session.flush()
    await events.record(
        session,
        EventKind.LAND_DESCRIBED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        was=was,
        now=words or None,
    )
    return node


def public_about(node: Node) -> str | None:
    """The place's description as `look` serves it: a string or nothing.

    The belt matters less than the emblem's -- the value is free text by
    design -- but a non-string planted past the command must not reach a
    client expecting words.
    """

    value = (node.properties or {}).get(ABOUT_PROPERTY)
    return value if isinstance(value, str) and value else None


def public_emblem(node: Node) -> str | None:
    """The node's mark as the public map serves it (D-238).

    Belted to the allowlist on the way out too: `emblem()` is the only writer
    today, but a value planted by a seed, an admin edit or future code must
    not reach the unauthenticated internet verbatim.
    """

    value = (node.properties or {}).get(EMBLEM_PROPERTY)
    return value if isinstance(value, str) and value in EMBLEMS else None


async def emblem(session: AsyncSession, body: Body, node: Node, mark: str | None) -> Node:
    """Nail a map mark on the node, or take it down (D-238).

    The same right and the same spot as the nameplate: the owner disposes of
    their land, the authority with the `land` right -- of civic land, and the
    mark is nailed in person.
    """

    if body.state is not BodyState.ALIVE:
        raise EstateError(key="estate-emblem-dead")
    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise EstateError(key="estate-emblem-on-foot")
    if not await may_name(session, body, node):
        raise NotOwner(key="estate-emblem-not-yours")
    if mark is not None and mark not in EMBLEMS:
        raise BadName(key="estate-emblem-unknown")

    was = node.properties.get(EMBLEM_PROPERTY)
    #: Reassigned whole rather than mutated: the JSON column does not watch
    #: its insides, and a quiet in-place write would never reach the base.
    fresh = {key: value for key, value in node.properties.items() if key != EMBLEM_PROPERTY}
    if mark is not None:
        fresh[EMBLEM_PROPERTY] = mark
    node.properties = fresh
    await session.flush()
    await events.record(
        session,
        EventKind.LAND_MARKED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        was=was,
        now=mark,
    )
    return node


async def rename(session: AsyncSession, body: Body, node: Node, name: str) -> Node:
    """Name a plot. The nameplate is nailed on the spot, not from the Net (D-178).

    The label changes, not the node key: `terra.capital.lot2` is referenced by
    deeds, edges and events, and renaming may not break them.
    """

    if body.state is not BodyState.ALIVE:
        raise EstateError(key="estate-rename-dead")
    await travel.require_here(session, body)
    if body.node_id != node.id:
        raise EstateError(key="estate-rename-on-foot")
    if not await may_name(session, body, node):
        raise NotOwner(key="estate-rename-not-yours")

    title = name.strip()
    if not title:
        raise BadName(key="estate-rename-no-name")
    if len(title) > LAND_NAME_LIMIT:
        raise BadName(key="estate-rename-too-long", limit=LAND_NAME_LIMIT)

    before, node.name = node.name, title
    await session.flush()
    await events.record(
        session,
        EventKind.LAND_RENAMED,
        actor_identity_id=body.identity_id,
        node_id=node.id,
        was=before,
        now=title,
    )
    return node
