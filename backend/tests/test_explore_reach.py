# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The reach a scout and a way are judged by (D-321, addendum of 2026-09-18).

What is pinned:

* a node's own land does not eat its reach: the far end is counted past the
  edge of the land, so a scout on the widest node the world lays still has a
  ring of ground at least a lattice cell wide, under every face of the vault;
* a way to a standing node reaches to that node's edge in turn, so a way to a
  wide node is laid where a find as far would be out of reach;
* a way is refused in the words of a way and a find in the words of a run --
  until 2026-09-18 a way to a known node was told that one scouts no
  farther from here, the scout's sentence, because the way reuses the
  scout's aim.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from explore_kit import _camp, _pin, _reach, _step
from src import globe
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.constants.catalog import Facet
from src.engine import biome, explore, facet, places, terrain, travel, world
from src.engine.explore import run as explore_run
from src.models.world import Layer, Node, Planet, Surface

#: How many bearings a test tries before it gives up on dry ground.
BEARINGS = 48


def _snapped(constants: Constants, point: globe.Geo) -> globe.Geo:
    """Where the engine will judge a point: its lattice cell's centre."""
    return explore.point_of(
        constants, Planet.TERRA, explore.cell_of(constants, Planet.TERRA, point)
    )


def _dry_bearings(constants: Constants, here: globe.Geo, metres: float) -> list[float]:
    """Bearings along which the ground is land and the way to it dry for
    `metres`: these tests ask about reach, so they walk where water does not
    answer first."""
    out = []
    for k in range(BEARINGS):
        bearing = math.tau * k / BEARINGS
        end = _snapped(constants, _step(constants, Planet.TERRA, here, metres, bearing))
        if terrain.is_land(constants, Planet.TERRA, *end) and not explore.crosses_water(
            constants, Planet.TERRA, here, end
        ):
            out.append(bearing)
    return out


def test_every_face_leaves_a_wide_node_a_ring_to_scout(
    constants: Constants, catalog: Catalog
) -> None:
    """The defect the owner saw (2026-09-18): from a node of wide land the
    scout had nothing to aim at. The band was counted from the node's centre,
    a find must stand clear of the node's circle by the room it needs, and on
    six hundred square metres -- the widest find -- a coastal reach of twenty
    metres left a ring under a metre wide, and a narrowing face none at all.
    Counted past the land, every face of the vault leaves at least a cell of
    the lattice between the room a find needs and the far end."""
    span = constants[R.EXPLORE_NODE_AREA]
    land = globe.radius_of_area(float(span.max))
    room = globe.radius_of_area(float(span.min)) / float(constants[R.EXPLORE_FILL_SHARE])
    cell = float(constants[R.MAP_LATTICE_M])
    for row in catalog.facets.facets:
        node = Node(
            key="terra.wide",
            name="Wide",
            planet=Planet.TERRA,
            layer=Layer.PLANET,
            area_m2=span.max,
            properties={facet.FACET: row.id},
        )
        near, far = facet.band_m(constants, catalog, node, row.biome)
        reach_near, reach_far = facet.reach_m(constants, row.biome, row)
        assert near == pytest.approx(reach_near)
        assert far == pytest.approx(max(land + reach_far, facet.centre_floor(constants)))
        assert far - (land + room) >= cell - 1e-9, f"{row.id} leaves a wide node no ring"


def test_no_node_scouts_less_far_than_it_did(constants: Constants, catalog: Catalog) -> None:
    """The owner asked for a longer reach (2026-09-18), and counting it past
    the land must not buy that with a shorter one elsewhere: counted from the
    centre the far end was the biome's times the face's, held up to the
    floor of the widest find's room, and that stands under the new band for
    every face and every size of node. Without the floor, a find of the
    least area in ten narrowing faces -- shore wood, thicket, reed beds --
    would have scouted three to five metres less far than before."""
    span = constants[R.EXPLORE_NODE_AREA]
    for row in catalog.facets.facets:
        biome_near, biome_far = biome.reach_m(constants, row.biome)
        before = max(biome_far * row.reach_k, facet.centre_floor(constants))
        for area in (span.min, span.max):
            node = Node(
                key="terra.any",
                name="Any",
                planet=Planet.TERRA,
                layer=Layer.PLANET,
                area_m2=area,
                properties={facet.FACET: row.id},
            )
            near, far = facet.band_m(constants, catalog, node, row.biome)
            assert near == pytest.approx(biome_near * row.reach_k)
            assert far >= before - 1e-9, f"{row.id} at {area} m2 scouts less far than before"


