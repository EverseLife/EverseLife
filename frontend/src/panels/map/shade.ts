// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The shader that draws the ground (landscape plan wave 5, §9.4 level A):
 * a full-screen quad under the SVG, and for every pixel the projection run
 * backwards -- `geoUnder` in GLSL -- to a point of the sphere, the height,
 * the biome and the landform read there off the field's rasters, and the
 * light laid on the height's slope.
 *
 * What the shader decides is the picture only (plan §9.1, rule two): the
 * tone is the relief, the colour is the biome, a cliff is a cut of shade.
 * Whether a point is land, and whether one may aim there, stays the
 * server's and the tiles' -- nothing here is asked by the scout.
 *
 * The GLSL is a string so that the pure parts -- the palette read off the
 * theme, the mip chain of the height -- can be tested without a GPU. The
 * shader itself is checked by eye (plan §9.9).
 */

import type { RasterPassport } from "../../api";
import type { Geo } from "./globe";

/** The largest palette the shader holds: sixteen biomes today, room for more. */
export const PALETTE_SLOTS = 32;
/** The raster's word for water, which has no biome. */
export const NO_BIOME = 255;


/**
 * Where the light comes from: north-west, forty-five degrees up -- the
 * convention of every topographic map, and the debug render's too
 * (`tools/field/render.py`), so the two hillshades agree.
 */
export const SUN_AZIMUTH_DEG = 315;
export const SUN_ALTITUDE_DEG = 45;
/** The slope is drawn steeper than it is: at hundreds of kilometres a
 *  frame, a rise of seven hundred metres is nothing to the eye without it. */
export const EXAGGERATION = 2;

/** How much of the light the ground keeps in full shadow of its own slope:
 *  the hillshade runs from this to one. */
export const AMBIENT = 0.5;

/** The sun in the shader (owner, 2026-09-12: the shadow is to play with the
 *  relief, not darken a region). The night used to be a flat path laid
 *  over the map in SVG with a hard edge; now the subsolar point goes to the
 *  shader and every pixel knows how high its sun stands.
 *
 *  The relief is lit from the sun's own side, but never from straight
 *  overhead -- at noon every slope would read alike -- so for the shading
 *  the sun is held at least this high, degrees. The cast shadow takes the
 *  true height. */
export const LIGHT_ALT_MIN_DEG = 28;
/** The terminator's width, as the sine of the sun's height: the day's tone
 *  fades to the night's over twice this, and there is no edge. */
export const TWILIGHT = 0.12;
/** What the night does to a colour: the ground keeps a third of its light
 *  and turns toward the blue of a night sky, so the relief still reads. */
export const NIGHT_TINT: readonly [number, number, number] = [0.32, 0.4, 0.62];
/** The cast shadow: so many steps back along the ground toward the sun,
 *  each twice the last, the first a cell (or a pixel's ground on the far
 *  frames). Nine reach two hundred and fifty-six cells -- thirteen
 *  kilometres at fifty metres a cell -- which is the shadow of a ridge at
 *  the edge of the day, where the sun stands SHADOW_ALT_MIN_DEG high and a
 *  rise of three hundred metres shades ten kilometres of ground. Five
 *  reached sixteen, and every long shadow ended at a wall sixteen cells
 *  from what cast it (owner, 2026-09-12: at the edge of day and night the
 *  big shadows are cut off). */
export const SHADOW_STEPS = 9;
/** From this step on the march reads the height a level coarser with every
 *  step: a ridge kilometres off shades by its silhouette, not by its
 *  cells, and the coarser level is that silhouette -- and a fetch that is
 *  likely in the cache, so the four steps added cost little. */
export const SHADOW_COARSE_FROM = 3;
/** The sun's height the shadow is cast at when it stands lower, degrees:
 *  at the horizon itself a shadow would be endless. */
