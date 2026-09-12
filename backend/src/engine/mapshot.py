# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The rows of the map, and the daily snapshot of the public ones (D-319 item 7).

One shape for a node on the wire, whoever asks: the personal map of
`/public/map` with a token, and the delayed public map without one. The rows
are built here so the two cannot drift apart, and the snapshot stores them
as they are -- the route serves a snapshot without rebuilding a thing.

The snapshot is what the anonymous reader gets: the public part of every
planet's surface as it was `map.public_delay_days` ago. Every node is in it
-- nodes do not hide (D-319) -- and everything about them is old: which
ways are paved, which node is a port, what stands where. The daily tick
writes one and prunes the ones older than the one being served.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, NamedTuple

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src import globe
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import biome, climate, estate, facet, memory, places, sheet, sight, travel, world
from src.engine import ship as vessels
from src.models.city import City
from src.models.identity import Body
from src.models.ship import Ship
from src.models.snapshot import MapSnapshot
from src.models.world import Edge, Layer, Node


class CityMark(NamedTuple):
    """What a city's row carries about the city: its name and its centre --
    and the city's own id, so the rows of the land it owns can name the node
    it stands on (`territory_key`) off the same reading."""

    name: str
    core: str | None
    city_id: uuid.UUID


def node_row(
    node: Node,
    *,
    parent_key: str | None,
    port: bool,
    flight: dict[str, Any] | None = None,
    faded: bool = False,
    moored: bool = False,
    drawn: int | None = None,
    reach: tuple[float, float] | None = None,
    mark: CityMark | None = None,
    territory: str | None = None,
) -> dict[str, Any]:
    """A node as the map draws it (D-045, D-097, D-237, D-238)."""
    row: dict[str, Any] = {
        "key": node.key,
        "name": node.name,
        #: Layers are a display abstraction: the world stays one graph, and
        #: the parent hierarchy groups nodes by layer.
        "layer": node.layer.value,
        "parent": parent_key,
        "port": port,
        #: The space layer paints by planet and lays nodes out by orbit.
        "planet": node.planet.value,
        #: Where the node stands, once and for everybody (D-237). Empty on the
        #: space layer -- there a place is a function of time.
        "place": places.wire(node),
        "orbit": world.orbit_of(node),
        "deferred": bool((node.properties or {}).get(world.DEFERRED)),
        #: A ship is a group of ordinary nodes (D-201), and only this mark
        #: tells them from ground: one boards a hull by the gangway.
        "aboard": vessels.is_aboard(node),
        "flight": flight,
        #: Place signs: the map draws the node's glyph by them (D-238), and a
        #: found node -- which has no name (D-321) -- is called by them in the
        #: column as well. An allowlist on purpose: this answers the whole
        #: internet. The biome rides with them because it is the one sign kept
        #: as a word rather than a flag, exactly as `look` sends it; the
        #: relief is public from the world's birth (D-319 item 3), so it hides
        #: from nobody.
        "features": world.public_signs(node) + biome.signs(node),
        #: The face the node's ground wears (landscape plan, wave 7): an id
        #: named through renames, like the province beside it.
        **({"facet": face} if (face := facet.of_node_id(node)) else {}),
        #: The province the node lies in (landscape plan, wave 3): an id the
        #: client names through renames; nothing sent tells it otherwise
        #: (D-225). Absent on a node without one.
        **({"province": province} if (province := biome.province_of(node)) else {}),
        #: The owner's mark, if one is nailed on (D-238).
        "emblem": estate.public_emblem(node),
        #: The land under the node, square metres: a city's outline is the
        #: land of its nodes joined (D-323 addendum), and the client cannot
        #: know a node's land otherwise (D-225). None off the ground.
        "area": float(node.area_m2) if node.layer is Layer.PLANET else None,
    }
    #: Memory and the public are drawn dark (D-319 item 6); sent only when so,
    #: so the bright majority of rows carry nothing for it.
    if faded:
        row["faded"] = True
    #: A ship lies at this pier (D-319 item 10): the hull is not a point of
    #: the map, the port wears the mark. The client cannot tell a pier from
    #: the parking off the hull's row -- both hang under the planet -- so
    #: the port says so itself (D-225).
    if moored:
        row["moored"] = True
    #: Known from a map in the hands and from nothing else (D-319 item 6):
    #: the day it was drawn is the map's own mark of "old".
    if drawn is not None:
        row["drawn"] = drawn
    #: How near and how far one may scout from here (D-321 item 4): the
    #: biome's reach, sent with the node the body stands in alone -- the
    #: client draws the scout's field by it and cannot read the biome (D-225).
    if reach is not None:
        row["reach"] = {"min": reach[0], "max": reach[1]}
    #: What a city puts on its own row, and only its own: that is the row the
    #: map draws when the city is one point.
    if mark is not None:
        #: The city's name. The row is the bioprinter's node (D-330), and
        #: standing in it one reads its own name: «Ядро: Принтер Предтеч», not
        #: «Столица Терры». So from afar the map needs the other one, and it
        #: cannot work it out (D-225) -- a city's name is copied at founding
        #: and never follows the node.
        row["city"] = mark.name
        #: The node the city grew from -- its bioprinter (D-319: from afar a
        #: city is the printer's point). Sent only when it is **another**
        #: node: since D-330 a city stands on its own printer, so normally
        #: this is the row's own key and saying so would be a key the client
        #: reads off the key beside it (D-225). The two part where the
        #: machine the city grew from is gone and another is the oldest left
        #: (D-312) -- and then the mark moves onto that one, because that is
        #: where a newcomer comes out. Which node holds the machine is not on
        #: the map at all, and "the oldest printer that is not the prison's"
        #: is the engine's own reading of a centre (`city.lookup.core`); the
        #: client already draws a row without the key where it stands
        #: (`model.drawnAt`).
        if mark.core is not None and mark.core != node.key:
            row["core"] = mark.core
    #: The city on whose land the node stands, by the key of the city's own
    #: node (D-332) -- sent only where the client could not tell (D-225): a
    #: plot hanging under its city is the city's by its `parent` already; a
    #: find taken in by a highway still hangs under the planet, and its city
    #: is on no row but this. The outline of the city is drawn round it.
    if territory is not None and territory != parent_key:
        row["territory"] = territory
    return row


