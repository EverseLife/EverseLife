// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The field's rasters as the page holds them (landscape plan waves 5-6):
 * asked for once per planet, decoded once, shared by the GPU ground that
 * shades by them and the vector layer that draws its lines off them.
 */

import { useEffect, useState } from "react";

import * as api from "../../api";
import type { RasterKind, RasterPassport, Terrain } from "../../api";
import { GROUND_RASTERS, previewPassport, type GroundRasters } from "./groundPrep";
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
  /** The climate, for the map's climate layers (D-331): the temperature in
   *  the passport's steps; the rain a byte of the field's own share, nought
   *  to one over `site.rain_range` -- the scale a node's `precipitation`
   *  and the drying law read it on (D-126), not a share of the wettest
   *  cell. */
  temperature: Uint8Array;
  rain: Uint8Array;
  /** Metres to the nearest river or lake, a byte (255 and past it: far),
   *  the engine's own measure of "beside water" (`terrain.marks_at`), for
   *  the moisture layer (D-331 addendum). */
  river: Uint8Array;
};

const RASTERS = new Map<string, Promise<Rasters>>();
//: The rasters that have come, for a caller that must know without waiting
//: (`heldRasters`); and who waits for an ask somebody else is to make
//: (`useRasters`).
const HELD = new Map<string, Rasters>();
const WAITING = new Map<string, Set<() => void>>();

/** The rasters of a planet, asked for once for the life of the page; a
 *  failed ask is forgotten, so the next asks again.
 *
 *  By the passport's version of the picture (`raster.version`, 2026-09-18):
 *  asked for under it, the rasters are kept by the browser a year, and the
 *  passport that named them is always the one they are read by. So the
 *  passport comes first -- it is the first thing the ground asks for anyway. */
export function rastersOf(planet: string): Promise<Rasters> {
  let asked = RASTERS.get(planet);
  if (!asked) {
    const kinds = [
      "height", "biome", "form", "water", "rock", "province", "flow", "lake", "stream",
      "temperature", "rain", "river",
    ] as const satisfies readonly RasterKind[];
    asked = reliefOf(planet).then((terrain) => {
      const passport = terrain.raster;
      //: The passport says what a step of the height raster is worth, and
      //: nothing here guesses it: read as whole metres, decimetres would
      //: put every height ten times over with nothing failing.
      if (!passport) throw new Error(`no raster passport for ${planet}`);
      return Promise.all(
        kinds.map((kind) => api.terrainRaster(planet, kind, { version: passport.version })),
      ).then(([height, biome, form, water, rock, province, flow, lake, stream, temperature, rain, river]) => ({
        height: heightsOf(height, passport.height_unit_m),
        biome: new Uint8Array(biome),
        form: new Uint8Array(form),
        water: new Uint8Array(water),
        rock: new Uint8Array(rock),
        province: new Uint8Array(province),
        flow: new Uint8Array(flow),
        lake: new Uint8Array(lake),
        stream: new Uint8Array(stream),
        temperature: new Uint8Array(temperature),
        rain: new Uint8Array(rain),
        river: new Uint8Array(river),
      }));
    });
    asked.then(
      (rasters) => HELD.set(planet, rasters),
      () => RASTERS.delete(planet),
    );
    RASTERS.set(planet, asked);
    const waiting = WAITING.get(planet);
    WAITING.delete(planet);
    for (const tell of waiting ?? []) tell();
  }
  return asked;
}

/** The rasters of a planet if they have come already, without asking. */
export function heldRasters(planet: string): Rasters | null {
  return HELD.get(planet) ?? null;
}

const PREVIEWS = new Map<string, Promise<GroundRasters>>();

/** The GPU's nine rasters of a planet at the quick copy's fineness, asked
 *  for once for the life of the page like the whole picture's, by the same
 *  version; a failed ask is forgotten. A sixteenth of the bytes, so the real
 *  planet is drawn a moment after the passport and not after the megabytes. */
export function previewOf(planet: string, passport: RasterPassport): Promise<GroundRasters> {
  const small = previewPassport(passport);
  if (!small) return Promise.reject(new Error(`no preview for ${planet}`));
  let asked = PREVIEWS.get(planet);
  if (!asked) {
    asked = Promise.all(
      GROUND_RASTERS.map((kind) =>
        api.terrainRaster(planet, kind, { nside: small.nside, version: passport.version }),
      ),
    ).then(([height, biome, form, rock, lake, stream, temperature, rain, river]) => ({
      height: heightsOf(height, small.height_unit_m),
      biome: new Uint8Array(biome),
      form: new Uint8Array(form),
      rock: new Uint8Array(rock),
      lake: new Uint8Array(lake),
      stream: new Uint8Array(stream),
      temperature: new Uint8Array(temperature),
      rain: new Uint8Array(rain),
      river: new Uint8Array(river),
    }));
    asked.catch(() => PREVIEWS.delete(planet));
    PREVIEWS.set(planet, asked);
  }
  return asked;
}

/** The rasters of a planet once they have come, null until then -- and
 *  never the one to ask for them. The ground asks: the GPU's after its
 *  quick copy has come (`GroundGL`), the SVG's at once where there is no
 *  GPU ground (`Planet`). The legend and the probe mount with the map and
 *  used to ask first, and the megabytes of the whole picture took the line
 *  from the copy the ground was waiting to draw (review, 2026-09-18). An ask
 *  that failed is waited past for the next one. */
export function useRasters(planet: string | null): Rasters | null {
  const [held, setHeld] = useState<{ planet: string; rasters: Rasters } | null>(null);
  useEffect(() => {
    if (!planet) return;
    let live = true;
    const wait = () => {
      let waiting = WAITING.get(planet);
      if (!waiting) WAITING.set(planet, (waiting = new Set()));
      waiting.add(follow);
    };
    const follow = () => {
      const asked = RASTERS.get(planet);
      if (!asked) return wait();
      asked.then(
        (rasters) => live && setHeld({ planet, rasters }),
        (why) => {
          console.warn(`rasters of ${planet}:`, why);
          if (live) wait();
        },
      );
    };
    follow();
    return () => {
      live = false;
      WAITING.get(planet)?.delete(follow);
    };
  }, [planet]);
  return held && held.planet === planet ? held.rasters : null;
}