export const SHADOW_ALT_MIN_DEG = 4;
/** The penumbra: the sun is a disc, not a point, and ground that only just
 *  tops the ray hides only part of it. This is the disc's width as the
 *  shadow draws it, radians -- three times the real half degree, so the
 *  soft edge is seen at all at the frames the map is looked at. The edge
 *  of a shadow is this share of the distance to what casts it: sharp under
 *  a near bank, soft a valley away, as a shadow is (owner, 2026-09-12: the
 *  shadow's darkness and blur are to be worked out, not only its reach). */
export const SHADOW_SOFT = 0.03;
/** How much of its light the ground loses in a full cast shadow with the
 *  sun well up... */
export const SHADOW_DEPTH = 0.45;
/** ...and the share of that left with the sun on the horizon: the low
 *  sun's light comes through more air and the sky lights what the sun does
 *  not reach, so the long shadow of the evening is a paler one than the
 *  short shadow of noon. The whole depth from SHADOW_FULL_SIN of the sun's
 *  height up (the sine; 0.35 is twenty degrees). */
export const SHADOW_LOW = 0.6;
export const SHADOW_FULL_SIN = 0.35;
/** The lie of the land: the height read this many levels coarser is the
 *  mean of the ground about the point, and the point's height over or
 *  under it is the valley or the ridge as a whole. */
export const RELIEF_LEVELS = 3;
/** Over or under the mean by this many metres is a whole valley or crest. */
export const RELIEF_M = 80;
/** What a whole valley or crest does to the tone. */
export const RELIEF_DEPTH = 0.12;
/** The layers of the map (D-331, owner 2026-09-12): what the ground is
 *  coloured by. `terrain` is the map as it is -- the biome's colour under
 *  the relief's light; `relief` the height alone, a hypsometric ramp under
 *  the same light; `biomes` the biome's colour flat, a legend; `temperature`
 *  and `rain` the climate's two rasters on their ramps; `moisture` the
 *  soil's -- how slowly a bed dries here, by the drying law of D-296, for
 *  the farmer (owner, 2026-09-12: the water layer was to be the soil's
 *  moisture and not the water alone). The order is the shader's `u_layer`. */
export const LAYERS = ["terrain", "relief", "biomes", "temperature", "rain", "moisture"] as const;
export type Layer = (typeof LAYERS)[number];

/** The legends' light (D-331): a layer that is read keeps this much of the
 *  ground's tone flat and takes the rest from the relief, so the shape
 *  still shows under a flat colour and the colour still reads as itself. */
export const LEGEND_AMBIENT = 0.6;
/** How much darker a legend's colour goes under water, so the water shows
 *  through a climate's ramp without a colour of its own. */
export const LEGEND_WET_DIM = 0.75;
/** How sharply the moisture layer's water comes up from the wet share of
 *  the pixel: past two-thirds wet the pixel is water outright. */
export const LEGEND_WET_EDGE = 1.5;
/** On the far frames the water on the land takes this share of the deep
 *  tone: a river drawn as a line reads by being darker than the ground. */
export const FAR_WATER_DEEP = 0.4;

/** A ramp: colours at shares of the way, nought to one, the way a climate
 *  map or a hypsometric one has always been drawn. One table for the
 *  shader (`glslRamp`) and the legend in the corner (`cssRamp`), so the
 *  bar and the ground under it cannot come apart. */