def homes_of(marks: dict[uuid.UUID, CityMark]) -> dict[uuid.UUID, uuid.UUID]:
    """Each city's id to the node it stands on, off the marks already read."""
    return {mark.city_id: node_id for node_id, mark in marks.items()}


def territory_key(
    node: Node, homes: dict[uuid.UUID, uuid.UUID], by_key: dict[uuid.UUID, str]
) -> str | None:
    """The key of the node the owning city stands on, for a node a city owns.

    Nothing for the city's own node: the delegate owns itself from founding
    (`founding.establish`, the seed), and a row naming its own key as its
    territory would say what the key beside it says (D-225) -- and the
    client, taking the word, would count the city's node twice.
    """
    if node.owner_city_id is None:
        return None
    home = homes.get(node.owner_city_id)
    return None if home is None or home == node.id else by_key.get(home)


async def city_marks(session: AsyncSession) -> dict[uuid.UUID, CityMark]:
    """Each city's node id to its name and the key of the node it grew from.

    One reading for both maps, the public and the personal: two would part,
    and then a city would stand in one place for a newcomer and in another
    for whoever lives there.
    """
    from src.engine.city import lookup  # noqa: PLC0415 -- lazy: city -> ... -> mapshot

    out: dict[uuid.UUID, CityMark] = {}
    for city in (await session.execute(select(City))).scalars():
        core = await lookup.core(session, city)
        out[city.node_id] = CityMark(city.name, None if core is None else core.key, city.id)
    return out


async def moored_at(session: AsyncSession) -> set[uuid.UUID]:
    """The surface nodes with a ship docked at them: piers, not parkings."""
    rows = await session.execute(
        select(Ship.docked_node_id)
        .join(Node, Node.id == Ship.docked_node_id)
        .where(Node.layer == Layer.PLANET)
    )
    return {node_id for node_id in rows.scalars() if node_id is not None}


def edge_row(constants: Constants, edge: Edge, by_key: dict[uuid.UUID, str]) -> dict[str, Any]:
    return {
        "a": by_key[edge.node_a_id],
        "b": by_key[edge.node_b_id],
        "surface": edge.surface.value,
        "seconds": round(travel.edge_seconds(constants, edge)),
    }


