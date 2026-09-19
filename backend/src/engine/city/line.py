# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""city: the land within the line (D-356).

Whose land a node is used to be read off the node alone -- a title the city
wrote on it (`owner_city_id`), or the city's node as its parent -- while the
map drew the city as a blot round its nodes (the D-323 addendum, D-332). The
two parted: a find standing inside the blot was nobody's by the engine, and
the window over it said "land beyond the city". Since D-356 the line decides:
everything inside a city's outline is the city's.

**What draws the line is the frame, not everything it covers.** The frame is
the city's own node, whatever hangs on it (its locations and the plots of its
rings), the finds its highways took (D-332), and any covered plot somebody
holds. A covered find nobody holds lies within the line and does not widen
it: were it to, the city would creep outward by finds alone, one after
another, without a highway ever being paved -- and through the median gap the
field is scaled by it could shrink somewhere else as well. A held plot is part
of the frame, so land with paper on it can never end up outside the line.

**When the line is asked.** Whenever a frame changes: a city is founded, a
highway takes a find (`land.annex_by_way`), a covered plot is bought,
allotted or ceded, a scout joins two nodes of one frame by a way
(`cover_way`); when a scout finds a node, of the lines whose raster reaches
it (`cover_near`); at the deploy; and every world tick, for every city, so
that no way of changing a frame, today's or a later one, leaves the land and
the picture apart for longer than a tick. The line itself is remembered by
what it is drawn from (`outline.outline_of`): asked every minute, it is
drawn again only when a frame has changed.

**What the line lets go.** Only empty land (`_settled_line`): a covered plot
with a house, a machine, a vein, a bed or a begun build on it stays the
city's when the line draws back, and joins the frame. **Two lines over one
node** -- the elder city's, asked first (`_elder_first`).

**It never waits for a node.** Every node it takes or lets go is locked with
`FOR NO KEY UPDATE SKIP LOCKED`, in id order, and judged again under the lock:
whoever holds a node right now -- a purchase, a highway finishing, another
city's line -- is simply passed over, and the next tick finds it free. What it
has locked it keeps until the commit, and that is why every caller asks it
last: nothing after it in the same transaction waits on a lock, so nothing
can wait on the rows it took while holding what the line waits for.

One wait is left, and it is the city's own row: a node taken is written the
city's id, and the foreign key checks the city under `KEY SHARE`, which
waits for a transaction holding that city `FOR UPDATE` -- a machine put up,
a loan, an emission, a credit. None of those waits on a node the line has
taken while it holds the city (a machine is put up node first), so the wait
ends when that transaction does.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src import globe, outline
from src.constants import Constants
from src.engine import estate, events, facet, places, ruins
from src.engine.city.lookup import by_id, by_node
from src.models.city import City
from src.models.event import EventKind
from src.models.world import (
    ABOARD,
    COVERED,
    PLOT,
    Edge,
    Layer,
    Node,
    Planet,
    only_covered,
)


def _frame(city: City):
    """The frame of a city in SQL: see `frame_of`."""
    return and_(
        Node.layer == Layer.PLANET,
        or_(
            Node.id == city.node_id,
            Node.parent_id == city.node_id,
            and_(
                Node.owner_city_id == city.id,
                or_(~Node.properties.has_key(COVERED), Node.owner_identity_id.is_not(None)),
            ),
        ),
    )


