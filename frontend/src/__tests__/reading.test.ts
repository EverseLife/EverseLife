// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The reading under the cursor (D-331 addendum): the cell of a point and
 * its numbers in the passport's units, off the rasters the map holds.
 */

import { describe, expect, it } from "vitest";

import type { RasterPassport } from "../api";
import { ang2pix, atlasIndex } from "../panels/map/healpix";
import { readingAt } from "../panels/map/reading";
import type { Rasters } from "../panels/map/rasters";
import { NO_BIOME } from "../panels/map/shade";

describe("the reading under a point", () => {
  const nside = 8;
  const passport: RasterPassport = {
    grid: "healpix", nside, cells: 12 * nside * nside,
    rows: 3 * (nside + 2), cols: 4 * (nside + 2), across: 4, down: 3, border: 1,
    step_m: 50, relief_m: 1000, height_unit_m: 0.1,
    biomes: ["desert", "taiga"], forms: ["plain"], water: ["land"],
    fluid: "water",
    temperature_c: { min: -64, step: 0.5, cold: -15, hot: 35 },
  };
  const n = passport.rows * passport.cols;
  const rasters: Rasters = {
    height: new Float32Array(n),
    biome: new Uint8Array(n).fill(NO_BIOME),
    form: new Uint8Array(n),
    water: new Uint8Array(n),
    rock: new Uint8Array(n),
    province: new Uint8Array(n),
    flow: new Uint8Array(n),
    lake: new Uint8Array(n),
    stream: new Uint8Array(n),
    temperature: new Uint8Array(n),
    rain: new Uint8Array(n),
    river: new Uint8Array(n).fill(255),
  };
  const at = { lat: 12.5, lon: -40 };
  const here = atlasIndex(passport, ang2pix(nside, at.lat, at.lon));
  rasters.biome[here] = 1;
  rasters.height[here] = 123.4;
  //: The temperature byte counts steps from the passport's floor: a hundred
  //: and fifty of half a degree over minus sixty-four is eleven degrees.
  rasters.temperature[here] = 150;
  rasters.rain[here] = 128;
  rasters.river[here] = 60;

  it("reads the cell the point falls in, in the passport's units", () => {
    const reading = readingAt(rasters, passport, at);
    expect(reading.biome).toBe(1);
    expect(passport.biomes[reading.biome!]).toBe("taiga");
    expect(reading.heightM).toBeCloseTo(123.4, 3);
    expect(reading.temperatureC).toBe(11);
    expect(reading.rainPercent).toBe(50);
    expect(reading.riverM).toBe(60);
  });

  it("names no biome over the water", () => {
    //: A point a face away: nothing was written there, and the raster's
    //: word for water is no biome at all.
    const sea = readingAt(rasters, passport, { lat: -50, lon: 120 });
    expect(sea.biome).toBeNull();
    expect(sea.riverM).toBe(255);
  });
});