def stub_rows(
    edges: list[Edge], shown: dict[uuid.UUID, Node], hidden: dict[uuid.UUID, Node]
) -> list[dict[str, Any]]:
    """The edges that lead out of sight, as stubs into the fog (D-319 item 6).

    An edge with one end in sight and the other on the surface beyond it is
    drawn from the seen end a little way towards the unseen one, and no
    farther: the row carries the way to set out, to the degree, and not how
    far the way goes. A stub has a direction by nature (D-319 item 4), and
    two of them towards one hidden node cross where it stands; what the fog
    keeps is the distance, and the row carries none.
    """
    rows: list[dict[str, Any]] = []
    for edge in edges:
        if edge.node_a_id in shown and edge.node_b_id in hidden:
            seen, unseen = shown[edge.node_a_id], hidden[edge.node_b_id]
        elif edge.node_b_id in shown and edge.node_a_id in hidden:
            seen, unseen = shown[edge.node_b_id], hidden[edge.node_a_id]
        else:
            continue
        here, there = places.geo_of(seen), places.geo_of(unseen)
        if here is None or there is None:
            continue
        rows.append(
            {
                "from": seen.key,
                "bearing": round(globe.bearing(here, there)),
                "surface": edge.surface.value,
            }
        )
    return rows


def passage_row(under_way: dict[str, Any] | None, by_key: dict[Any, str]) -> dict[str, str] | None:
    """A ship's passage for the map: the port it is due at and the two moments.

    The destination is a node key rather than a planet: the client climbs the
    parent hierarchy to whatever layer it is drawing.
    """
    if under_way is None:
        return None
    #: A drifter (D-289) is bound nowhere: its line is the coast ahead.
    goal = None if under_way.get("to") is None else by_key.get(under_way["to"])
    if goal is None and under_way.get("to") is not None:  # pragma: no cover
        return None
    return {
        "to": goal,
        "started_at": under_way["started_at"].isoformat(),
        "arrives_at": under_way["arrives_at"].isoformat(),
    }


async def anonymous(
    session: AsyncSession, constants: Constants, now: datetime
) -> tuple[dict[str, Any], MapSnapshot | None]:
    """The map for nobody's body: the sky as it is, the surface as it was.

    The sky is live -- a planet's place is arithmetic, a hull under way is a
    passage anybody may plan around -- and the surface is the daily snapshot
    old enough to be fair (D-319 item 7). Returns the snapshot served too, so
    the route can name it in an `ETag`.
    """
    every, _ = await sight.read(session)
    heaven = [node for node in every if node.layer is Layer.SPACE]
    by_key = {node.id: node.key for node in every}
    under_way = await vessels.passages(session)
    old = await served(session, constants, now)
    surface = old.data if old is not None else {"nodes": [], "edges": []}
    return {
        "nodes": [
            node_row(
                node,
                parent_key=by_key.get(node.parent_id),
                port=False,
                flight=passage_row(under_way.get(node.id), by_key),
            )
            for node in heaven
        ]
        + list(surface["nodes"]),
        "edges": list(surface["edges"]),
        #: The public surface is whole: no edge of it leads out of sight.
        "stubs": [],
        "routes": await vessels.corridors(session, constants, at=now),
    }, old


