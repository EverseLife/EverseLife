// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The field's rasters as the page holds them (landscape plan waves 5-6):
 * asked for once per planet, decoded once, shared by the GPU ground that
 * shades by them and the vector layer that draws its lines off them.
 */

import { useEffect, useState } from "react";

import * as api from "../../api";
import type { RasterKind, Terrain } from "../../api";
import { heightsOf } from "./shade";

/** The planet's terrain passport, asked for once for the life of the page
 *  and shared by the ground (`Ground.tsx`) and the rasters, which read the
 *  height raster's unit off it. A failed fetch is not a fact about the
 *  planet: the next ask asks again. */
const RELIEF = new Map<string, Promise<Terrain>>();

export function reliefOf(planet: string): Promise<Terrain> {
  let asked = RELIEF.get(planet);
  if (!asked) {
    asked = api.terrain(planet).catch((why) => {
      RELIEF.delete(planet);
      throw why;
    });
    RELIEF.set(planet, asked);
  }
  return asked;
}

/** The rasters decoded: heights in metres, classes as bytes. */
export type Rasters = {
  height: Float32Array;
  biome: Uint8Array;
  form: Uint8Array;
  water: Uint8Array;
  rock: Uint8Array;
  province: Uint8Array;
  flow: Uint8Array;
  lake: Uint8Array;
  /** The river's ribbon (`stream`): a measure that falls to a half at the
   *  bank (the vault's `pipeline.ribbon`), so the shader cuts it between
   *  the cells as it cuts a lake's. */
  stream: Uint8Array;
};

const RASTERS = new Map<string, Promise<Rasters>>();

/** The rasters of a planet, asked for once for the life of the page; a
 *  failed ask is forgotten, so the next asks again. */
export function rastersOf(planet: string): Promise<Rasters> {
  let asked = RASTERS.get(planet);
  if (!asked) {
    const kinds = [
      "height", "biome", "form", "water", "rock", "province", "flow", "lake", "stream",
    ] as const satisfies readonly RasterKind[];
    asked = Promise.all([
      //: The passport says what a step of the height raster is worth, and
      //: nothing here guesses it: read as whole metres, decimetres would
      //: put every height ten times over with nothing failing.
      reliefOf(planet).then((terrain) => {
        const unit = terrain.raster?.height_unit_m;
        if (unit === undefined) throw new Error(`no raster passport for ${planet}`);
        return unit;
      }),
      ...kinds.map((kind) => api.terrainRaster(planet, kind)),
    ]).then(([unit, height, biome, form, water, rock, province, flow, lake, stream]) => ({
      height: heightsOf(height, unit),
      biome: new Uint8Array(biome),
      form: new Uint8Array(form),
      water: new Uint8Array(water),
      rock: new Uint8Array(rock),
      province: new Uint8Array(province),
      flow: new Uint8Array(flow),
      lake: new Uint8Array(lake),
      stream: new Uint8Array(stream),
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
