# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The facet: the face a point's biome wears (landscape plan, wave 7).

What is pinned:

* the vault's rows are whole -- every biome has faces, their shares add up,
  and every face has a name in both languages;
* the choice is of the vault, not of the code: the readings pick among the
  boxes that hold the point, by their shares, and a point outside every box
  still gets the nearest face rather than none;
* the same point is the same face, always -- the field is a file and the
  noise is a function of the seed (D-237);
* the numbers of a place are the biome's bent by the face: the marks, the
  vein's chance, the day's swing and the reach one scouts by.
"""

from __future__ import annotations

import pytest

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import biome, facet, terrain
from src.models.world import Planet

#: The vault's own softness, as `facet.at` reads it.
SOFT = 0.25


def land(constants: Constants, step: int = 7) -> list[tuple[float, float]]:
    """Points of Terra's land, coarsely swept."""
    field = terrain.field_of(constants, Planet.TERRA)
    return [
        (lat, lon)
        for lat in range(-60, 61, step)
        for lon in range(-180, 180, step * 2)
        if not field.is_water(lat, lon)
    ]


def test_every_biome_has_faces_and_their_shares_add_up(
    constants: Constants, catalog: Catalog
) -> None:
    names = constants[R.BIOME_NAMES]
    assert catalog.facets.facets, "the vault's facets are loaded"
    for name in names:
        rows = catalog.facets.of_biome(name)
        assert rows, f"biome {name} has no face"
        assert round(sum(row.share for row in rows), 3) == 100
        for row in rows:
            assert row.name and len(row.name) <= 18
            assert set(row.marks) == {"woods", "stones", "meadow"}
            assert row.vein_k > 0 and row.reach_k > 0 and row.swing_k > 0
            for span in (row.where.slope, row.where.wet, row.where.high):
                assert 0 <= span[0] < span[1] <= 1


def test_the_choice_is_the_box_and_the_share(catalog: Catalog) -> None:
    rows = catalog.facets.of_biome("forest")
    #: A point inside one box alone takes that face, whatever the noise says.
    for row in rows:
        middle = tuple(
            (span[0] + span[1]) / 2 for span in (row.where.slope, row.where.wet, row.where.high)
        )
        chosen = {facet.choose(rows, noise / 20, *middle, SOFT).id for noise in range(21)}
        assert row.id in chosen, f"{row.id} is never chosen inside its own box"
    #: The noise divides the ones that hold the point, and nothing else does:
    #: the same readings with a different noise may give a different face, and
    #: with the same noise always give the same one.
    readings = (0.05, 0.3, 0.5)
    twice = [facet.choose(rows, 0.42, *readings, SOFT).id for _ in range(2)]
    assert twice[0] == twice[1]
    #: A point outside every box is not left faceless: the nearest one takes it.
    assert facet.choose(rows, 0.5, 1.0, 1.0, 1.0, SOFT) is not None
    assert facet.choose((), 0.5, 0.5, 0.5, 0.5, SOFT) is None


def test_the_readings_are_of_the_field_and_stay_in_their_bounds(constants: Constants) -> None:
    for point in land(constants)[:60]:
        noise, slope, wet, high = facet.readings(constants, Planet.TERRA, *point)
        assert 0 <= noise <= 1 and 0 <= slope <= 1 and 0 <= wet <= 1 and 0 <= high <= 1
    #: The water's own edge reads as wet, the middle of a continent as dry.
    field = terrain.field_of(constants, Planet.TERRA)
    shore = next(
        p
        for p in land(constants, step=3)
        if field.sea_distance_m(*p) < constants[R.BIOME_FACET_AXES]["wet_km"] * 500
    )
    assert facet.readings(constants, Planet.TERRA, *shore)[2] > 0.5


def test_a_point_wears_one_face_and_wears_it_always(constants: Constants, catalog: Catalog) -> None:
    for point in land(constants)[:40]:
        here = biome.classify(constants, Planet.TERRA, *point)
        face = facet.at(constants, catalog, Planet.TERRA, *point, here=here)
        assert face is not None and face.biome == here
        again = facet.at(constants, catalog, Planet.TERRA, *point)
        assert again is not None and again.id == face.id, "the same point, the same face"