export type Stop = { at: number; rgb: readonly [number, number, number] };
export const RAMPS = {
  /** Cold blue to white to warm yellow to hot red. */
  temperature: [
    { at: 0, rgb: [0.16, 0.3, 0.7] },
    { at: 0.33, rgb: [0.75, 0.85, 0.95] },
    { at: 0.66, rgb: [0.95, 0.8, 0.3] },
    { at: 1, rgb: [0.75, 0.15, 0.1] },
  ],
  /** Dry sand to the teal of a damp land to the blue of a wet one. */
  rain: [
    { at: 0, rgb: [0.85, 0.75, 0.5] },
    { at: 0.5, rgb: [0.45, 0.63, 0.62] },
    { at: 1, rgb: [0.12, 0.3, 0.6] },
  ],
  /** The soil: pale where it dries in a day, the green of a damp ground,
   *  the dark of a ground that holds its water. Not the rain's blues, so
   *  the two layers are told apart at a glance. */
  moisture: [
    { at: 0, rgb: [0.84, 0.72, 0.5] },
    { at: 0.5, rgb: [0.5, 0.62, 0.34] },
    { at: 1, rgb: [0.1, 0.36, 0.34] },
  ],
  /** The land from the brown of the soil through ochre to the grey of the
   *  stone and the white of the summits -- no green anywhere the ground is
   *  not green: the height is the rock's, and green means growth alone
   *  (D-329 p. 20). */
  height: [
    { at: 0, rgb: [0.56, 0.47, 0.34] },
    { at: 0.3, rgb: [0.78, 0.68, 0.45] },
    { at: 0.7, rgb: [0.6, 0.57, 0.53] },
    { at: 1, rgb: [0.95, 0.95, 0.95] },
  ],
} as const satisfies Record<string, readonly Stop[]>;

/** The ramp as a GLSL function of the share, the stops blended pairwise. */
export function glslRamp(name: string, stops: readonly Stop[]): string {
  const vec = (rgb: readonly [number, number, number]) =>
    `vec3(${rgb.map((v) => v.toFixed(2)).join(", ")})`;
  const legs: string[] = [];
  for (let i = 0; i + 1 < stops.length; i++) {
    const a = stops[i];
    const b = stops[i + 1];
    const leg = `mix(${vec(a.rgb)}, ${vec(b.rgb)}, (share - ${a.at.toFixed(2)}) / ${(b.at - a.at).toFixed(2)})`;
    legs.push(i + 2 < stops.length ? `share < ${b.at.toFixed(2)} ? ${leg}` : leg);
  }
  return `vec3 ${name}(float share) {\n  return ${legs.join("\n    : ")};\n}`;
}

/** The ramp as a CSS gradient, left to right, for the legend. */
export function cssRamp(stops: readonly Stop[]): string {
  const at = (s: Stop) =>
    `rgb(${s.rgb.map((v) => Math.round(v * 255)).join(" ")}) ${Math.round(s.at * 100)}%`;
  return `linear-gradient(to right, ${stops.map(at).join(", ")})`;
}

/** The drying law of a bed (D-296, `farm.life.dry_rate`) as the moisture
 *  layer reads it off the book of constants (D-225: the client derives what
 *  it can): what share of the drying the rain closes at the wettest
 *  (`site.rain_water_offset`), what share is left beside water
 *  (`farm.river_dry_share`), how much a degree over the reference adds
 *  (`farm.dry_per_degree`, `farm.dry_temp_ref`), and how near water has to
 *  be to count (`terrain.river_reach_km`). Without the book there is no
 *  law: the offsets are nought and the layer is one moisture everywhere,
 *  and the picture says so rather than guessing. */
export type DryLaw = { offset: number; share: number; perDegree: number; ref: number; reachM: number };
export function dryLaw(constants: Record<string, unknown> | null | undefined): DryLaw {
  const num = (key: string, fallback: number) => {
    const v = Number(constants?.[key]);
    return Number.isFinite(v) ? v : fallback;
  };
  return {
    offset: num("site.rain_water_offset", 0) / 100,
    share: num("farm.river_dry_share", 100) / 100,
    perDegree: num("farm.dry_per_degree", 0) / 100,
    ref: num("farm.dry_temp_ref", 0),
    reachM: num("terrain.river_reach_km", 0) * 1000,
  };
}

/** The moisture layer's number for a point, as the shader works it out
 *  (`fragment.ts`, the soil's moisture): the pace a bed dries at by the
 *  law of D-296 -- the heat over the reference, the rain's share closed,
 *  the share left beside water -- against the fastest the planet has,
 *  bare ground at its hottest, and one minus that. Kept beside the GLSL so
 *  a test can hold the two to the engine's numbers; the GLSL cannot run
 *  in a test. `beside` is nought to one: one within the reach of fresh
 *  water by the river raster, nought a cell past it, as the shader reads
 *  it. */
