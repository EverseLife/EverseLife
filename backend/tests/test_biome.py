# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The biome is the vault's table, not the engine's ladder (landscape plan, wave 4).

What is pinned:

* the zonal table sorts a climate by its rectangle -- the low edge in, the
  high edge out, the top of the plane in -- and a climate off the plane is
  pushed to its edge, never left without a class;
* the azonal table outranks the climate: a point whose landform is in it
  gets that biome, whatever the temperature says;
* the ice of the field is read as ice, and the mountain line as alpine;
* the coast is the sea-distance raster's word: within `coast_km` of the sea a
  formless point is a coast, and no ray-walk over the heights decides it.
"""

from __future__ import annotations

import pytest

from src.constants import Constants
from src.constants import registry as R
from src.engine import biome, terrain
from src.models.world import Planet
from src.units import METRES_PER_KM


def test_the_zonal_table_sorts_a_climate_by_its_rectangle(constants: Constants) -> None:
    rows = constants[R.BIOME_ZONAL]
    for row in rows.values():
        (t0, t1), (r0, r1) = row["temp"], row["rain"]
        assert biome.zonal(constants, t0, r0) == row["biome"], "the low corner is the row's"
        assert biome.zonal(constants, (t0 + t1) / 2, (r0 + r1) / 2) == row["biome"]
    top_t = max(row["temp"][1] for row in rows.values())
    top_r = max(row["rain"][1] for row in rows.values())
    corner = next(
        row["biome"] for row in rows.values() if row["temp"][1] == top_t and row["rain"][1] == top_r
    )
    assert biome.zonal(constants, top_t, top_r) == corner, "the top of the plane has a class"
    assert biome.zonal(constants, top_t + 40.0, top_r + 40.0) == corner, (
        "and so does what is past it"
    )
    bottom = next(
        row["biome"]
        for row in rows.values()
        if row["temp"][0] == min(r["temp"][0] for r in rows.values()) and row["rain"][0] == 0
    )
    assert biome.zonal(constants, -300.0, -5.0) == bottom
    assert all(row["biome"] in constants[R.BIOME_NAMES] for row in rows.values())


def test_the_land_shape_outranks_the_climate_and_the_ice_is_read(constants: Constants) -> None:
    field = terrain.field_of(constants, Planet.TERRA)
    azonal = constants[R.BIOME_AZONAL]
    points = [(lat, lon) for lat in range(-70, 71, 2) for lon in range(-180, 180, 3)]
    shaped = [
        p
        for p in points
        if not field.is_water(*p)
        and not field.ice_at(*p)
        and not field.is_mountain(*p)
        and field.form_at(*p) in azonal
    ]
    assert shaped, "no shaped land on Terra"
    for p in shaped[:40]:
        assert biome.classify(constants, Planet.TERRA, *p) == azonal[field.form_at(*p)]
    icy = [p for p in points if not field.is_water(*p) and field.ice_at(*p)]
    if icy:
        assert biome.classify(constants, Planet.TERRA, *icy[0]) == biome.ICE
    high = next(
        p
        for p in points
        if not field.is_water(*p) and field.is_mountain(*p) and not field.ice_at(*p)
    )
    assert biome.classify(constants, Planet.TERRA, *high) == biome.ALPINE


def test_the_coast_is_the_sea_distance_raster(constants: Constants) -> None:
    field = terrain.field_of(constants, Planet.TERRA)
    reach = constants[R.BIOME_BOUNDS]["coast_km"] * METRES_PER_KM
    azonal = constants[R.BIOME_AZONAL]
    shore = next(
        (lat, lon)
        for lat in range(-60, 61, 1)
        for lon in range(-180, 180, 1)
        if not field.is_water(lat, lon)
        and not field.ice_at(lat, lon)
        and not field.is_mountain(lat, lon)
        and field.form_at(lat, lon) not in azonal
        and field.sea_distance_m(lat, lon) <= reach
        and field.river_distance_deg(lat, lon)
        > terrain.river_reach_deg(constants, Planet.TERRA, lat)
    )
    assert biome.classify(constants, Planet.TERRA, *shore) == biome.COAST
    inland = max(
        (
            (lat, lon)
            for lat in range(-60, 61, 2)
            for lon in range(-180, 180, 2)
            if not field.is_water(lat, lon)
        ),
        key=lambda p: field.sea_distance_m(*p) if field.sea_distance_m(*p) < float("inf") else 1e12,
    )
    assert field.sea_distance_m(*inland) > reach
    assert biome.classify(constants, Planet.TERRA, *inland) != biome.COAST


@pytest.mark.parametrize("planet", [Planet.AURORA, Planet.PYROXIS])
def test_the_planets_of_one_face_keep_it(constants: Constants, planet: Planet) -> None:
    assert biome.classify(constants, planet, 0.0, 0.0) in (biome.OF_PLANET[planet], None)
