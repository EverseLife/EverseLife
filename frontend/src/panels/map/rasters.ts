// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The field's rasters as the page holds them (landscape plan waves 5-6):
 * asked for once per planet, decoded once, shared by the GPU ground that
 * shades by them and the vector layer that draws its lines off them.
 */

import { useEffect, useState } from "react";

import * as api from "../../api";
import type { RasterKind } from "../../api";
import { heightsOf } from "./shade";

/** The rasters decoded: heights in metres, classes as bytes. */
export type Rasters = {
  height: Float32Array;
  biome: Uint8Array;
  form: Uint8Array;
  water: Uint8Array;
  rock: Uint8Array;
  province: Uint8Array;
};

const RASTERS = new Map<string, Promise<Rasters>>();

/** The rasters of a planet, asked for once for the life of the page; a
 *  failed ask is forgotten, so the next asks again. */
export function rastersOf(planet: string): Promise<Rasters> {
  let asked = RASTERS.get(planet);
  if (!asked) {
    asked = Promise.all(
      (
        ["height", "biome", "form", "water", "rock", "province"] as const satisfies
          readonly RasterKind[]
      ).map((kind) => api.terrainRaster(planet, kind)),
    ).then(([height, biome, form, water, rock, province]) => ({
      height: heightsOf(height),
      biome: new Uint8Array(biome),
      form: new Uint8Array(form),
      water: new Uint8Array(water),
      rock: new Uint8Array(rock),
      province: new Uint8Array(province),
    }));
    asked.catch(() => RASTERS.delete(planet));
    RASTERS.set(planet, asked);
  }
  return asked;
}

/** The rasters of a planet once they have come, null until then. */
export function useRasters(planet: string | null): Rasters | null {
  const [held, setHeld] = useState<{ planet: string; rasters: Rasters } | null>(null);
  useEffect(() => {
    if (!planet) return;
    let live = true;
    rastersOf(planet).then(
      (rasters) => live && setHeld({ planet, rasters }),
      (why) => console.warn(`rasters of ${planet}:`, why),
    );
    return () => {
      live = false;
    };
  }, [planet]);
  return held && held.planet === planet ? held.rasters : null;
}