export function moistureOf(
  law: DryLaw,
  tC: number,
  rain01: number,
  beside: number,
  hotC: number,
): number {
  const heat = Math.max(0, 1 + law.perDegree * (tC - law.ref));
  const pace = heat * (1 - law.offset * rain01) * (1 + (law.share - 1) * beside);
  const fastest = Math.max(1 + law.perDegree * (hotC - law.ref), 0.05);
  return Math.min(1, Math.max(0, 1 - pace / fastest));
}


/** The rivers on the far frames: a river narrower than a pixel is drawn as
 *  the share of the pixel it wets, put through a ramp -- nothing under
 *  RIVER_FAINT, a full line from RIVER_FULL. The ramp is what keeps a line
 *  a line: the hardware's blend between texels lays a halo of small shares
 *  a texel wide on either side, and drawn as they were the halo made every
 *  river a band four pixels wide (2026-09-12). Lakes and the sea keep their
 *  share: they are areas, and a halo on an area is its shore. */
export const RIVER_FAINT = 0.12;
export const RIVER_FULL = 0.5;
/** The water at night keeps this much of its light on top of the night's
 *  tint: water is darker than the land in the dark, and drawn with the
 *  lake's own light tone the rivers glowed on the night side. */
export const NIGHT_WATER = 0.55;

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
 *  **A cell is eight metres of ground and stays eight metres at every
 *  zoom.** It followed the pixel once, stepping by octaves so as to stay
 *  the same size on the glass, and that was wrong for a reason no measure
 *  would have caught: the ground then changes when the hand zooms, and a
 *  map whose country is rearranged by looking closer is not a map (owner,
 *  2026-09-09). What the zoom may change is only whether the grain can be
 *  seen at all -- and that it must, or a texture finer than a pixel turns
 *  into a shimmer of noise. */
export const GRAIN_M = 32;
/** How many octaves of it there are, each half the last, and how much
 *  quieter each finer one is. Five from thirty-two metres reach down to
 *  two: the ground is one fixed texture with detail at many sizes, as real
 *  ground is, so coming closer **uncovers** the fine detail instead of
 *  rearranging the coarse -- which is the whole of what the owner asked
 *  for. An octave is drawn only where its own cell is worth pixels, so
 *  none of them is ever aliasing. */
export const GRAIN_OCTAVES = 5;
export const GRAIN_FALL = 0.6;

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

/** How far the biome is read astray of the pixel, in cells of the raster,
 *  and over what length of ground that wander waves. Both are metres of the
 *  ground and neither follows the frame, for the reason the grain's size
 *  does not: the edge between two biomes is one definite line of the
 *  country, and a line that is redrawn when the hand zooms reads as the
 *  country itself changing.
 *
 *  The numbers are set against what they hide: one straight edge of a cell.
 *  On the equal-area grid (D-328) a cell is a diamond and shows the eye its
 *  **diagonal** -- a hundred and forty metres of unbroken line at forty-five
 *  degrees, which reads as a line somebody drew rather than as a step. A
 *  wander has to be worth about that whole edge to break it: three fifths of
 *  a cell softened the teeth and left them countable.
 *
 *  The amplitude is in cells and moved by itself when the planets shrank;
 *  the wavelength is in metres and did not, so it was divided by the same
 *  four (260 -> 65). Left alone it would have been two and a half cells of
 *  the picture long, and a wander longer than the teeth it hides does not
 *  break the staircase -- it carries the whole staircase sideways, which is
 *  the very look the constant exists to remove. */
export const EDGE_CELLS = 2.0;
/** How far apart the four taps of a pixel stand on the glass, pixels. The
 *  class of the ground is picked, never averaged (plan §9.3) -- but the
 *  **colour** of a pixel is the mean of four picks on a rotated grid this
 *  wide, and so is its wetness. Picked once at the pixel's own point, a
 *  cell was a diamond -- the grid's own shape on the screen -- and every
 *  boundary of biomes, every shore of a lake, was a row of them at any
 *  frame where a cell is a few pixels (owner, 2026-09-11: diamonds on the
 *  map, twice). Four taps are the edge anti-aliased; the wander (EDGE_CELLS)
 *  is what breaks its straightness. */