def test_the_shares_of_the_vault_divide_the_ground(constants: Constants, catalog: Catalog) -> None:
    """The measured defect of the first cut (the review of wave 7): the boxes
    of the vocabulary do not tile the cube, so a hard box test gave whole
    biomes to one face -- 93 % of the coast was `shore_wood`. The shares are
    weights now, and the test measures the world rather than the arithmetic."""
    from collections import Counter

    soft = facet.axes(constants)["soft_edge"]
    seen: dict[str, Counter] = {}
    for point in land(constants, step=3)[:600]:
        here = biome.classify(constants, Planet.TERRA, *point)
        rows = catalog.facets.of_biome(here)
        chosen = facet.choose(rows, *facet.readings(constants, Planet.TERRA, *point), soft)
        seen.setdefault(here, Counter())[chosen.id] += 1
    #: Where a biome is well sampled, no face takes it whole and more than one
    #: shows up: that is what the shares are for.
    for here, counter in seen.items():
        total = sum(counter.values())
        if total < 40:
            continue
        top = counter.most_common(1)[0][1] / total
        assert len(counter) > 2, f"{here} wears only {sorted(counter)}"
        assert top < 0.6, f"{here} is {top:.0%} one face"


def test_a_face_may_narrow_the_band_but_never_close_it(
    constants: Constants, catalog: Catalog
) -> None:
    """A find one cannot scout from is a dead end: ten faces of the vault
    would shorten a twenty-metre reach below the room a node needs, and the
    floor of the placement rule holds them up (D-321 item 4)."""
    floor = facet.room_floor(constants)
    assert floor > 0
    for row in catalog.facets.facets:
        near, far = facet.reach_m(constants, row.biome, row)
        assert far >= floor, f"{row.id} leaves nothing to aim at"
        assert near < far
    #: The narrowest of them is held exactly at the floor, and a wide one is
    #: the biome's band times its own multiplier, untouched.
    reeds = catalog.facets.by_id("reed_beds")
    assert facet.reach_m(constants, "marsh", reeds)[1] == pytest.approx(floor)
    knoll = catalog.facets.by_id("knoll")
    assert facet.reach_m(constants, "forest", knoll)[1] == pytest.approx(
        biome.reach_m(constants, "forest")[1] * knoll.reach_k
    )


def test_the_face_bends_the_biome_numbers(constants: Constants, catalog: Catalog) -> None:
    thicket = catalog.facets.by_id("thicket")
    assert thicket is not None and thicket.biome == "forest"
    #: The marks are the face's own, not the biome's.
    assert facet.marks(constants, "forest", thicket) == dict(thicket.marks)
    assert facet.marks(constants, "forest", None) == biome.marks(constants, "forest")
    #: The rest are the biome's, multiplied.
    assert facet.vein_k(constants, "forest", thicket) == pytest.approx(
        biome.vein_k(constants, "forest") * thicket.vein_k
    )
    assert facet.swing_c(constants, "forest", thicket) == pytest.approx(
        biome.swing_c(constants, "forest") * thicket.swing_k
    )
    near, far = facet.reach_m(constants, "forest", thicket)
    plain_near, plain_far = biome.reach_m(constants, "forest")
    floor = facet.room_floor(constants)
    assert near == pytest.approx(plain_near * thicket.reach_k)
    #: The far end is the biome's times the face's, but never under the room
    #: a node needs: the thicket's 0.6 is one of the ten that reach the floor.
    assert far == pytest.approx(max(plain_far * thicket.reach_k, floor))
    #: Without a face the biome's own band stands, held by the same floor.
    assert facet.reach_m(constants, "forest", None) == pytest.approx(
        (plain_near, max(plain_far, floor))
    )
    #: A face the vault has dropped reads as no face at all, not as a crash.
    assert catalog.facets.by_id("no_such_facet") is None
