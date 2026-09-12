// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What the ground says under a point, read off the rasters the map already
 * holds (D-331 addendum, owner 2026-09-12: on the biomes layer the cursor
 * names the biome, on the relief the height, on the temperature the mean
 * temperature). Pure: the cell of the point, and the numbers in it in the
 * passport's units -- the words are the component's (`Probe.tsx`). Named
 * apart from it by more than the case: on a case-blind disk `probe.ts` and
 * `Probe.tsx` were one file to the bundler, and the wrong one.
 */

import type { RasterPassport } from "../../api";
import type { Geo } from "./globe";
import { ang2pix, atlasIndex } from "./healpix";
import type { Rasters } from "./rasters";
import { NO_BIOME } from "./shade";

export type Reading = {
  /** The biome's code in the passport's `biomes`, or null over water. */
  biome: number | null;
  /** Metres over the sea; negative under it. */
  heightM: number;
  /** The mean temperature, degrees. */
  temperatureC: number;
  /** The rain, per cent of the vault's scale (`site.rain_range`). */
  rainPercent: number;
  /** Metres to the nearest river or lake, 255 and past it far. */
  riverM: number;
};

/** The readings of the cell under a point of the ground. */
export function readingAt(rasters: Rasters, passport: RasterPassport, at: Geo): Reading {
  const cell = ang2pix(passport.nside, at.lat, at.lon);
  const atlas = atlasIndex(passport, cell);
  const code = rasters.biome[atlas];
  return {
    biome: code === NO_BIOME ? null : code,
    heightM: rasters.height[atlas],
    temperatureC: passport.temperature_c.min + rasters.temperature[atlas] * passport.temperature_c.step,
    rainPercent: Math.round((rasters.rain[atlas] / 255) * 100),
    riverM: rasters.river[atlas],
  };
}