export const AA_PX = 3;
/** The share of a water raster at which the edge of the water is cut
 *  between the cells: a lake's share of the cell, a river's ribbon. The
 *  vault writes the ribbon so that it falls to this at the bank
 *  (`pipeline.ribbon`); the pair meets here and in the vault's
 *  `test_the_river_ribbon_is_half_gone_at_its_bank`. */
export const BANK_SHARE = 0.5;

/** The Catmull-Rom weights of the four texels about a place `t` of the
 *  way from one texel centre to the next, as polynomials in `t`: one row
 *  per texel, the coefficients of 1, t, t^2, t^3. The one table serves
 *  both readers -- `catmullRom` here and the shader's `cubicWeights`,
 *  written from it -- so a test on the one holds the other. */
export const CATMULL_ROM: readonly (readonly [number, number, number, number])[] = [
  [0, -0.5, 1, -0.5],
  [1, 0, -2.5, 1.5],
  [0, 0.5, 2, -1.5],
  [0, 0, -0.5, 0.5],
];

export function catmullRom(t: number): [number, number, number, number] {
  const t2 = t * t;
  const t3 = t2 * t;
  return CATMULL_ROM.map(([c0, c1, c2, c3]) => c0 + c1 * t + c2 * t2 + c3 * t3) as [
    number, number, number, number,
  ];
}

export const glslWeight = ([c0, c1, c2, c3]: readonly [number, number, number, number]): string =>
  `${c0.toFixed(1)} + ${c1.toFixed(1)} * t + ${c2.toFixed(1)} * t2 + ${c3.toFixed(1)} * t3`;
export const EDGE_M = 65;
/** And the same two pixel widths for it: a wander finer than a pixel is
 *  not a rough edge but salt and pepper, because neighbouring pixels then
 *  read the wander at points too far apart to be alike. */
export const EDGE_SEEN_PX = 2;
export const EDGE_FULL_PX = 6;

/** How much of the wander is drawn where its own wave is this many pixels. */
export function edgeStrength(wavePx: number): number {
  if (!Number.isFinite(wavePx)) return 0;
  return Math.min(1, Math.max(0, (wavePx - EDGE_SEEN_PX) / (EDGE_FULL_PX - EDGE_SEEN_PX)));
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
//: The GLSL itself lives in `fragment.ts`, written from the constants above.


/** The subsolar point as a direction on the unit ball, the way the shader
 *  places every point of the sphere: x along the prime meridian, z to the
 *  north pole. */
export function sunVector(sun: Geo): [number, number, number] {
  const lat = (sun.lat * Math.PI) / 180;
  const lon = (sun.lon * Math.PI) / 180;
  return [Math.cos(lat) * Math.cos(lon), Math.cos(lat) * Math.sin(lon), Math.sin(lat)];
}

/** The sun's direction as the shader takes it: east, north, up. */
export function sunDirection(azimuthDeg = SUN_AZIMUTH_DEG, altitudeDeg = SUN_ALTITUDE_DEG): [number, number, number] {
  const az = (azimuthDeg * Math.PI) / 180;
  const alt = (altitudeDeg * Math.PI) / 180;
  return [Math.cos(alt) * Math.sin(az), Math.cos(alt) * Math.cos(az), Math.sin(alt)];
}

/**
 * A CSS colour as the browser computes it, to red, green, blue in 0..1.
 * `rgb(...)` and `rgba(...)` with numbers or percentages, `color(srgb ...)`
 * as `color-mix()` and `light-dark()` come back; nothing else, and nothing
 * for a colour that is not one.
 */
export function parseColor(text: string): [number, number, number] | null {
  const value = text.trim();
  const rgb = /^rgba?\(\s*([^)]+)\)$/i.exec(value);
  if (rgb) {
    const parts = rgb[1].split(/[\s,/]+/).filter(Boolean).slice(0, 3);
    if (parts.length !== 3) return null;
    const out = parts.map((part) =>
      part.endsWith("%") ? Number(part.slice(0, -1)) / 100 : Number(part) / 255,
    );
    return out.every((v) => Number.isFinite(v)) ? [out[0], out[1], out[2]] : null;
  }
  const srgb = /^color\(\s*srgb\s+([^)]+)\)$/i.exec(value);
  if (srgb) {
    const parts = srgb[1].split(/[\s/]+/).filter(Boolean).slice(0, 3);
    if (parts.length !== 3) return null;
    const out = parts.map((part) =>
      part.endsWith("%") ? Number(part.slice(0, -1)) / 100 : Number(part),
    );
    return out.every((v) => Number.isFinite(v)) ? [out[0], out[1], out[2]] : null;
  }
  return null;
}

