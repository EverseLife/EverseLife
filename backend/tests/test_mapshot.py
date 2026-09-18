# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The public map is a snapshot with a delay (D-319 п. 7).

The daily tick writes the public surface as rows; the route serves the
newest snapshot at least `map.public_delay_days` old and nothing younger;
the tick prunes what is older than the one served. The rows are the map's
own rows, so a snapshot and a live map cannot drift apart.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ship_kit import _laid, _port, _shipwright
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import biome, facet, mapshot, memory, travel, world
from src.models.identity import Body
from src.models.snapshot import MapSnapshot
from src.models.world import ABOARD, Layer, Node, Planet, Surface


async def _world(session: AsyncSession) -> tuple[Node, Node, Node]:
    terra = await world.create_node(
        session, "terra", "Terra", area_m2=1, planet=Planet.TERRA, layer=Layer.SPACE
    )
    city = await world.create_node(session, "terra.city", "City", area_m2=1, parent=terra)
    plot = await world.create_node(session, "terra.city.plot", "Plot", area_m2=100, parent=city)
    await travel.connect(session, city, plot, base_seconds=20, surface=Surface.PAVED)
    hull = await world.create_node(
        session, "ship.x", "Hull", area_m2=1, planet=Planet.TERRA, layer=Layer.SPACE
    )
    await world.create_node(
        session,
        "ship.x.room",
        "Room",
        area_m2=40,
        layer=Layer.LOCATION,
        parent=hull,
        properties={ABOARD: True},
    )
    return terra, city, plot


async def test_a_city_names_the_node_it_grew_from(
    session: AsyncSession, constants: Constants, catalog
) -> None:
    """From afar a city is its bioprinter's point (D-319), and only the server can say which.

    The map does not know where the machines stand, and the client cannot work
    the core out from what it is given (D-225): "the oldest printer, the
    prison's aside" is knowledge of its own
    чтение движка (`city.lookup.core`). Поэтому ключ едет на строке города —
    той самой, которую карта рисует, когда город свёрнут.
    """
    from estate_kit import _city

    city, core, _near, _far = await _city(session, catalog)
    taken = await mapshot.take(session, constants, datetime.now(UTC))
    rows = {row["key"]: row for row in taken.data["nodes"]}
    delegate = await session.get(Node, city.node_id)
    assert delegate is not None
    assert rows[delegate.key]["core"] == core.key, "a city stands on its own printer"
    #: And a city alone: an ordinary node has no use for the field, and a
    #: spare key in the answer is what D-225 forbids.
    assert "core" not in rows[core.key]
    #: The city's name travels on the same row, and only there: the row is a
    #: node with a name of its own (D-330), and from afar the map writes the
    #: city's instead -- which the client cannot work out, `City.name` being
    #: copied at founding and never following the node.
    assert rows[delegate.key]["city"] == city.name
    assert "city" not in rows[core.key]