def _narrow_ground(constants: Constants, catalog: Catalog, clear: float) -> globe.Geo:
    """A point of Terra's land whose face narrows the far reach below `clear`
    -- the widest land and the room a find needs -- with dry ground all round:
    where a band counted from the centre left the scout nothing at all."""

    def narrow(point: globe.Geo, here: str, face: Facet) -> bool:
        if biome.reach_m(constants, here)[1] * face.reach_k >= clear - 1:
            return False
        return len(_dry_bearings(constants, point, clear * 2)) >= BEARINGS // 2

    return _ground(constants, catalog, narrow)


async def test_a_wide_node_still_has_ground_to_scout(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The same, on the ground: a scout standing on the widest node, where
    the face narrows the reach, finds a lawful aim past its land -- where the
    band counted from the centre, lifted to its old floor, ended exactly at
    the room a find needs and held no cell at all."""
    wide = float(constants[R.EXPLORE_NODE_AREA].max)
    land = globe.radius_of_area(wide)
    room = globe.radius_of_area(float(constants[R.EXPLORE_NODE_AREA].min)) / float(
        constants[R.EXPLORE_FILL_SHARE]
    )
    at = _narrow_ground(constants, catalog, land + room)
    _, camp, _ = await _camp(session, constants, at=at, area=wide)
    here = places.geo_of(camp)
    assert here is not None
    _, far = _reach(constants, catalog, camp)
    metres = globe.midpoint(land + room, far)
    lawful = []
    for bearing in _dry_bearings(constants, here, metres):
        try:
            lawful.append(
                await explore.check(
                    session,
                    constants,
                    catalog,
                    camp,
                    _step(constants, Planet.TERRA, here, metres, bearing),
                )
            )
        except explore.ExploreError:
            continue
    assert lawful, "a wide node has ground to scout past its land"
    assert all(aim.existing is None and aim.metres > land + room - 1e-6 for aim in lawful)


async def test_a_way_reaches_to_the_edge_of_a_wide_node(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A way to a standing node is walked from one land's edge to the
    other's: a wide node beyond a find's reach is still within a way's."""
    sphere, camp, _ = await _camp(session, constants)
    here = places.geo_of(camp)
    assert here is not None
    _, far = _reach(constants, catalog, camp)
    wide = float(constants[R.EXPLORE_NODE_AREA].max)
    edge = globe.radius_of_area(wide)
    #: Past a find's far end by half the wide node's radius: more than the
    #: lattice can move a point, and less than the node's edge brings back.
    metres = far + edge / 2
    dry = _dry_bearings(constants, here, far + edge)
    assert len(dry) >= 2, "the capital has dry ground in two directions"
    there = _snapped(constants, _step(constants, Planet.TERRA, here, metres, dry[0]))
    node = await world.create_node(
        session, "terra.wide", "Wide", area_m2=wide, parent=sphere, properties=_pin(there)
    )
    aim = await explore.check(session, constants, catalog, camp, there)
    assert aim.existing is not None and aim.existing.id == node.id
    assert aim.metres > far
    #: A bare point as far away, off the wide node's land, is out of reach:
    #: a find has no land of its own to be walked to the edge of yet.
    opposite = max(dry, key=lambda bearing: abs(math.remainder(bearing - dry[0], math.tau)))
    with pytest.raises(explore.TooFar) as caught:
        await explore.check(
            session,
            constants,
            catalog,
            camp,
            _step(constants, Planet.TERRA, here, metres, opposite),
        )
    assert caught.value.key == "explore-too-far"


async def test_a_way_is_refused_in_the_words_of_a_way(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The way reuses the scout's aim, and until 2026-09-18 it reused its
    words too: a node out of reach was told that one scouts no farther from
    here. An aim at a standing node is refused as a way, and names the
    node."""
    sphere, camp, _ = await _camp(session, constants)
    here = places.geo_of(camp)
    assert here is not None
    _, far = _reach(constants, catalog, camp)
    dry = _dry_bearings(constants, here, far * 3)
    assert len(dry) >= 2, "the capital has dry ground in two directions"
    opposite = max(dry, key=lambda bearing: abs(math.remainder(bearing - dry[0], math.tau)))
    small = float(constants[R.EXPLORE_NODE_AREA].min)
    beyond = _snapped(
        constants,
        _step(constants, Planet.TERRA, here, far + globe.radius_of_area(small) + 10, dry[0]),
    )
    await world.create_node(
        session, "terra.far", "Far", area_m2=small, parent=sphere, properties=_pin(beyond)
    )
    with pytest.raises(explore.TooFar) as caught:
        await explore.check(session, constants, catalog, camp, beyond)
    assert caught.value.key == "path-too-far"
    assert caught.value.params["node"] == "Far"

    #: A way across a way speaks of the way too.
    target = _snapped(constants, _step(constants, Planet.TERRA, here, far * 0.8, dry[0]))
    await world.create_node(
        session, "terra.target", "Target", area_m2=small, parent=sphere, properties=_pin(target)
    )
    left = await world.create_node(
        session,
        "terra.left",
        "Left",
        area_m2=small,
        parent=sphere,
        properties=_pin(_step(constants, Planet.TERRA, here, far * 0.5, dry[0] - 1.0)),
    )
    right = await world.create_node(
        session,
        "terra.right",
        "Right",
        area_m2=small,
        parent=sphere,
        properties=_pin(_step(constants, Planet.TERRA, here, far * 0.5, dry[0] + 1.0)),
    )
    await travel.connect(session, left, right, surface=Surface.WILD)
    with pytest.raises(explore.CrossesWay) as crossed:
        await explore.check(session, constants, catalog, camp, target)
    assert crossed.value.key == "path-crosses-way"
    assert crossed.value.params["node"] == "Target"
    #: And so does a way through a node: a second target the other way, with
    #: a small node on the straight way a third along it.
    other = _snapped(constants, _step(constants, Planet.TERRA, here, far * 0.8, opposite))
    await world.create_node(
        session, "terra.other", "Other", area_m2=small, parent=sphere, properties=_pin(other)
    )
    between = (here[0] + (other[0] - here[0]) / 3, here[1] + (other[1] - here[1]) / 3)
    await world.create_node(
        session, "terra.between", "Between", area_m2=30, parent=sphere, properties=_pin(between)
    )
    with pytest.raises(explore.ThroughNode) as through:
        await explore.check(session, constants, catalog, camp, other)
    assert through.value.key == "path-through-node"
    assert through.value.params == {"node": "Between", "target": "Other"}


def _ground(
    constants: Constants,
    catalog: Catalog,
    keep: Callable[[globe.Geo, str, Facet], bool],
) -> globe.Geo:
    """The first point of Terra's land, on a coarse sweep, that `keep` takes:
    looked up and not named, because every rebuilt field moves the faces."""
    for lat in range(-60, 61, 3):
        for lon in range(-180, 180, 6):
            point = _snapped(constants, (float(lat), float(lon)))
            here = biome.classify(constants, Planet.TERRA, *point)
            if here is None:
                continue
            face = facet.at(constants, catalog, Planet.TERRA, *point, here=here)
            if face is not None and keep(point, here, face):
                return point
    raise AssertionError("no such ground on Terra")


def test_a_scheme_fans_its_parts_where_they_have_room(
    constants: Constants, catalog: Catalog
) -> None:
    """The parts of a complex stand one reach out from the find, and that
    reach is the middle of the ring a part may lawfully take: past the find's
    own land and the room a part needs, short of the find's far end. The
    middle of the band from the centre put a wide find's parts inside itself
    and a narrow one's short of the room, and the room rule dropped them."""
    room = facet.room_m(constants)
    span = constants[R.EXPLORE_NODE_AREA]
    radius = globe.radius_m(constants, Planet.TERRA)
    centre = (0.0, 0.0)
    for row in catalog.facets.facets:
        for area in (span.min, span.max):
            node = Node(
                key="terra.find",
                name="",
                planet=Planet.TERRA,
                layer=Layer.PLANET,
                area_m2=area,
                properties={biome.BIOME: row.biome, facet.FACET: row.id},
            )
            _, far = facet.band_m(constants, catalog, node, row.biome)
            where = explore_run._beside(constants, catalog, node, centre, 0)
            metres = globe.distance_m(radius, centre, where)
            assert metres - globe.radius_of_area(area) >= room, f"{row.id} at {area} m2"
            assert metres <= far, f"{row.id} at {area} m2"


async def test_a_way_too_near_or_across_water_is_refused_as_a_way(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The two refusals of a way the other tests do not reach: a standing node
    closer than the near end, and one whose straight way lies over water."""
    lattice = float(constants[R.MAP_LATTICE_M])
    small = float(constants[R.EXPLORE_NODE_AREA].min)

    #: Where the near end is wider than two cells of the lattice, the next
    #: cell but one is too near for a way.
    def wide_near(point: globe.Geo, here: str, face: Facet) -> bool:
        near = biome.reach_m(constants, here)[0] * face.reach_k
        return near > 2.5 * lattice and bool(_dry_bearings(constants, point, 2 * lattice))

    at = _ground(constants, catalog, wide_near)
    sphere, camp, _ = await _camp(session, constants, at=at)
    here = places.geo_of(camp)
    assert here is not None
    bearing = _dry_bearings(constants, here, 2 * lattice)[0]
    close = _snapped(constants, _step(constants, Planet.TERRA, here, 2 * lattice, bearing))
    await world.create_node(
        session, "terra.close", "Close", area_m2=small, parent=sphere, properties=_pin(close)
    )
    with pytest.raises(explore.TooNear) as caught:
        await explore.check(session, constants, catalog, camp, close)
    assert caught.value.key == "path-too-near"
    assert caught.value.params["node"] == "Close"

    #: A node on land across a stretch of water, within the reach.
    radius = globe.radius_m(constants, Planet.TERRA)

    def across(point: globe.Geo) -> globe.Geo | None:
        node = Node(
            key="terra.probe",
            name="",
            planet=Planet.TERRA,
            layer=Layer.PLANET,
            area_m2=small,
            properties={places.PLACE: {places.PLACE_LAT: point[0], places.PLACE_LON: point[1]}},
        )
        here_biome = biome.classify(constants, Planet.TERRA, *point) or ""
        near, far = facet.band_m(constants, catalog, node, here_biome)
        for k in range(BEARINGS):
            for share in (0.6, 0.8, 0.95):
                metres = near + share * (far - near)
                end = _snapped(
                    constants,
                    _step(constants, Planet.TERRA, point, metres, math.tau * k / BEARINGS),
                )
                if (
                    globe.distance_m(radius, point, end) <= far
                    and terrain.is_land(constants, Planet.TERRA, *end)
                    and explore.crosses_water(constants, Planet.TERRA, point, end)
                ):
                    return end
        return None

    shore = _ground(constants, catalog, lambda point, _here, _face: across(point) is not None)
    #: A second place on the same sphere: the aim needs a node to stand on,
    #: not a scout on it.
    coast = await world.create_node(
        session, "terra.coast", "Coast", area_m2=small, parent=sphere, properties=_pin(shore)
    )
    from_here = places.geo_of(coast)
    assert from_here is not None
    far_side = across(from_here)
    assert far_side is not None
    await world.create_node(
        session, "terra.isle", "Isle", area_m2=small, parent=sphere, properties=_pin(far_side)
    )
    with pytest.raises(explore.IntoWater) as wet:
        await explore.check(session, constants, catalog, coast, far_side)
    assert wet.value.key == "path-into-water"
    assert wet.value.params["node"] == "Isle"