/** The colours the shader is given, all read off the theme (plan §9.3: the
 *  palette comes as uniforms from the theme's variables, or the light theme
 *  falls apart). */
export type Palette = {
  /** Three floats a slot, `PALETTE_SLOTS` slots, by the raster's biome code. */
  biomes: Float32Array;
  seaDeep: [number, number, number];
  lake: [number, number, number];
  high: [number, number, number];
};

/** What is asked of the theme for the fluid and the heights, as the map's
 *  own tones are spelt in `map.css`. The SVG ground answers the same
 *  question there in its own rules (`.ground[data-fluid]`) rather than
 *  through these expressions: the two paths agree on the colour of a sea
 *  because they are given the same recipe, not because they share a token.
 *
 *  A set per fluid (`RasterPassport.fluid`). Lava is not water in another
 *  hue: water darkens with depth because light stops reaching down it, and
 *  lava brightens because the light is coming out of it. Mixed towards the
 *  page's own dark the way the sea is, a lava ocean came out a muddy brown
 *  and read as a mud flat -- which is what Pyroxis looked like before the
 *  planets were told apart at all. */
export const FLUID_TONES = {
  water: {
    seaDeep: "var(--gl-sea-deep)",
    lake: "var(--gl-lake)",
  },
  lava: {
    seaDeep: "var(--gl-lava-deep)",
    lake: "var(--gl-lava-lake)",
  },
} as const;
export const TONES = {
  ...FLUID_TONES.water,
  high: "var(--gl-high)",
} as const;

/** The magenta that says a token is missing: it must never look like land. */
export const MISSING: [number, number, number] = [1, 0, 1];

/**
 * The palette read off the page: a probe element is coloured by each
 * expression in turn and its computed colour read back, so `color-mix()`
 * and `light-dark()` resolve as the browser resolves them.
 */
export function paletteOf(
  probe: HTMLElement,
  planet: string,
  biomes: readonly string[],
  fluid: keyof typeof FLUID_TONES = "water",
  computed: (el: HTMLElement) => string = (el) => getComputedStyle(el).color,
): Palette {
  probe.style.setProperty("--pc", `var(--planet-${planet})`);
  const read = (expression: string): [number, number, number] => {
    probe.style.color = expression;
    return parseColor(computed(probe)) ?? MISSING;
  };
  const table = new Float32Array(PALETTE_SLOTS * 3);
  for (let i = 0; i < PALETTE_SLOTS; i++) {
    const name = biomes[i];
    const [r, g, b] = name ? read(`var(--biome-${name}, magenta)`) : MISSING;
    table[i * 3] = r;
    table[i * 3 + 1] = g;
    table[i * 3 + 2] = b;
  }
  //: A fluid the client has not heard of falls back to water rather than to
  //: magenta: an unknown word is a vault ahead of this build, and a blue sea
  //: is a better wrong answer than a hole in the map.
  const tones = FLUID_TONES[fluid] ?? FLUID_TONES.water;
  return {
    biomes: table,
    seaDeep: read(tones.seaDeep),
    lake: read(tones.lake),
    high: read(TONES.high),
  };
}