async def test_a_city_on_its_own_printer_says_its_name_and_not_its_centre(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A city founded where a printer stands is its own centre (D-330), and
    saying so would be a key read off the key beside it (D-225)."""
    from src.engine.city import founding

    planet = await world.create_node(session, "terra.sky", "Терра", area_m2=1, layer=Layer.SPACE)
    core = await world.create_node(
        session,
        "terra.town",
        "Ядро",
        area_m2=100,
        parent=planet,
    )
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, world.BIOPRINTER, quality=60, origin="тест")
    await founding.found(session, catalog, core, "Новоград")
    await session.flush()

    taken = await mapshot.take(session, constants, datetime.now(UTC))
    row = {one["key"]: one for one in taken.data["nodes"]}[core.key]
    assert row["name"] == "Ядро", "вблизи узел зовётся своим именем"
    assert row["city"] == "Новоград", "издали — именем города"
    assert "core" not in row, "ядро города — сама эта строка, и говорить это незачем"


async def test_the_snapshot_carries_the_surface_and_not_the_insides(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    terra, city, plot = await _world(session)
    taken = await mapshot.take(session, constants, datetime.now(UTC))
    keys = {row["key"] for row in taken.data["nodes"]}
    assert {"terra.city", "terra.city.plot"} <= keys
    assert "terra" not in keys, "небо в снимке не лежит: оно арифметика и всегда живое"
    assert "ship.x.room" not in keys, "борт не публичен (D-201)"
    plot_row = next(row for row in taken.data["nodes"] if row["key"] == "terra.city.plot")
    assert plot_row["parent"] == "terra.city" and "faded" not in plot_row
    way = taken.data["edges"]
    assert len(way) == 1 and (way[0]["a"], way[0]["b"], way[0]["surface"]) == (
        "terra.city",
        "terra.city.plot",
        "paved",
    )
    assert taken.data["edges"][0]["seconds"] > 0


async def test_the_route_serves_only_a_snapshot_old_enough_and_the_tick_prunes(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    await _world(session)
    delay = timedelta(days=float(constants[R.MAP_PUBLIC_DELAY_DAYS]))
    now = datetime.now(UTC)
    old = await mapshot.take(session, constants, now - delay * 3)
    older = await mapshot.take(session, constants, now - delay * 2)
    fresh = await mapshot.take(session, constants, now - delay / 2)
    served = await mapshot.served(session, constants, now)
    assert served is not None and served.id == older.id, "самый свежий из достаточно старых"
    assert served.id != fresh.id, "свежее задержки не отдаётся"
    assert await mapshot.served(session, constants, now - delay * 4) is None
    #: The tick prunes what is older than the one served, and keeps the rest.
    assert await mapshot.prune(session, constants, now) == 1
    left = set((await session.execute(select(MapSnapshot.id))).scalars())
    assert left == {older.id, fresh.id} and old.id not in left
    assert await session.scalar(select(func.count()).select_from(MapSnapshot)) == 2


async def test_the_anonymous_map_is_the_sky_now_and_the_surface_then(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Without a body: the sky live, the surface from the served snapshot, no tones."""
    terra, city, plot = await _world(session)
    delay = timedelta(days=float(constants[R.MAP_PUBLIC_DELAY_DAYS]))
    now = datetime.now(UTC)
    answer, old = await mapshot.anonymous(session, constants, now)
    assert old is None
    assert {row["key"] for row in answer["nodes"]} == {"terra", "ship.x"}, (
        "без снимка — только небо"
    )
    taken = await mapshot.take(session, constants, now - delay * 2)
    late = await world.create_node(session, "terra.city.late", "Late", area_m2=100, parent=city)
    answer, old = await mapshot.anonymous(session, constants, now)
    assert old is not None and old.id == taken.id
    keys = {row["key"] for row in answer["nodes"]}
    assert {"terra", "terra.city", "terra.city.plot"} <= keys
    assert late.key not in keys, "то, что появилось после снимка, аноним не видит"
    assert all("faded" not in row for row in answer["nodes"])
    city_row = next(row for row in answer["nodes"] if row["key"] == "terra.city")
    assert city_row["parent"] == "terra", "родитель города — его планета, как на личной карте"
    assert answer["edges"] and "routes" in answer
    #: The origin of the sky's count, which nothing else gives a reader
    #: without a body: the globe before the world runs its time from it.
    origin = await world.epoch(session)
    assert origin is not None and answer["epoch"] == origin.isoformat()


async def test_the_personal_map_is_the_askers_sight_and_memory(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    terra, city, plot = await _world(session)
    identity = await world.create_identity(session, "Asker")
    body = await world.print_body(session, identity, plot)
    await memory.remember(session, constants, identity.id, ["terra.city"], at=datetime.now(UTC))
    answer = await mapshot.personal(session, constants, catalog, body, datetime.now(UTC))
    keys = {row["key"]: row for row in answer["nodes"]}
    assert "terra.city.plot" in keys and "terra.city" in keys and "terra" in keys
    assert "ship.x.room" not in keys, "борт не публичен (D-201)"
    assert "faded" not in keys["terra.city.plot"], "где стоишь — ярко"
    assert isinstance(body, Body)
    assert "epoch" not in answer, "у тела начало счёта есть в look.clock (D-225)"


async def test_a_ship_at_the_pier_marks_the_port_and_a_parking_marks_nothing(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The hull is not a point of the map (D-319 item 10): the port it lies at
    says so, and the client cannot tell that off the hull's own row."""
    port = await _port(session)
    _, body = await _shipwright(session, port)
    await _laid(session, constants, body, port)
    answer = await mapshot.personal(session, constants, catalog, body, datetime.now(UTC))
    rows = {row["key"]: row for row in answer["nodes"]}
    assert rows[port.key].get("moored") is True
    assert all("moored" not in row for key, row in rows.items() if key != port.key)
    #: The public snapshot carries the mark too: a pier with a ship at it is
    #: what stands where, and it is old like the rest.
    snapshot = await mapshot.take(session, constants, datetime.now(UTC))
    assert {row["key"] for row in snapshot.data["nodes"] if row.get("moored")} == {port.key}


async def test_the_map_tells_the_reach_of_the_node_one_stands_in(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The scout's field is drawn by the very band the aim judges a find by
    (`facet.band_m`: the biome's reach, the face's multiplier and the land
    underfoot, D-321 item 4 and the addendum of 2026-09-18), sent with the
    node under the body alone (D-225)."""
    from src.engine import mapshot
    from src.seed import seed

    core = await seed(session)
    identity = await world.create_identity(session, "Разведчица")
    body = await world.print_body(session, identity, core)
    got = await mapshot.personal(session, constants, catalog, body, datetime.now(UTC))
    rows = {row["key"]: row for row in got["nodes"]}
    here = rows[core.key]
    assert 0 < here["reach"]["min"] < here["reach"]["max"]
    #: The ring and the judge are one band: a ring drawn by another rule is
    #: green the aim refuses, or red it would allow.
    band = facet.band_m(constants, catalog, core, biome.of_node(constants, core) or "")
    assert (here["reach"]["min"], here["reach"]["max"]) == pytest.approx(band)
    assert all("reach" not in row for key, row in rows.items() if key != core.key)


async def test_the_map_names_a_find_by_its_biome_the_way_the_world_does(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A found node has no name (D-321) and wears its kind instead.

    The kind is the biome, and the biome is the one place sign kept as a word
    rather than a flag -- so it falls out of the flag allowlist and has to be
    sent by hand. It was not, and the column called the node «Безымянный
    узел» while the world's own refusals called it «Ледяное поле»: one node,
    two words. The row carries the sign now, and the client reads the word off
    `biome.names` -- the same table `biome.word_of` reads.
    """
    terra, city, plot = await _world(session)
    find = await world.create_node(
        session,
        "terra.cell.1.1",
        "",
        area_m2=100,
        planet=Planet.TERRA,
        parent=terra,
        properties={"biome": "forest", "map": {"lat": 41.0, "lon": 24.0}},
    )

    row = mapshot.node_row(find, parent_key=terra.key, port=False)
    assert row["name"] == "", "имени у находки нет и не появляется"
    assert "forest" in row["features"], "а вид её на проводе есть"
    #: And that is the word the world says about the very same node.
    assert biome.word_of(constants, find) == constants[R.BIOME_NAMES]["forest"]
