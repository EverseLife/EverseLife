// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The rasters made ready for the GPU: every number the ground's textures
 * hold, worked out before a single call of WebGL is made.
 *
 * The atlas laid out again with its wide border (`atlas.widen`, `retile`),
 * and every raster's chain of levels by what its bytes mean (`shade`'s
 * `mipChain`, `topChain`, `byteChain`). This is arithmetic over a few
 * million texels a raster, nine rasters -- a third of a second on a
 * laptop's main thread in one piece, a second and more on a phone's, and
 * the page stood frozen through it the first time a planet was opened. So
 * it runs in a worker (`groundPrep.worker.ts`), and the page only hands
 * the finished levels to the GPU; where no worker can be had the same
 * function runs here, as it always did.
 *
 * Nothing in this file may touch the page: it is imported by the worker,
 * where there is no document and no WebGL.
 */

import type { RasterPassport } from "../../api";
import { retile, widen } from "./atlas";
import type { Rasters } from "./rasters";
import { byteChain, deepOf, mipChain, topChain } from "./shade";

/** One level of a chain, as `texImage2D` takes it. */
export type Level<T extends Float32Array | Uint8Array> = { data: T; cols: number; rows: number };

/** The nine rasters the GPU draws by and no others: `water`, `province` and
 *  `flow` are the vector layer's and are not laid out again. */
export const GROUND_RASTERS = [
  "height", "biome", "form", "rock", "lake", "stream", "temperature", "rain", "river",
] as const satisfies readonly (keyof Rasters)[];

export type GroundRasters = Pick<Rasters, (typeof GROUND_RASTERS)[number]>;

/** The GPU's nine out of the page's rasters, the arrays themselves: what
 *  goes to the worker is copied on the way, and the page keeps its own. */
export function groundOf(rasters: GroundRasters): GroundRasters {
  const { height, biome, form, rock, lake, stream, temperature, rain, river } = rasters;
  return { height, biome, form, rock, lake, stream, temperature, rain, river };
}

/** Everything the textures hold, level by level. */
export type Prepared = {
  /** The passport of the picture's own layout: the border and the size. */
  passport: RasterPassport;
  height: Level<Float32Array>[];
  /** The height's max chain, which the cast shadow reads a stretch by. */
  top: Level<Float32Array>[];
  biome: Level<Uint8Array>[];
  form: Level<Uint8Array>[];
  rock: Level<Uint8Array>[];
  lake: Level<Uint8Array>[];
  stream: Level<Uint8Array>[];
  temperature: Level<Uint8Array>[];
  rain: Level<Uint8Array>[];
  river: Level<Uint8Array>[];
  /** The tallest ground: the last level of the max chain is the max of all. */
  topM: number;
  /** The deepest sea of the raster, metres: the water's shade runs to it. */
  deep: number;
};

/** The rasters as they came from the server, made ready for the GPU. */
export function prepare(served: RasterPassport, came: GroundRasters): Prepared {
  //: The picture's own layout: the faces as they came, each tile grown to
  //: a power of two and the room round the face filled from over the edge
  //: (`atlas.widen`), so the coarse levels of every chain stay within
  //: their own face and no seam shows. The passport kept with the textures
  //: is this one; what the vector layer reads is untouched.
  const wide = widen(served);
  const passport = wide.passport;
  const laid = <T extends Float32Array | Uint8Array>(raster: T): T =>
    wide.map ? retile(wide.map, raster) : raster;
  const { rows, cols } = passport;
  const tile = passport.nside + 2 * passport.border;
  const heights = laid(came.height);
  //: How a level is made differs by what the byte means (`byteChain`) --
  //: a share is averaged, a class is picked, because the mean of two codes
  //: is a third code that means something else; the river's ribbon is cut
  //: at its bank first and then averaged.
  const bytes = (raster: Uint8Array, how: "mean" | "pick" | "cut") =>
    byteChain(laid(raster), cols, rows, how, tile);
  const top = topChain(heights, cols, rows, tile);
  return {
    passport,
    height: mipChain(heights, cols, rows, tile),
    top,
    biome: bytes(came.biome, "pick"),
    form: bytes(came.form, "pick"),
    rock: bytes(came.rock, "mean"),
    lake: bytes(came.lake, "mean"),
    stream: bytes(came.stream, "cut"),
    temperature: bytes(came.temperature, "mean"),
    rain: bytes(came.rain, "mean"),
    river: bytes(came.river, "mean"),
    topM: Math.max(...top[top.length - 1].data),
    deep: deepOf(heights),
  };
}

/** The buffers of a prepared set, to be handed over rather than copied. */
export function buffersOf(prepared: Prepared): ArrayBuffer[] {
  const seen = new Set<ArrayBuffer>();
  for (const key of GROUND_RASTERS) {
    const chain: Level<Float32Array | Uint8Array>[] = prepared[key];
    for (const level of chain) seen.add(level.data.buffer as ArrayBuffer);
  }
  for (const level of prepared.top) seen.add(level.data.buffer as ArrayBuffer);
  return [...seen];
}
