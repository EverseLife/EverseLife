// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The grain of the ground: the numbers of the texture the fragment
 * roughens the near frames with (`grainGlsl.ts` is the GLSL of it).
 *
 * Cut out of `shade.ts` on 2026-09-12, when the grain grew a table of
 * kinds (`biome.grain`, D-331 addendum) and the file crossed the bar. What
 * is here is the picture's own: the lattice and its octaves, the kinds a
 * biome's word names and their numbers, the wrap that keeps the lattice
 * within a float, and when the grain shows at all. The word a biome wears
 * is the vault's (`biome.grain`, read off the book); nothing here decides
 * anything the engine reads (landscape plan §9.2).
 */

import { PALETTE_SLOTS } from "./shade";

/** The grain of the ground (landscape plan wave 8, §9.5): the texture that
 *  says what one is standing on -- stone, sand, ice, turf -- laid over the
 *  hillshade.
 *
 *  It decides nothing, and it is not the facet drawn (§9.2: "зерно от
 *  шейдера ничего не решает... ему разрешено быть просто красивым"). The
 *  face a find wears is the engine's (`engine/facet.py`), off the field's
 *  own numbers; what the eye sees here is the same ground's character read
 *  off the landform and the rock.
 *
 *  **A cell is so many metres of ground and stays so at every zoom.** It
 *  followed the pixel once, stepping by octaves so as to stay the same
 *  size on the glass, and that was wrong for a reason no measure would
 *  have caught: the ground then changes when the hand zooms, and a map
 *  whose country is rearranged by looking closer is not a map (owner,
 *  2026-09-09). What the zoom may change is only whether the grain can be
 *  seen at all -- and that it must, or a texture finer than a pixel turns
 *  into a shimmer of noise. Sixty-four metres is the coarsest cell any
 *  biome's grain has: a biome's own lattice is this divided by its scale
 *  (`GRAIN_KINDS`), and the scale is a whole number so that the wrap of
 *  the lattice (`GRAIN_WRAP`) is a wrap of every biome's lattice too. */
export const GRAIN_M = 64;
/** How many octaves of it there are, each half the last, and how much
 *  quieter each finer one is. Six from sixty-four metres reach down to
 *  two: the ground is one fixed texture with detail at many sizes, as real
 *  ground is, so coming closer **uncovers** the fine detail instead of
 *  rearranging the coarse -- which is the whole of what the owner asked
 *  for. An octave is drawn only where its own cell is worth pixels, so
 *  none of them is ever aliasing. */
export const GRAIN_OCTAVES = 6;
export const GRAIN_FALL = 0.6;

/**
 * The shapes a biome's grain can take, by number, as the fragment shapes
 * the raw noise (`shapeOf`): the plain noise; clumps, where it is pushed to
 * plateaus with soft edges, as crowns of trees read from above; cracks,
 * thin dark lines where the noise crosses nought; pools, dark where it
 * falls low; patches, dark where it stands high; a net, faint lines with
 * the plain noise between them, as frost polygons; and speckle, the noise
 * sharpened.
 */
export const GRAIN_SHAPE = {
  plain: 0,
  clumps: 1,
  cracks: 2,
  pools: 3,
  patches: 4,
  net: 5,
  speckle: 6,
} as const;

/** One kind of grain: how fine its lattice is (a whole number of cells to
 *  GRAIN_M -- see there), how much the lattice is squeezed along one axis
 *  (a quarter, a half, three quarters or none: the wrap must land on a
 *  whole cell), how loud it is, and its shape. */
export type GrainKind = {
  scale: number;
  stretch: number;
  contrast: number;
  shape: (typeof GRAIN_SHAPE)[keyof typeof GRAIN_SHAPE];
};

/**
 * The grain of each kind of ground the vault names (`biome.grain`, D-331
 * addendum; owner, 2026-09-12: the noise is too much the same, each biome
 * is to have its own). The word is the vault's -- which biome wears which
 * character -- and the numbers are the picture's, here: what a biome's
 * ground looks like from above at a few metres a pixel.
 *
 * * `sand` -- fine ripples of a beach, quiet; `dunes` -- long waves across
 *   the wind, loud; `patches` -- a semidesert, light ground with dark
 *   scrub in patches.
 * * `turf` -- the soft mottle of a wet meadow; `blades` -- a steppe, fine
 *   and combed one way by the wind; `tussocks` -- a savanna, the same
 *   grass with dark clumps of trees in it.
 * * `canopy` -- the crowns of a broadleaf wood; `groves` -- a dry
 *   woodland, the crowns larger and farther apart; `needles` -- the
 *   taiga, finer and darker; `jungle` -- the rainforest, the crowns
 *   largest and softest.
 * * `pools` -- a marsh, dark water standing in the low of the ground;
 *   `polygons` -- the tundra, a faint net of frost cracks.
 * * `rubble` -- the foothills, coarse speckle; `scree` -- the alpine
 *   slope, fine and sharp; `clinker` -- the cinder field, coarse and
 *   loud; `cracks` -- the ice field, thin dark lines in white.
 */