async def personal(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    asker: Body,
    now: datetime,
) -> dict[str, Any]:
    """The map as the asker's body sees it and their identity remembers it."""
    standing = await session.get(Node, asker.node_id)
    every, all_edges = await sight.read(session)
    by_key = {node.id: node.key for node in every}
    under_way = await vessels.passages(session)
    ports = {node.id for node in await vessels.ports(session)}
    piers = await moored_at(session)
    cities = set((await session.execute(select(City.node_id))).scalars())
    #: A ship's rooms are **not** public (D-201): from outside a ship is one
    #: hull. The interior comes with `look`, to whoever stands in it.
    inside = {
        node.id for node in every if vessels.is_aboard(node) and node.layer is not Layer.SPACE
    }
    #: Memory, and the maps in the hands: a sheet shows its places for as
    #: long as it is carried, in the tone of memory, marked with its day.
    remembered = await memory.known(session, asker.identity_id)
    carried = await sheet.held(session, asker)
    epoch = await world.epoch(session)

    def drawn_day(node: Node) -> int | None:
        """The day a place is known from a map alone -- not in sight, not
        remembered, not public -- in the calendar of the node's own planet,
        counted as the clock counts, from one."""
        moment = carried.get(node.key)
        if moment is None or node.key in remembered or node.id in view.public:
            return None
        return climate.day_index(constants, node.planet, epoch, moment) + 1

    view = sight.around(
        standing,
        constants=constants,
        nodes=every,
        edges=all_edges,
        known=remembered | set(carried),
        cities=cities,
    )
    nodes = [node for node in every if node.id in view.seen and node.id not in inside]
    shown = {node.id for node in nodes}
    here_biome = biome.of_node(constants, standing) if standing is not None else None
    #: The very band the aim refuses by (`explore.aim`), the facet's multiplier
    #: and all: a ring drawn wider than the aim allows is a promise broken.
    reach = (
        facet.reach_m(constants, here_biome, facet.of_node(constants, catalog, standing))
        if here_biome and standing is not None
        else None
    )
    #: The surface beyond sight: what a stub points at. Insides are not
    #: hidden by the fog, they are simply not the map's (D-201, item 9).
    beyond = {node.id: node for node in _public_surface(every) if node.id not in shown}
    marks = await city_marks(session)
    homes = homes_of(marks)
    return {
        "nodes": [
            node_row(
                node,
                parent_key=by_key.get(node.parent_id),
                port=node.id in ports,
                flight=passage_row(under_way.get(node.id), by_key),
                faded=node.id in view.faded,
                moored=node.id in piers,
                drawn=drawn_day(node) if node.id in view.faded else None,
                reach=reach if standing is not None and node.id == standing.id else None,
                mark=marks.get(node.id),
                territory=territory_key(node, homes, by_key),
            )
            for node in nodes
        ],
        "edges": [
            edge_row(constants, edge, by_key)
            for edge in all_edges
            if edge.node_a_id in shown and edge.node_b_id in shown
        ],
        "stubs": stub_rows(all_edges, {node.id: node for node in nodes}, beyond),
        "routes": await vessels.corridors(session, constants, at=now),
    }


def _public_surface(nodes: list[Node]) -> list[Node]:
    """What the snapshot carries: the surfaces, without the insides.

    The rooms of a hull are not public (D-201), and the floors and rooms of
    the land are the inside window's, not the map's (D-319 item 9).
    """
    return [node for node in nodes if node.layer is Layer.PLANET and not vessels.is_aboard(node)]


async def take(session: AsyncSession, constants: Constants, now: datetime) -> MapSnapshot:
    """Write the public map as it is now; the route will serve it when it is old enough."""
    every, all_edges = await sight.read(session)
    nodes = _public_surface(every)
    shown = {node.id for node in nodes}
    #: Keys of the whole graph, so a city's parent is its planet's sphere here
    #: as on the personal map -- the client climbs parents to the space layer.
    by_key = {node.id: node.key for node in every}
    ports = {node.id for node in await vessels.ports(session)}
    piers = await moored_at(session)
    marks = await city_marks(session)
    homes = homes_of(marks)
    rows = [
        node_row(
            node,
            parent_key=by_key.get(node.parent_id),
            port=node.id in ports,
            moored=node.id in piers,
            mark=marks.get(node.id),
            territory=territory_key(node, homes, by_key),
        )
        for node in nodes
    ]
    edges = [
        edge_row(constants, edge, by_key)
        for edge in all_edges
        if edge.node_a_id in shown and edge.node_b_id in shown
    ]
    snapshot = MapSnapshot(taken_at=now, data={"nodes": rows, "edges": edges})
    session.add(snapshot)
    await session.flush()
    return snapshot


def _served_before(constants: Constants, now: datetime) -> datetime:
    return now - timedelta(days=float(constants[R.MAP_PUBLIC_DELAY_DAYS]))


async def served(session: AsyncSession, constants: Constants, now: datetime) -> MapSnapshot | None:
    """The newest snapshot that is at least `map.public_delay_days` old."""
    return await session.scalar(
        select(MapSnapshot)
        .where(MapSnapshot.taken_at <= _served_before(constants, now))
        .order_by(MapSnapshot.taken_at.desc())
        .limit(1)
    )


async def prune(session: AsyncSession, constants: Constants, now: datetime) -> int:
    """Drop every snapshot older than the one being served: nothing reads them."""
    current = await served(session, constants, now)
    if current is None:
        return 0
    gone = await session.execute(delete(MapSnapshot).where(MapSnapshot.taken_at < current.taken_at))
    return int(gone.rowcount or 0)