async def frame_of(session: AsyncSession, city: City) -> tuple[list[Node], list[tuple[str, str]]]:
    """The nodes that draw the city's line, and the ways between them.

    Exactly the rows the map groups into the city (`cityOutlines`: by the
    row's `territory`, else its `parent`): the city's node, the surface nodes
    hanging on it, and the land it holds by title -- all but a covered plot
    nobody holds, whose row carries no `territory` (`mapshot.territory_key`).
    A hull's rooms are not the map's (D-201), and a node without a place on
    the sphere is not drawn.
    """
    rows = (await session.execute(select(Node).where(_frame(city)))).scalars().all()
    frame = [
        node
        for node in rows
        if not (node.properties or {}).get(ABOARD) and places.geo_of(node) is not None
    ]
    keys = {node.id: node.key for node in frame}
    if not keys:
        return [], []
    ways = (
        (
            await session.execute(
                select(Edge).where(Edge.node_a_id.in_(keys), Edge.node_b_id.in_(keys))
            )
        )
        .scalars()
        .all()
    )
    return frame, [(keys[way.node_a_id], keys[way.node_b_id]) for way in ways]


def line_of(
    constants: Constants,
    planet: Planet,
    frame: Sequence[Node],
    ways: Sequence[tuple[str, str]],
) -> outline.Outline | None:
    """The city's outline over its frame, as the map draws it."""
    members = []
    for node in frame:
        point = places.geo_of(node)
        if point is not None:
            members.append(outline.Member(node.key, point[0], point[1], float(node.area_m2)))
    return outline.outline_of(
        members, ways, globe.radius_m(constants, planet), outline.law_of(constants)
    )


