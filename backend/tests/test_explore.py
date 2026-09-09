# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Exploration as the generator of the graph (D-321).

The scout aims at the globe and the landscape answers: too near, too far, into
the water, no room, across a way. A lawful aim costs the walk of its metres
over wild ground -- one way, because the scout walks there and stays (D-327).
When the run is over the cell is a node: the same node for everybody, read off
the field, joined by the one way that was walked (D-326), and by the vault's
chance the middle of a complex. What is checked here is every one of those
rules, the turning back, and the race two scouts run for one cell.
"""

from __future__ import annotations

import asyncio
import json
import math
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import VAULT_BUILD, _slow
from src import globe, seed_planets
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import (
    access,
    biome,
    explore,
    jobs,
    occupation,
    places,
    ruins,
    terrain,
    transport,
    travel,
    world,
)
from src.engine.explore import run as explore_run
from src.models.event import Event, EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Container, ContainerKind, Item
from src.models.job import Job, JobState
from src.models.world import Edge, Layer, Node, Planet, Surface, Vein
from src.units import METRES_PER_KM


def _capital() -> tuple[float, float]:
    """Where the layout pins the capital: read off the build the tests run
    on, because the field decides where land is and the pin follows it
    (landscape plan, wave 2)."""
    layout = json.loads((VAULT_BUILD / "world.json").read_text(encoding="utf-8"))
    nodes = layout["nodes"] if isinstance(layout, dict) else layout
    place = next(node["place"] for node in nodes if node.get("key") == "terra.capital")
    return float(place["lat"]), float(place["lon"])


CAPITAL = _capital()


async def _sphere(session: AsyncSession, planet: Planet = Planet.TERRA) -> Node:
    return await world.create_node(
        session, planet.value, planet.value.title(), area_m2=1, planet=planet, layer=Layer.SPACE
    )


def _pin(point: globe.Geo) -> dict:
    return {places.PLACE: {places.PLACE_LAT: point[0], places.PLACE_LON: point[1]}}


async def _camp(
    session: AsyncSession,
    constants: Constants,
    planet: Planet = Planet.TERRA,
    at: globe.Geo = CAPITAL,
) -> tuple[Node, Node, Body]:
    """A sphere, a seeded node on it and a scout standing there."""
    sphere = await _sphere(session, planet)
    point = explore.point_of(constants, planet, explore.cell_of(constants, planet, at))
    camp = await world.create_node(
        session,
        f"{planet.value}.camp.{uuid.uuid4().hex[:6]}",
        "Camp",
        planet=planet,
        area_m2=60,
        parent=sphere,
        properties=_pin(point),
    )
    identity = await world.create_identity(session, f"Scout-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, camp)
    body.stamina = constants[R.BODY_STAMINA_MAX]
    await session.flush()
    return sphere, camp, body


def _step(
    constants: Constants, planet: Planet, origin: globe.Geo, metres: float, bearing: float = 0.0
) -> globe.Geo:
    radius = globe.radius_m(constants, planet)
    return globe.offset(radius, origin, metres * math.sin(bearing), metres * math.cos(bearing))


def _reach(constants: Constants, node: Node) -> tuple[float, float]:
    here = biome.of_node(constants, node)
    assert here is not None
    return biome.reach_m(constants, here)


# --- the lattice --------------------------------------------------------------


def test_the_lattice_gives_one_cell_and_one_key_per_point(constants: Constants) -> None:
    """Two points closer than a cell fall into one cell; a cell's centre is its own."""
    step = constants[R.MAP_LATTICE_M]
    cell = explore.cell_of(constants, Planet.TERRA, CAPITAL)
    centre = explore.point_of(constants, Planet.TERRA, cell)
    assert explore.cell_of(constants, Planet.TERRA, centre) == cell
    nudged = _step(constants, Planet.TERRA, centre, step / 4, bearing=1.0)
    assert explore.cell_of(constants, Planet.TERRA, nudged) == cell
    radius = globe.radius_m(constants, Planet.TERRA)
    assert globe.distance_m(radius, centre, CAPITAL) <= step
    #: Near the pole the columns widen with the row, so a cell stays a cell.
    high = explore.cell_of(constants, Planet.TERRA, (80.0, 10.0))
    beside = explore.point_of(constants, Planet.TERRA, (high[0], high[1] + 1))
    assert globe.distance_m(
        radius, explore.point_of(constants, Planet.TERRA, high), beside
    ) == pytest.approx(step, rel=0.05)
    assert explore.key_of(Planet.TERRA, cell) == f"terra.cell.{cell[0]}.{cell[1]}"


# --- the biome ----------------------------------------------------------------


def test_water_has_no_biome_and_the_frozen_planets_have_one(constants: Constants) -> None:
    field = terrain.field_of(constants, Planet.TERRA)
    for point in (CAPITAL, (10.0, -100.0), (-30.0, 60.0)):
        if field.is_water(*point):
            assert biome.classify(constants, Planet.TERRA, *point) is None
        else:
            assert biome.classify(constants, Planet.TERRA, *point) in constants[R.BIOME_NAMES]
    assert biome.classify(constants, Planet.AURORA, 0.0, 0.0) == biome.ICE
    assert biome.classify(constants, Planet.PYROXIS, 0.0, 0.0) == biome.CINDER
    for name in constants[R.BIOME_NAMES]:
        near, far = biome.reach_m(constants, name)
        assert 0 < near < far