/** The form codes the shader tells cliffs and the grain's kinds by, off the
 *  passport's table; a form the table lacks is a code no cell carries. Water
 *  is not among them: the shader tells the sea by the height's sign, a lake
 *  by its share and a river by its ribbon (u_wet, u_stream), never by the
 *  form. */
export function formCodes(passport: RasterPassport): {
  /** What the grain of a cell is made of: bare rock, loose sand, ice
   *  (wave 8). A landform in none of the three mottles as ground does. */
  stone: [number, number, number, number];
  sand: [number, number];
  ice: [number, number, number];
  /** The biome the shore's last strip is painted as: the coast's; -1 where
   *  the table has no coast, and the shader paints the missing magenta. */
  shore: number;
} {
  const code = (name: string): number => {
    const at = passport.forms.indexOf(name);
    return at < 0 ? NO_BIOME : at;
  };
  return {
    stone: [code("scree"), code("rocky_desert"), code("cliff"), code("coast_cliff")],
    sand: [code("dunes"), code("beach")],
    ice: [code("ice"), code("glacial"), code("fjord")],
    shore: passport.biomes.indexOf("coast"),
  };
}

/** The heights as the texture wants them: metres as floats, from the
 *  raster's signed sixteen-bit steps of `unit` metres (the passport's
 *  `height_unit_m`, a decimetre). */
export function heightsOf(bytes: ArrayBuffer, unit: number): Float32Array {
  const steps = new Int16Array(bytes);
  const out = new Float32Array(steps.length);
  for (let i = 0; i < steps.length; i++) out[i] = steps[i] * unit;
  return out;
}

/** How deep the sea goes on this planet, metres, off the raster itself: the
 *  shade of the water runs from the shore to this, and it is the field's
 *  number, not one copied from the pipeline. At least a metre. */
export function deepOf(heights: Float32Array): number {
  let deepest = 0;
  for (let i = 0; i < heights.length; i++) if (heights[i] < deepest) deepest = heights[i];
  return Math.max(1, -deepest);
}

/**
 * The mip chain of a height raster: each level the mean of two by two of
 * the one above, the odd last row or column folded in. Built here rather
 * than by `generateMipmap`, which WebGL2 does not promise for a half-float
 * red texture without an extension; and so that a far frame reads the mean
 * height of a region, not one cell of it.
 *
 * `tile` is the side of one face of the atlas, borders counted, and the
 * chain stops where a face would stop being a whole number of texels. The
 * face is a power of two (`terrain.raster_nside`) so every level down to
 * one texel a face halves it evenly, and a texel of a coarse level is
 * always a mean of one face's own ground. Past that a texel would be a
 * mixture of faces from opposite sides of the planet -- and the whole globe
 * is a few pixels there anyway.
 */
export function mipChain(
  level0: Float32Array,
  cols: number,
  rows: number,
  tile = 0,
): { data: Float32Array; cols: number; rows: number }[] {
  const deepest = tile > 1 ? Math.floor(Math.log2(tile)) : Infinity;
  const chain = [{ data: level0, cols, rows }];
  let { data, cols: w, rows: h } = chain[0];
  while ((w > 1 || h > 1) && chain.length <= deepest) {
    const w2 = Math.max(1, Math.floor(w / 2));
    const h2 = Math.max(1, Math.floor(h / 2));
    const next = new Float32Array(w2 * h2);
    for (let y = 0; y < h2; y++) {
      const y0 = Math.min(h - 1, 2 * y);
      const y1 = Math.min(h - 1, 2 * y + 1);
      for (let x = 0; x < w2; x++) {
        const x0 = Math.min(w - 1, 2 * x);
        const x1 = Math.min(w - 1, 2 * x + 1);
        next[y * w2 + x] =
          (data[y0 * w + x0] + data[y0 * w + x1] + data[y1 * w + x0] + data[y1 * w + x1]) / 4;
      }
    }
    chain.push({ data: next, cols: w2, rows: h2 });
    data = next;
    w = w2;
    h = h2;
  }
  return chain;
}