export const GRAIN_KINDS: Record<string, GrainKind> = {
  sand: { scale: 4, stretch: 0.5, contrast: 0.5, shape: GRAIN_SHAPE.plain },
  turf: { scale: 2, stretch: 1, contrast: 0.8, shape: GRAIN_SHAPE.plain },
  canopy: { scale: 2, stretch: 1, contrast: 1.0, shape: GRAIN_SHAPE.clumps },
  blades: { scale: 4, stretch: 0.5, contrast: 0.7, shape: GRAIN_SHAPE.plain },
  dunes: { scale: 1, stretch: 0.25, contrast: 1.1, shape: GRAIN_SHAPE.plain },
  needles: { scale: 3, stretch: 1, contrast: 1.2, shape: GRAIN_SHAPE.clumps },
  polygons: { scale: 2, stretch: 1, contrast: 0.9, shape: GRAIN_SHAPE.net },
  pools: { scale: 2, stretch: 1, contrast: 1.0, shape: GRAIN_SHAPE.pools },
  rubble: { scale: 2, stretch: 1, contrast: 1.2, shape: GRAIN_SHAPE.speckle },
  scree: { scale: 4, stretch: 1, contrast: 1.4, shape: GRAIN_SHAPE.speckle },
  cracks: { scale: 2, stretch: 1, contrast: 1.0, shape: GRAIN_SHAPE.cracks },
  clinker: { scale: 3, stretch: 1, contrast: 1.6, shape: GRAIN_SHAPE.speckle },
  jungle: { scale: 1, stretch: 1, contrast: 0.9, shape: GRAIN_SHAPE.clumps },
  tussocks: { scale: 3, stretch: 0.75, contrast: 0.9, shape: GRAIN_SHAPE.patches },
  patches: { scale: 2, stretch: 1, contrast: 0.8, shape: GRAIN_SHAPE.patches },
  groves: { scale: 1, stretch: 1, contrast: 0.9, shape: GRAIN_SHAPE.clumps },
};
/** The grain of a biome the vault has no word for, or a word the picture
 *  does not know: the soft mottle, as all ground was before. */
export const GRAIN_FALLBACK = "turf";

/** Four floats a slot -- scale, stretch, contrast, shape -- for the
 *  shader's `u_grains`, by the raster's biome code: the vault's word for
 *  each biome of the passport, looked up in `GRAIN_KINDS`. Slots past the
 *  passport's biomes carry the fallback. */
export function grainTable(
  words: Record<string, unknown> | null | undefined,
  biomes: readonly string[],
): Float32Array {
  const table = new Float32Array(PALETTE_SLOTS * 4);
  for (let slot = 0; slot < PALETTE_SLOTS; slot++) {
    const word = slot < biomes.length ? words?.[biomes[slot]] : undefined;
    const kind = (typeof word === "string" && GRAIN_KINDS[word]) || GRAIN_KINDS[GRAIN_FALLBACK];
    table[slot * 4] = kind.scale;
    table[slot * 4 + 1] = kind.stretch;
    table[slot * 4 + 2] = kind.contrast;
    table[slot * 4 + 3] = kind.shape;
  }
  return table;
}

/** What the octaves add up to when every one of them shows: the sum the
 *  grain is divided by, so its loudest is the same wherever one stands. */
export function grainWhole(): number {
  let whole = 0;
  let amp = 1;
  for (let o = 0; o < GRAIN_OCTAVES; o++) {
    whole += amp;
    amp *= GRAIN_FALL;
  }
  return whole;
}
/** How much of the tone the grain may take at its strongest. */
export const GRAIN_DEPTH = 0.15;
/** Over what width of a grain cell, in device pixels, the grain comes in:
 *  nothing under the first, whole from the second. Below a pixel a texture
 *  is not a texture but aliasing, and above a couple it is itself. */
export const GRAIN_SEEN_PX = 1.5;
export const GRAIN_FULL_PX = 4;

/** How strong the grain is where a cell of it is this many pixels wide. */
export function grainStrength(cellPx: number): number {
  if (!Number.isFinite(cellPx)) return 0;
  const span = GRAIN_FULL_PX - GRAIN_SEEN_PX;
  return Math.min(1, Math.max(0, (cellPx - GRAIN_SEEN_PX) / span));
}

/** How many cells a lattice repeats in. A lattice that counted to the
 *  planet's radius would be five figures long before it reached the ground,
 *  and the ground would get what the float had left -- which it did: a star
 *  of rays stood in the middle of the map, where the figures ran out first.
 *  Wrapped, and counted from the eye rather than from the planet's centre,
 *  a lattice coordinate is a few hundred and every figure of it is ground.
 *  The seam repeats every 512 cells, which no frame that draws is wide
 *  enough to reach. */
export const GRAIN_WRAP = 512;

/** Where the eye itself stands in a lattice of cells this big, wrapped into
 *  the first period. Reckoned here, where a number carries sixteen figures,
 *  and handed to the shader, where it would carry seven: this is the whole
 *  of the trick that keeps the grain steady under the eye. */
export function latticeAt(
  lat: number,
  lon: number,
  radiusM: number,
  cellM: number,
): [number, number, number] {
  const scale = radiusM / cellM;
  const up: [number, number, number] = [
    Math.cos(lat) * Math.cos(lon),
    Math.cos(lat) * Math.sin(lon),
    Math.sin(lat),
  ];
  return up.map((one) => {
    const at = one * scale;
    return at - Math.floor(at / GRAIN_WRAP) * GRAIN_WRAP;
  }) as [number, number, number];
}

/** What the value noise is multiplied by to fill -1..1: a blend of eight
 *  uniform draws heaps about its middle, and untouched it swings a tenth of
 *  the way -- a texture nobody would see. Measured, not guessed: the spread
 *  of the blend is about a seventh of the range. */
export const NOISE_GAIN = 3;