# --- the aim ------------------------------------------------------------------


async def test_the_landscape_refuses_too_near_too_far_and_the_water(
    session: AsyncSession, constants: Constants
) -> None:
    _, camp, _ = await _camp(session, constants)
    here = places.geo_of(camp)
    assert here is not None
    near, far = _reach(constants, camp)
    with pytest.raises(explore.TooNear):
        await explore.check(
            session, constants, camp, _step(constants, Planet.TERRA, here, near / 3)
        )
    with pytest.raises(explore.TooFar):
        await explore.check(session, constants, camp, _step(constants, Planet.TERRA, here, far * 3))
    aim = await explore.check(
        session, constants, camp, _step(constants, Planet.TERRA, here, (near + far) / 2)
    )
    assert near <= aim.metres <= far and aim.existing is None
    #: Into the sea: a camp on the land side of a shoreline aims across it.
    sphere = await session.get(Node, camp.parent_id)
    land, water = _shoreline(constants)
    shore = await world.create_node(
        session, "terra.shore", "Shore", area_m2=60, parent=sphere, properties=_pin(land)
    )
    with pytest.raises((explore.NotLand, explore.IntoWater)):
        await explore.check(session, constants, shore, water)


def _shoreline(constants: Constants) -> tuple[globe.Geo, globe.Geo]:
    """A dry point just inland of the sea's edge and a wet one a lawful step
    out: the edge is bisected along a row between a dry centre and a wet one,
    because the height is interpolated between cells.

    The step out is the middle of the **land point's own** reach band, not a
    fixed handful of metres: an aim is measured before it is looked at, so a
    wet point nearer than `biome.reach_m` is refused for being too near and
    the test never reaches the question it is asking. Fixed metres held only
    while the shoreline the scan finds first happened to sit in a biome that
    reaches that near, and D-324 moved every shoreline.
    """
    field = terrain.field_of(constants, Planet.TERRA)
    radius = globe.radius_m(constants, Planet.TERRA)
    lat_max = constants[R.MAP_CITY_LAT_MAX]
    for lat10 in range(-int(lat_max) * 10, int(lat_max) * 10, 7):
        lat = lat10 / 10
        for lon10 in range(-1800, 1800, 20):
            dry, wet = lon10 / 10, lon10 / 10 + 2.0
            if field.is_water(lat, dry) or not field.is_sea(lat, wet):
                continue
            for _ in range(40):
                mid = (dry + wet) / 2
                if field.is_sea(lat, mid):
                    wet = mid
                else:
                    dry = mid
            edge = (dry + wet) / 2
            land = globe.offset(radius, (lat, edge), -6.0, 0.0)
            if field.is_water(*land):
                continue
            here = biome.classify(constants, Planet.TERRA, *land)
            near, far = biome.reach_m(constants, here)
            water = globe.offset(radius, land, (near + far) / 2, 0.0)
            if field.is_water(*water):
                return land, water
    raise AssertionError("no shoreline on Terra")


def seed_points() -> list[globe.Geo]:
    return [(lat, lon) for lat in range(-60, 61, 10) for lon in range(-180, 180, 15)]


async def test_no_room_beside_a_node_and_no_way_across_another(
    session: AsyncSession, constants: Constants
) -> None:
    """The found node must not overlap a standing one, and the way must not cross a way."""
    sphere, camp, _ = await _camp(session, constants)
    here = places.geo_of(camp)
    assert here is not None
    near, far = _reach(constants, camp)
    taken = _step(constants, Planet.TERRA, here, far * 0.7, bearing=0.0)
    other = await world.create_node(
        session, "terra.taken", "Taken", area_m2=240, parent=sphere, properties=_pin(taken)
    )
    #: Right beside the standing node: the two circles would overlap.
    beside = _step(constants, Planet.TERRA, taken, constants[R.MAP_LATTICE_M], bearing=math.pi / 2)
    with pytest.raises(explore.NoRoom):
        await explore.check(session, constants, camp, beside)
    #: A way from the camp to a node east, then an aim north-east that would
    #: have to cross it... laid as two nodes joined across the aimed line.
    left = await world.create_node(
        session,
        "terra.left",
        "Left",
        area_m2=60,
        parent=sphere,
        properties=_pin(_step(constants, Planet.TERRA, here, far * 0.7, bearing=math.pi / 2 - 1.0)),
    )
    right = await world.create_node(
        session,
        "terra.right",
        "Right",
        area_m2=60,
        parent=sphere,
        properties=_pin(_step(constants, Planet.TERRA, here, far * 0.7, bearing=math.pi / 2 + 1.0)),
    )
    await travel.connect(session, left, right, surface=Surface.WILD)
    with pytest.raises(explore.CrossesWay):
        await explore.check(
            session,
            constants,
            camp,
            _step(constants, Planet.TERRA, here, far * 0.85, bearing=math.pi / 2),
        )
    assert other is not None


# --- the run ------------------------------------------------------------------