async def _locked(session: AsyncSession, ids: Sequence[uuid.UUID]) -> list[Node]:
    """The rows nobody else holds right now, locked, read afresh, in id order."""
    if not ids:
        return []
    return list(
        (
            await session.execute(
                select(Node)
                .where(Node.id.in_(ids))
                .order_by(Node.id)
                .with_for_update(skip_locked=True, key_share=True)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


def _free(node: Node, homes: set[uuid.UUID]) -> bool:
    """Whether the node is nobody's ground a line may take (D-198, D-332).

    Nobody's by title and by parent: the node of a city and what hangs on one
    are that city's whatever their row says, and a hull is not land (D-201).
    """
    return (
        node.layer is Layer.PLANET
        and node.owner_city_id is None
        and node.owner_identity_id is None
        and node.id not in homes
        and node.parent_id not in homes
        and not (node.properties or {}).get(ABOARD)
    )


def _on_the_ground():
    """The cities that stand on a planet's surface: the only ones with a line.

    A city is a node of the ground (D-319). A world laid after D-330 and then
    deployed had one founded on its planet's sphere by the catch-up, which
    took the core's parent for the capital; read as a city, that sphere would
    hang every find of the planet under a "city" and keep them all out of
    every line, and its frame would be the whole planet.
    """
    return select(City).join(Node, Node.id == City.node_id).where(Node.layer == Layer.PLANET)


async def _homes(session: AsyncSession) -> set[uuid.UUID]:
    """The nodes the cities stand on."""
    return {city.node_id for city in (await session.execute(_on_the_ground())).scalars()}


async def _held_covered(session: AsyncSession, city: City) -> list[Node]:
    """The city's covered plots nobody holds: the land the line alone keeps."""
    return list(
        (
            await session.execute(
                select(Node).where(
                    Node.owner_city_id == city.id,
                    Node.owner_identity_id.is_(None),
                    Node.properties.has_key(COVERED),
                )
            )
        )
        .scalars()
        .all()
    )


async def of_the_forerunners(session: AsyncSession, node: Node) -> bool:
    """Whether the node is a Forerunner ruin's: its root, its pier and hall
    (marked `precursors`), or a room opened under the root (D-232).

    Within a city's line or at the end of its highway such a node is the
    city's land -- its laws hold there -- and never a plot (D-356): a ruin is
    a find of the Forerunners' and not ground to divide, so it is neither
    sold nor handed out, and it gets no gate (D-282). The rooms carry no
    mark of their own (`ruins.open_room`), so the root above answers for them.
    """
    return ruins.is_precursor(node) or await ruins.city_of(session, node) is not None


def _within(line: outline.Outline | None, node: Node) -> bool:
    point = places.geo_of(node)
    return line is not None and point is not None and line.covers(*point)


async def _settled_line(
    session: AsyncSession, constants: Constants, city: City, home: Node
) -> tuple[outline.Outline | None, list[Node]]:
    """The city's line, once the land it would leave with something on it has
    joined the frame.

    The line lets go of empty land only (D-356). A covered plot with a house,
    a machine, a vein, a bed or a begun build on it -- the city's or a
    settler's -- does not go back to the wild when the line draws back: it
    stays the city's, and so it draws the line from then on, or the picture
    would show it outside the city it belongs to. Each pass that keeps one
    widens the frame and may reach another, so the line is drawn again until
    none is left outside; a pass that could lock nothing ends it, and the
    tick takes up the rest.
    """
    while True:
        frame, ways = await frame_of(session, city)
        line = line_of(constants, home.planet, frame, ways)
        held = await _held_covered(session, city)
        stranded = [node for node in held if not _within(line, node)]
        kept = [
            node.id for node in stranded if not await estate.is_vacant(session, constants, node)
        ]
        promoted = 0
        for node in await _locked(session, kept):
            if node.owner_city_id != city.id or not only_covered(node):
                continue
            if await estate.is_vacant(session, constants, node):
                continue
            node.properties = {
                key: value for key, value in (node.properties or {}).items() if key != COVERED
            }
            promoted += 1
        if not promoted:
            return line, held
        await session.flush()


async def cover(
    session: AsyncSession,
    constants: Constants,
    city: City,
    *,
    homes: set[uuid.UUID] | None = None,
) -> tuple[int, int]:
    """Take into the city what its line covers, and let go what it no longer does.

    Returns how many nodes were taken and how many let go. Only nobody's
    ground is taken -- another city's node never moves, as with a highway
    (D-332) -- and only an empty covered plot nobody holds is let go
    (`_settled_line`): whatever else the city holds, it holds by a title or
    by work the line did not give. `homes` are the cities' nodes, when the
    caller has them.
    """
    home = await session.get(Node, city.node_id)
    if home is None or home.layer is not Layer.PLANET:
        return 0, 0
    if homes is None:
        homes = await _homes(session)
    line, held = await _settled_line(session, constants, city, home)

    inside: list[uuid.UUID] = []
    if line is not None:
        (south, north), (west, east) = line.window()
        lat = places.degrees(places.PLACE_LAT)
        lon = places.degrees(places.PLACE_LON)
        nearby = (
            (
                await session.execute(
                    select(Node).where(
                        Node.layer == Layer.PLANET,
                        Node.planet == home.planet,
                        Node.owner_city_id.is_(None),
                        Node.owner_identity_id.is_(None),
                        lat.between(south, north),
                        lon.between(west, east),
                    )
                )
            )
            .scalars()
            .all()
        )
        inside = [node.id for node in nearby if _free(node, homes) and _within(line, node)]
    outside = [node.id for node in held if not _within(line, node)]

    taken = 0
    for node in await _locked(session, inside):
        #: Judged again under the lock: a highway or another city's line may
        #: have reached it between the look and the lock.
        if not _free(node, homes):
            continue
        node.owner_city_id = city.id
        #: A plot to sell and hand out -- unless it is a ruin's (above).
        marks = (
            {COVERED: True}
            if await of_the_forerunners(session, node)
            else {COVERED: True, PLOT: True}
        )
        node.properties = {**(node.properties or {}), **marks}
        taken += 1
        await events.record(
            session,
            EventKind.LAND_COVERED,
            node_id=node.id,
            city_id=str(city.id),
            **facet.told_of(constants, node),
            city=city.name,
        )
    let_go = 0
    for node in await _locked(session, outside):
        if node.owner_city_id != city.id or not only_covered(node):
            continue
        #: Asked again under the lock: somebody may have put something down
        #: since the line was settled, and land with work on it stays.
        if not await estate.is_vacant(session, constants, node):
            continue
        node.owner_city_id = None
        node.properties = {
            key: value
            for key, value in (node.properties or {}).items()
            if key not in (COVERED, PLOT)
        }
        #: Measured to a centre it no longer has: beyond the walls nothing is
        #: counted from a printer (`estate.note_new_place`).
        node.center_node_id = None
        node.center_steps = None
        let_go += 1
        await events.record(
            session,
            EventKind.LAND_UNCOVERED,
            node_id=node.id,
            city_id=str(city.id),
            **facet.told_of(constants, node),
            city=city.name,
        )
    if taken or let_go:
        await session.flush()
    return taken, let_go


def _elder_first(query):
    """Cities in the order their lines are asked: the elder first (D-356).

    A node goes to the first line that covers it, and what a line took does
    not move to another (`_free`). Two lines reaching one free node in the
    same pass -- the tick, a deploy, a scout's find -- are asked elder first:
    the city founded earlier takes it, and the order is the same every time,
    so it does not change hands back and forth between two neighbours'
    ticks. The id only breaks a tie of one second.
    """
    return query.order_by(City.created_at, City.id)


async def _cities(session: AsyncSession, planet: Planet | None) -> list[City]:
    query = _on_the_ground()
    if planet is not None:
        query = query.where(Node.planet == planet)
    return list((await session.execute(_elder_first(query))).scalars().all())


async def cover_all(
    session: AsyncSession, constants: Constants, *, planet: Planet | None = None
) -> tuple[int, int]:
    """Ask every city's line -- or every city of one planet's. World tick, seed.

    Returns the nodes taken and let go, all told.
    """
    homes = await _homes(session)
    taken = let_go = 0
    for city in await _cities(session, planet):
        more, fewer = await cover(session, constants, city, homes=homes)
        taken += more
        let_go += fewer
    return taken, let_go


async def cover_near(
    session: AsyncSession, constants: Constants, planet: Planet, point: globe.Geo
) -> tuple[int, int]:
    """Ask the lines that could hold this point -- a scout's find.

    A find changes no frame: it can only lie within a line or not, so only the
    cities whose raster reaches the point are asked (`Outline.window`), and
    the rest of the planet is left to the tick. Their lines are remembered by
    what they are drawn from (`outline.outline_of`), so looking is two
    queries a city and no arithmetic until a frame changes.
    """
    homes = await _homes(session)
    taken = let_go = 0
    for city in await _cities(session, planet):
        home = await session.get(Node, city.node_id)
        if home is None:  # pragma: no cover -- a city outlives nothing of its own
            continue
        frame, ways = await frame_of(session, city)
        line = line_of(constants, home.planet, frame, ways)
        if line is None:
            continue
        (south, north), (west, east) = line.window()
        if not (south <= point[0] <= north and west <= point[1] <= east):
            continue
        more, fewer = await cover(session, constants, city, homes=homes)
        taken += more
        let_go += fewer
    return taken, let_go


async def _drawing(session: AsyncSession, node: Node) -> City | None:
    """The city whose line this node draws, or None: the reading `frame_of`
    and the map row's `territory` make, asked of one node."""
    if node.layer is not Layer.PLANET or (node.properties or {}).get(ABOARD):
        return None
    own = await by_node(session, node.id)
    if own is not None:
        return own
    if node.parent_id is not None:
        above = await by_node(session, node.parent_id)
        if above is not None:
            return above
    if node.owner_city_id is not None and not only_covered(node):
        return await by_id(session, node.owner_city_id)
    return None


async def cover_way(session: AsyncSession, constants: Constants, one: Node, other: Node) -> None:
    """A way laid between two nodes: a street of the city whose line both draw
    closes that line (D-332), and the line is asked. Any other way changes no
    frame."""
    city = await _drawing(session, one)
    if city is None:
        return
    also = await _drawing(session, other)
    if also is not None and also.id == city.id:
        await cover(session, constants, city)