/**
 * The mip chain of a **byte** raster, the same halving as the height's.
 *
 * Two reducers, because two kinds of byte travel in these rasters and they
 * cannot be coarsened the same way.
 *
 * * `mean` is for a **share** -- how much of the cell is lake, river, hard
 *   rock. Half of a half is a quarter, and the mean says so.
 * * `cut` is for a **measure cut at the bank** -- the river's ribbon, a
 *   distance to the channel on a ramp with the bank at `BANK_SHARE`. Its
 *   mean is not a share of water; the first halving cuts every byte at the
 *   bank to nought or all and takes the mean of that, and the levels above
 *   are the mean of the cut: how much of the texel is river. So the far
 *   frames read a river as the share of the pixel it wets, and a river
 *   narrower than a pixel is a line and not a row of dashes (owner,
 *   2026-09-12: the rivers break).
 * * `pick` is for a **class** -- which biome, which landform. A mean of two
 *   codes is a third code that means something else entirely; the coarse
 *   texel takes one of its four instead, and the one it takes is the first,
 *   so the choice is the same on every machine.
 *
 * Why at all: without a chain a pixel that covers ten texels reads one of
 * them and shimmers as the hand moves -- a one-texel river blinking in and
 * out on the globe, a coast fizzing along its whole length. The height had
 * its chain from the first day (`mipChain`); the rasters beside it did not,
 * and that was the whole of the noise on the far frames.
 */
export function byteChain(
  level0: Uint8Array,
  cols: number,
  rows: number,
  how: "mean" | "pick" | "cut",
  tile = 0,
): { data: Uint8Array; cols: number; rows: number }[] {
  const deepest = tile > 1 ? Math.floor(Math.log2(tile)) : Infinity;
  const chain = [{ data: level0, cols, rows }];
  let { data, cols: w, rows: h } = chain[0];
  const bank = BANK_SHARE * 255;
  while ((w > 1 || h > 1) && chain.length <= deepest) {
    const w2 = Math.max(1, Math.floor(w / 2));
    const h2 = Math.max(1, Math.floor(h / 2));
    const next = new Uint8Array(w2 * h2);
    //: The cut happens once, on the finest level: above it the bytes are
    //: shares already and average as shares do.
    const read =
      how === "cut" && chain.length === 1 ? (v: number) => (v > bank ? 255 : 0) : (v: number) => v;
    for (let y = 0; y < h2; y++) {
      const y0 = Math.min(h - 1, 2 * y);
      const y1 = Math.min(h - 1, 2 * y + 1);
      for (let x = 0; x < w2; x++) {
        const x0 = Math.min(w - 1, 2 * x);
        const x1 = Math.min(w - 1, 2 * x + 1);
        next[y * w2 + x] =
          how === "pick"
            ? data[y0 * w + x0]
            : Math.round(
                (read(data[y0 * w + x0]) + read(data[y0 * w + x1]) + read(data[y1 * w + x0]) + read(data[y1 * w + x1])) / 4,
              );
      }
    }
    chain.push({ data: next, cols: w2, rows: h2 });
    data = next;
    w = w2;
    h = h2;
  }
  return chain;
}

/** Whether this browser can draw the shaded ground at all: WebGL2 for the
 *  shader and `light-dark()` for the palette's tokens -- a browser without
 *  the second would read every biome as the magenta of a missing token.
 *  Asked once; the probe's context is given straight back. Without either
 *  the SVG ground stands, as the plan keeps it (§9.3). */
let shaded: boolean | null = null;
export function supportsShadedGround(): boolean {
  if (shaded === null) {
    try {
      const probe = document.createElement("canvas");
      const gl = probe.getContext("webgl2");
      gl?.getExtension("WEBGL_lose_context")?.loseContext();
      shaded = Boolean(gl) && CSS.supports("color", "light-dark(#000, #fff)");
    } catch {
      shaded = false;
    }
  }
  return shaded;
}