async def test_a_run_costs_the_walk_of_its_metres_over_wild_ground(
    session: AsyncSession, constants: Constants
) -> None:
    _, camp, scout = await _camp(session, constants)
    here = places.geo_of(camp)
    assert here is not None
    near, far = _reach(constants, camp)
    before = float(scout.stamina)
    moment = datetime.now(UTC)
    job = await explore.survey(
        session, constants, scout, _step(constants, Planet.TERRA, here, far * 0.8), now=moment
    )
    metres = globe.distance_m(
        globe.radius_m(constants, Planet.TERRA),
        here,
        explore.point_of(constants, Planet.TERRA, tuple(job.payload["cell"])),
    )
    expected = travel.walk_seconds(constants, metres) * constants[R.ROAD_WILD_MULTIPLIER]
    assert (job.run_at - moment).total_seconds() == pytest.approx(expected, abs=1)
    assert float(scout.stamina) < before, "разведка стоит выносливости, как дорога"
    doing = await occupation.current(session, scout)
    assert doing is not None and doing.kind == occupation.SURVEY
    with pytest.raises(occupation.Busy):
        await explore.survey(
            session, constants, scout, _step(constants, Planet.TERRA, here, near), now=moment
        )


async def test_the_run_reads_the_field_and_sews_the_node_on(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The cell becomes a node with the field's properties, a way to the camp,
    the cell's key, and the biome's name; the second scout of the same cell
    brings home a way, not a node."""
    async with factory() as session, session.begin():
        _, camp, scout = await _camp(session, constants)
        here = places.geo_of(camp)
        assert here is not None
        _, far = _reach(constants, camp)
        target = _step(constants, Planet.TERRA, here, far * 0.8, bearing=0.3)
        job = await explore.survey(session, constants, scout, target)
        cell = tuple(job.payload["cell"])
        term, camp_id, scout_id = job.run_at, camp.id, scout.id
    assert await jobs.run_one(factory, now=term) is not None
    async with factory() as session, session.begin():
        key = explore.key_of(Planet.TERRA, cell)
        node = await session.scalar(select(Node).where(Node.key == key))
        assert node is not None, "разведка не родила узла"
        point = places.geo_of(node)
        assert point == explore.point_of(constants, Planet.TERRA, cell)
        here = node.properties[biome.BIOME]
        assert node.name == explore.NAMELESS, "находка безымянна: её тип показывает знак"
        assert node.properties[biome.TEMPERATURE_SWING] == biome.swing_c(constants, here)
        assert (
            node.properties["temperature"] == terrain.climate_at(constants, Planet.TERRA, *point)[0]
        )
        assert node.properties["wild"] is True
        edge = await session.scalar(
            select(Edge).where(
                ((Edge.node_a_id == camp_id) & (Edge.node_b_id == node.id))
                | ((Edge.node_a_id == node.id) & (Edge.node_b_id == camp_id))
            )
        )
        assert edge is not None and edge.surface is Surface.WILD and edge.base_seconds > 0
        #: The second scout aims at the same cell from the new node's side.
        scout = await session.get(Body, scout_id)
        second_id = await world.create_identity(session, "Second")
        second = await world.print_body(session, second_id, node)
        second.stamina = constants[R.BODY_STAMINA_MAX]
        camp = await session.get(Node, camp_id)
        camp_point = places.geo_of(camp)
        await session.flush()
        again = await explore.check(session, constants, node, camp_point)
        assert again.existing is not None and again.existing.id == camp_id
        assert scout is not None
    assert await _count(factory, Node) >= 2


async def test_a_find_gets_one_way_and_it_is_the_one_walked(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """A road is somebody's labour, and so is a node (D-326).

    A find used to be sewn to every neighbour within reach -- `knit`, D-321's
    "the graph is sewn, not grown as a thread". Nobody laid those roads: the
    player spent time on a **node**, and the ways between finds came free, so
    two points scouted apart turned out joined. Now a find has one way, and it
    is the one that was walked to it.

    The test checks itself: first it makes sure a neighbour **is** within reach
    of the find -- that there would have been something to sew.
    """
    async with factory() as session, session.begin():
        _, camp, scout = await _camp(session, constants)
        here = places.geo_of(camp)
        assert here is not None
        _, far = _reach(constants, camp)
        job = await explore.survey(
            session, constants, scout, _step(constants, Planet.TERRA, here, far * 0.8, bearing=0.3)
        )
        cell, term, camp_id = tuple(job.payload["cell"]), job.run_at, camp.id
        #: The neighbour is placed on purpose and **before** the run is over:
        #: within reach of the find to be, which is exactly the case `knit`
        #: sewed. Without it the test would check nothing.
        where = explore.point_of(constants, Planet.TERRA, cell)
        neighbour = await world.create_node(
            session,
            f"terra.neighbour.{uuid.uuid4().hex[:6]}",
            "Сосед",
            planet=Planet.TERRA,
            area_m2=60,
            parent=await session.get(Node, camp.parent_id),
            properties=_pin(_step(constants, Planet.TERRA, where, far * 0.5, bearing=1.9)),
        )
        neighbour_id = neighbour.id
    assert await jobs.run_one(factory, now=term) is not None

    async with factory() as session:
        node = await session.scalar(
            select(Node).where(Node.key == explore.key_of(Planet.TERRA, cell))
        )
        assert node is not None
        point = places.geo_of(node)
        assert point is not None
        radius = globe.radius_m(constants, Planet.TERRA)
        _, reach = _reach(constants, node)
        near = [
            other
            for other in (await session.scalars(select(Node).where(Node.planet == Planet.TERRA)))
            if other.id not in (node.id, camp_id)
            and places.geo_of(other) is not None
            and globe.distance_m(radius, places.geo_of(other), point) <= reach
        ]
        assert neighbour_id in {one.id for one in near}, (
            "the neighbour stands within reach -- there was something to sew"
        )

        edges = list(
            await session.scalars(
                select(Edge).where((Edge.node_a_id == node.id) | (Edge.node_b_id == node.id))
            )
        )
        ends = {edge.node_a_id for edge in edges} | {edge.node_b_id for edge in edges}
        assert ends == {node.id, camp_id}, "one way, and it is the one walked (D-326)"


async def _count(factory: async_sessionmaker[AsyncSession], model) -> int:
    async with factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_two_scouts_do_not_lay_one_cell_twice(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Two runs aimed at one cell from two camps end in one node and two ways."""
    async with factory() as session, session.begin():
        sphere, camp, scout = await _camp(session, constants)
        here = places.geo_of(camp)
        assert here is not None
        near, far = _reach(constants, camp)
        target = _step(constants, Planet.TERRA, here, far * 0.8, bearing=0.0)
        other_point = _step(constants, Planet.TERRA, here, far * 1.6, bearing=0.0)
        other_camp = await world.create_node(
            session,
            "terra.other",
            "Other",
            area_m2=60,
            parent=sphere,
            properties=_pin(other_point),
        )
        other_identity = await world.create_identity(session, "Other scout")
        other = await world.print_body(session, other_identity, other_camp)
        other.stamina = constants[R.BODY_STAMINA_MAX]
        await session.flush()
        first = await explore.survey(session, constants, scout, target)
        second = await explore.survey(session, constants, other, target)
        assert first.payload["cell"] == second.payload["cell"]
        term = max(first.run_at, second.run_at)
        cell = tuple(first.payload["cell"])
    #: Both jobs fire in the same instant, each in its own transaction.
    ready = asyncio.Barrier(2)

    async def fire() -> None:
        await ready.wait()
        await jobs.run_one(factory, now=term)

    await asyncio.gather(fire(), fire())
    async with factory() as session:
        found = (
            (
                await session.execute(
                    select(Node).where(Node.key == explore.key_of(Planet.TERRA, cell))
                )
            )
            .scalars()
            .all()
        )
        assert len(found) == 1, "одна ячейка стала двумя узлами"
        ways = await session.scalar(
            select(func.count())
            .select_from(Edge)
            .where((Edge.node_a_id == found[0].id) | (Edge.node_b_id == found[0].id))
        )
        assert ways == 2, "второй разведчик не принёс пути к найденному"


async def test_a_run_on_the_ice_may_find_a_complex_of_the_forerunners(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """On Aurora a find is, by the vault's chance, a scheme: a mine with its
    stores stocked, or the frozen city itself with every room open."""
    laid: dict[str, int] = {}
    async with factory() as session, session.begin():
        sphere, camp, scout = await _camp(session, constants, Planet.AURORA, at=(-8.0, 112.0))
        here = places.geo_of(camp)
        assert here is not None
        near, far = _reach(constants, camp)
        #: Runs from one camp at the cells whose own roll hides a complex: the
        #: roll is the cell's (`complex_roll`), so the test may ask it first.
        chance = constants[R.COMPLEX_CHANCE]["aurora"][biome.ICE] / 100
        picked = []
        for k in range(48):
            target = _step(constants, Planet.AURORA, here, far * 0.9, bearing=math.tau * k / 48)
            cell = explore.cell_of(constants, Planet.AURORA, target)
            if explore.complex_roll(explore.key_of(Planet.AURORA, cell)) < chance:
                picked.append(target)
        assert picked, "среди сорока восьми ячеек ни одна не прячет комплекса"
        terms = []
        for k, target in enumerate(picked[:6]):
            identity = await world.create_identity(session, f"Scout {k}")
            body = await world.print_body(session, identity, camp)
            body.stamina = constants[R.BODY_STAMINA_MAX]
            await session.flush()
            try:
                job = await explore.survey(session, constants, body, target)
            except explore.ExploreError:
                continue
            terms.append(job.run_at)
        assert terms, "ни одного законного прицела на льду"
        term = max(terms)
    while await jobs.run_one(factory, now=term) is not None:
        pass
    async with factory() as session:
        parts = (
            (
                await session.execute(
                    select(Node).where(
                        Node.planet == Planet.AURORA,
                        Node.properties[explore.ROLE].astext.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for part in parts:
            role = str(part.properties[explore.ROLE])
            laid[role] = laid.get(role, 0) + 1
        cities = (
            (
                await session.execute(
                    select(Node).where(
                        Node.key.like("aurora.lost.%"),
                        Node.properties[ruins.KIND].astext.is_not(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        stores = (
            (
                await session.execute(
                    select(Node).where(Node.properties[explore.ROLE].astext == "store")
                )
            )
            .scalars()
            .all()
        )
        for store in stores:
            things = await session.scalar(
                select(func.count())
                .select_from(Item)
                .join(Container, Item.container_id == Container.id)
                .where(Container.owner_id == store.id, Container.kind == ContainerKind.NODE)
            )
            assert things, "склад комплекса пуст"
        for city in cities:
            assert ruins.exhausted(constants, city), "город Предтеч найден с закрытыми помещениями"
    assert laid or cities, (
        f"двенадцать разведок на льду и ни одного комплекса при {constants[R.COMPLEX_CHANCE]}"
    )


def test_the_mountains_are_cold_and_bear_veins_more_often(constants: Constants) -> None:
    """Reading the field: the alpine biome multiplies the vein share and the
    lapse rate lowers the temperature next to the plain."""
    assert biome.vein_k(constants, biome.ALPINE) > biome.vein_k(constants, biome.FLOODPLAIN)
    field = terrain.field_of(constants, Planet.TERRA)
    high = next(p for p in seed_points() if not field.is_water(*p) and field.is_mountain(*p))
    low = next(
        p
        for p in seed_points()
        if not field.is_water(*p) and not field.is_mountain(*p) and abs(p[0] - high[0]) < 15
    )
    assert biome.classify(constants, Planet.TERRA, *high) == biome.ALPINE
    cold, _ = terrain.climate_at(constants, Planet.TERRA, *high)
    warm, _ = terrain.climate_at(constants, Planet.TERRA, high[0], low[1])
    assert cold <= warm + 1, "в горах не холоднее, чем на той же широте внизу"
    assert METRES_PER_KM > 0 and Vein is not None


async def test_a_body_scouts_again_after_a_run_is_over(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The second run of a life is not refused: the job's dedup key names the
    run, not the body (the review of D-321).

    And it is set out on **from the find**: a run ends standing where it went
    (D-327), so the second one is aimed from there, not from the camp.
    """
    async with factory() as session, session.begin():
        _, camp, scout = await _camp(session, constants)
        here = places.geo_of(camp)
        assert here is not None
        _, far = _reach(constants, camp)
        job = await explore.survey(
            session, constants, scout, _step(constants, Planet.TERRA, here, far * 0.8, bearing=0.0)
        )
        term, scout_id, camp_id = job.run_at, scout.id, camp.id
    assert await jobs.run_one(factory, now=term) is not None
    async with factory() as session, session.begin():
        scout = await session.get(Body, scout_id)
        assert scout is not None
        scout.stamina = constants[R.BODY_STAMINA_MAX]
        first = await session.scalar(select(Node).where(Node.key.like("terra.cell.%")))
        assert first is not None
        assert scout.node_id == first.id, "a run ends on the find (D-185, D-327)"
        here = places.geo_of(first)
        #: Aiming back at the camp, to which the find already has a way, is
        #: refused before it is paid.
        camp = await session.get(Node, camp_id)
        with pytest.raises(explore.AlreadyJoined):
            await explore.survey(session, constants, scout, places.geo_of(camp))
        again = await explore.survey(
            session, constants, scout, _step(constants, Planet.TERRA, here, far * 0.8, bearing=2.0)
        )
        assert again.state is JobState.PENDING


async def test_the_loser_of_the_race_brings_home_a_way_and_both_runs_end(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Two runs at one cell fired together: both jobs are done, one find is
    laid, the other run is told it reached a known place -- no failure, no retry."""
    async with factory() as session, session.begin():
        sphere, camp, scout = await _camp(session, constants)
        here = places.geo_of(camp)
        assert here is not None
        _, far = _reach(constants, camp)
        target = _step(constants, Planet.TERRA, here, far * 0.8, bearing=0.0)
        other_camp = await world.create_node(
            session,
            "terra.other",
            "Other",
            area_m2=60,
            parent=sphere,
            properties=_pin(_step(constants, Planet.TERRA, here, far * 1.6, bearing=0.0)),
        )
        other = await world.print_body(
            session, await world.create_identity(session, "Other"), other_camp
        )
        other.stamina = constants[R.BODY_STAMINA_MAX]
        await session.flush()
        first = await explore.survey(session, constants, scout, target)
        second = await explore.survey(session, constants, other, target)
        term = max(first.run_at, second.run_at)
        ids = (first.id, second.id)
    ready = asyncio.Barrier(2)

    async def fire() -> None:
        await ready.wait()
        await jobs.run_one(factory, now=term)

    await asyncio.gather(fire(), fire())
    async with factory() as session:
        states = {
            job.id: job.state
            for job in (await session.execute(select(Job).where(Job.id.in_(ids)))).scalars()
        }
        assert set(states.values()) == {JobState.DONE}, f"поход не закончился: {states}"
        found = (
            (
                await session.execute(
                    select(Event).where(Event.kind == EventKind.EXPLORE_FOUND.value)
                )
            )
            .scalars()
            .all()
        )
        assert len(found) == 2
        assert sorted(bool(event.payload.get("known")) for event in found) == [False, True]


async def test_a_scout_who_walked_away_comes_back_to_nothing(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The run is spent if the body is not where it began when the job fires."""
    async with factory() as session, session.begin():
        sphere, camp, scout = await _camp(session, constants)
        here = places.geo_of(camp)
        assert here is not None
        _, far = _reach(constants, camp)
        job = await explore.survey(
            session, constants, scout, _step(constants, Planet.TERRA, here, far * 0.8, bearing=0.0)
        )
        elsewhere = await world.create_node(
            session,
            "terra.elsewhere",
            "Elsewhere",
            area_m2=60,
            parent=sphere,
            properties=_pin(_step(constants, Planet.TERRA, here, far * 3, bearing=1.0)),
        )
        scout.node_id = elsewhere.id
        term = job.run_at
    assert await jobs.run_one(factory, now=term) is not None
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(Node).where(Node.key.like("terra.cell.%"))
            )
            == 0
        )
        empty = await session.scalar(
            select(Event).where(Event.kind == EventKind.EXPLORE_EMPTY.value)
        )
        assert empty is not None and empty.payload["why"] == "explore-scout-gone"
        assert BodyState.ALIVE is not None


def test_the_seed_pins_pyroxis_on_the_lattice_a_reach_apart(constants: Constants) -> None:
    """What the seed still lays itself stands where a scout would have found it."""
    spots = seed_planets.sites(constants, Planet.PYROXIS, 4, taken=[])
    assert len(spots) == 4
    radius = globe.radius_m(constants, Planet.PYROXIS)
    near, far = biome.reach_m(constants, biome.CINDER)
    for spot in spots:
        assert terrain.is_land(constants, Planet.PYROXIS, *spot.point)
        cell = explore.cell_of(constants, Planet.PYROXIS, spot.point)
        assert explore.point_of(constants, Planet.PYROXIS, cell) == spot.point
    for a in spots:
        for b in spots:
            if a is not b:
                assert globe.distance_m(radius, a.point, b.point) >= near
    assert all(globe.distance_m(radius, spots[0].point, s.point) <= far * 1.5 for s in spots[1:])


async def test_a_long_leap_lands_on_wide_ground_and_a_wide_node_keeps_others_off(
    session: AsyncSession, constants: Constants
) -> None:
    """The find's area follows the leap (the owner, 2026-09-06): the farther the
    aim, the wider the node; and next to a wide node there is no room to aim."""
    sphere, camp, _ = await _camp(session, constants)
    here = places.geo_of(camp)
    assert here is not None
    near, far = _reach(constants, camp)
    short = await explore.check(
        session, constants, camp, _step(constants, Planet.TERRA, here, far * 0.5)
    )
    long = await explore.check(
        session, constants, camp, _step(constants, Planet.TERRA, here, far * 0.75)
    )
    assert long.area > short.area, "дальний выпад — больше площадь"
    span = constants[R.EXPLORE_NODE_AREA]
    assert span.min <= short.area <= span.max and long.area <= span.max
    #: A wide node standing near the camp: the ground beside it is taken.
    wide_at = _step(constants, Planet.TERRA, here, far * 0.7, bearing=math.pi / 2)
    await world.create_node(
        session, "terra.wide", "Wide", area_m2=span.max, parent=sphere, properties=_pin(wide_at)
    )
    with pytest.raises(explore.NoRoom):
        await explore.check(
            session,
            constants,
            camp,
            _step(constants, Planet.TERRA, here, far * 0.8, bearing=math.pi / 2 + 0.3),
        )
    #: Too short a leap leaves no room for a node at all: refused as no room.
    with pytest.raises((explore.NoRoom, explore.TooNear)):
        await explore.check(session, constants, camp, _step(constants, Planet.TERRA, here, near))


async def test_a_scout_with_a_run_under_way_does_not_set_out(
    factory: async_sessionmaker[AsyncSession], constants: Constants
) -> None:
    """The road is a second deed (D-211): refused at the door, not found out
    at the run's end (D-321 item 7). The body stands in the node meanwhile."""
    async with factory() as session, session.begin():
        sphere, camp, scout = await _camp(session, constants)
        here = places.geo_of(camp)
        assert here is not None
        _, far = _reach(constants, camp)
        await explore.survey(
            session, constants, scout, _step(constants, Planet.TERRA, here, far * 0.8, bearing=0.0)
        )
        elsewhere = await world.create_node(
            session,
            "terra.elsewhere",
            "Elsewhere",
            area_m2=60,
            parent=sphere,
            properties=_pin(_step(constants, Planet.TERRA, here, far * 3, bearing=1.0)),
        )
        await travel.connect(session, camp, elsewhere, base_seconds=60, surface=Surface.WILD)
        with pytest.raises(travel.Scouting):
            await travel.depart(session, constants, scout, elsewhere)
        #: Not an absence: in the node, the scout is still there to be asked.
        await travel.require_here(session, scout)


def test_every_scheme_of_a_complex_names_a_planet_and_a_biome_that_exist(
    constants: Constants,
) -> None:
    """A scheme with a typo in its planet or biome would never roll and never
    complain (`spec.Shape` checks no record's shape): this does."""
    planets = {planet.value for planet in Planet}
    biomes = set(constants[R.BIOME_NAMES])
    for name, scheme in constants[R.COMPLEX_SCHEMES].items():
        assert scheme.get("planet") in planets, name
        assert scheme.get("biome") in biomes, name
        assert scheme.get("city") or scheme.get("nodes"), name


async def test_the_aim_reads_the_surface_by_its_index(session: AsyncSession) -> None:
    """The window is read by the index over the node's degrees, not by a
    scan of the planet: the query must be spelled as the index is."""
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    window = (
        select(Node.id)
        .where(places.degrees(places.PLACE_LAT).between(40, 42))
        .compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    plan = "\n".join(row[0] for row in (await session.execute(text(f"EXPLAIN {window}"))).all())
    assert "ix_node_map_lat" in plan, plan


async def test_the_run_ends_standing_on_the_find(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """A run is a walk out, and it ends where it went (D-185, D-327).

    The price is the walk **one way** -- `_wild_seconds` of the distance, with
    no return leg in it -- and D-185 settled long ago that the scout stays on
    the find. The rewrite for the globe (D-321 item 7) lost that quietly, and
    until 2026-09-09 the body paid a one-way price and came home for free.
    """
    async with factory() as session, session.begin():
        _, camp, scout = await _camp(session, constants)
        here = places.geo_of(camp)
        assert here is not None
        _, far = _reach(constants, camp)
        job = await explore.survey(
            session, constants, scout, _step(constants, Planet.TERRA, here, far * 0.8, bearing=0.4)
        )
        cell, term, scout_id, camp_id = tuple(job.payload["cell"]), job.run_at, scout.id, camp.id
        assert scout.node_id == camp_id
    assert await jobs.run_one(factory, now=term) is not None

    async with factory() as session:
        node = await session.scalar(
            select(Node).where(Node.key == explore.key_of(Planet.TERRA, cell))
        )
        scout = await session.get(Body, scout_id)
        assert node is not None and scout is not None
        assert scout.node_id == node.id, "the scout stands on the find"
        #: Arrived, not merely appeared: the hearing horizon starts here (D-043).
        assert scout.node_since == term


async def test_the_loser_of_the_race_ends_on_the_node_somebody_else_laid(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Walked there all the same: the cell was taken, the feet went the same metres.

    The second scout of a cell gets no node -- they get a way (D-321 item 3) --
    and afterwards they stand where they would have stood had they been first.
    """
    async with factory() as session, session.begin():
        sphere, camp, scout = await _camp(session, constants)
        here = places.geo_of(camp)
        assert here is not None
        _, far = _reach(constants, camp)
        target = _step(constants, Planet.TERRA, here, far * 0.8, bearing=0.0)
        job = await explore.survey(session, constants, scout, target)
        term, scout_id = job.run_at, scout.id
        #: The cell is taken before the term is up -- by somebody else's find,
        #: as in the race.
        aim = await explore.check(session, constants, camp, target)
        taken, _ = await explore.materialise(session, constants, catalog, aim, camp, who=None)
        taken_id = taken.id
        #: The way that other find laid is removed: with it the run would come
        #: to an already joined node and would earn no way of its own.
        await session.execute(
            Edge.__table__.delete().where(
                (Edge.node_a_id == taken_id) | (Edge.node_b_id == taken_id)
            )
        )
    assert await jobs.run_one(factory, now=term) is not None

    async with factory() as session:
        scout = await session.get(Body, scout_id)
        assert scout is not None
        assert scout.node_id == taken_id, "the second walked to the same point, and stands on it"


async def test_turning_back_leaves_the_scout_where_they_set_out(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Turning back: there is no half of a way in this world (D-152, D-194).

    It returns rather than stopping midway -- a node is the unit of place. What
    was spent is not returned: the stamina went up front, the hours have passed.
    """
    async with factory() as session, session.begin():
        _, camp, scout = await _camp(session, constants)
        here = places.geo_of(camp)
        assert here is not None
        _, far = _reach(constants, camp)
        job = await explore.survey(
            session, constants, scout, _step(constants, Planet.TERRA, here, far * 0.8, bearing=1.0)
        )
        cell, term, job_id = tuple(job.payload["cell"]), job.run_at, job.id
        left = float(scout.stamina)
        await explore.stop(session, scout)
        assert float(scout.stamina) == left, "the stamina went up front and does not come back"
        assert scout.node_id == camp.id
        camp_id = camp.id

    #: The job is dropped, and its term no longer fires anything.
    assert await jobs.run_one(factory, now=term) is None
    async with factory() as session:
        job = await session.get(Job, job_id)
        assert job is not None and job.state is JobState.CANCELLED
        laid = await session.scalar(
            select(func.count())
            .select_from(Node)
            .where(Node.key == explore.key_of(Planet.TERRA, cell))
        )
        assert laid == 0, "whoever turns back finds nothing"
        stopped = await session.scalar(
            select(Event).where(Event.kind == EventKind.EXPLORE_STOPPED.value)
        )
        assert stopped is not None and stopped.node_id == camp_id


async def test_turning_back_from_nothing_is_refused(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Nothing to turn back from: a refusal in words, not a silent "done"."""
    async with factory() as session, session.begin():
        _, _, scout = await _camp(session, constants)
        with pytest.raises(explore.NotOut):
            await explore.stop(session, scout)


async def test_the_run_says_where_it_goes_and_by_when(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """The map is told the run: where from, where to, and by which two stamps.

    The far end is a **place**, not a key: the node does not exist yet. Nothing
    already sent lets the client work it out (D-225), so it travels; where the
    scout is right now does not -- that is the two stamps and a straight line,
    counted on the client (D-226).
    """
    async with factory() as session, session.begin():
        _, camp, scout = await _camp(session, constants)
        here = places.geo_of(camp)
        assert here is not None
        _, far = _reach(constants, camp)
        assert await explore.leg_of(session, constants, scout) is None
        job = await explore.survey(
            session, constants, scout, _step(constants, Planet.TERRA, here, far * 0.8, bearing=2.5)
        )
        leg = await explore.leg_of(session, constants, scout)
        assert leg is not None
        assert leg["from_key"] == camp.key
        assert leg["arrives_at"] == job.run_at.isoformat()
        #: The point is the cell's centre: exactly where the find will stand.
        lat, lon = explore.point_of(constants, Planet.TERRA, tuple(job.payload["cell"]))
        assert leg["place"] == {"lat": lat, "lon": lon}
        #: The start and the term are one clock's two ends: the share is between.
        assert datetime.fromisoformat(leg["started_at"]) < job.run_at


async def test_the_turn_back_and_the_run_do_not_both_land(
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The race: a turn-back sent in the very second the job fires.

    Exactly one of the two must win, and the job's row lock is what settles it.
    The worker holds that row for the whole of its transaction (`jobs._claim`),
    so the turn-back finds it locked, skips it and refuses -- rather than
    cancelling a run that has already found something. The other order needs
    nothing: the worker's claim skips a locked row and reads `cancelled` next
    time round.

    The window is widened on purpose (`_slow`): on a local database the two
    sides otherwise miss each other and the race does not reproduce.
    """
    async with factory() as session, session.begin():
        _, camp, scout = await _camp(session, constants)
        here = places.geo_of(camp)
        assert here is not None
        _, far = _reach(constants, camp)
        job = await explore.survey(
            session, constants, scout, _step(constants, Planet.TERRA, here, far * 0.8, bearing=1.4)
        )
        cell, term, scout_id, camp_id = tuple(job.payload["cell"]), job.run_at, scout.id, camp.id

    #: The find is held between reading the field and writing the node -- the
    #: very window the turn-back arrives in.
    _slow(monkeypatch, explore_run, "materialise", delay=0.3)

    async def turn_back() -> str:
        await asyncio.sleep(0.1)
        async with factory() as session, session.begin():
            body = await session.get(Body, scout_id, with_for_update=True)
            assert body is not None
            try:
                await explore.stop(session, body)
            except explore.NotOut:
                return "refused"
            return "stopped"

    ran, said = await asyncio.gather(jobs.run_one(factory, now=term), turn_back())

    async with factory() as session:
        found = await session.scalar(
            select(Node).where(Node.key == explore.key_of(Planet.TERRA, cell))
        )
        body = await session.get(Body, scout_id)
        assert body is not None
        if said == "refused":
            #: The job won: the find is there, and the scout stands on it.
            assert ran is not None and found is not None
            assert body.node_id == found.id
        else:
            #: The turn-back won: no node, and the scout is where they set out.
            assert found is None
            assert body.node_id == camp_id


async def test_a_cart_does_not_go_into_the_wild(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Harnessed, one does not scout: a cart crosses neither wild nor trail (D-157).

    D-185 had the convoy follow the scout to the find, and that was written
    before the surfaces decided what a cart may cross. Left alone it also made
    the run free: `transport.stamina_k` is nought, because a cart carries
    instead of legs -- so a harnessed run cost no strength at all, against
    D-321 item 7's "as much stamina as the walk".
    """
    async with factory() as session, session.begin():
        _, camp, scout = await _camp(session, constants)
        here = places.geo_of(camp)
        assert here is not None
        _, far = _reach(constants, camp)
        yard = await world.node_container(session, camp)
        cart = await world.grant_item(session, yard, "cart", amount=1, origin="test")
        await transport.harness(session, constants, catalog, scout, cart)
        with pytest.raises(explore.Harnessed):
            await explore.survey(
                session,
                constants,
                scout,
                _step(constants, Planet.TERRA, here, far * 0.8, bearing=0.2),
            )


async def test_a_shut_place_is_not_aimed_at(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """A cell somebody has shut is a cell one may not aim at (D-199, D-327).

    A run ends standing where it went, so its far end has the same door a road
    has (`travel.depart` asks `access.require_entry`). Without this the survey
    command was a way into a fenced place -- one `may_enter` refuses to
    everybody not on the list -- and the map from inside it came with it.
    """
    async with factory() as session, session.begin():
        sphere, camp, scout = await _camp(session, constants)
        here = places.geo_of(camp)
        assert here is not None
        _, far = _reach(constants, camp)
        target = _step(constants, Planet.TERRA, here, far * 0.8, bearing=2.9)
        theirs = await world.create_identity(session, "Somebody")
        cell = explore.cell_of(constants, Planet.TERRA, target)
        yard = await world.create_node(
            session,
            "terra.yard",
            "Yard",
            planet=Planet.TERRA,
            area_m2=60,
            parent=sphere,
            properties=_pin(explore.point_of(constants, Planet.TERRA, cell)),
        )
        yard.owner_identity_id = theirs.id
        yard.gated = True
        await session.flush()
        assert not await access.may_enter(session, yard, scout.identity_id)
        with pytest.raises(explore.Shut):
            await explore.survey(session, constants, scout, target)
