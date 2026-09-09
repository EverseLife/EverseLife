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
import { CLOSE_FRAME_M } from "./contours";
import { UNITS_PER_METRE } from "./globe";

/** The largest palette the shader holds: sixteen biomes today, room for more. */
export const PALETTE_SLOTS = 32;
/** The raster's word for water, which has no biome. */
export const NO_BIOME = 255;

export const VERTEX = `#version 300 es
in vec2 a_pos;
void main() { gl_Position = vec4(a_pos, 0.0, 1.0); }
`;

/**
 * Where the light comes from: north-west, forty-five degrees up -- the
 * convention of every topographic map, and the debug render's too
 * (`tools/field/render.py`), so the two hillshades agree.
 */
export const SUN_AZIMUTH_DEG = 315;
export const SUN_ALTITUDE_DEG = 45;
/** The slope is drawn steeper than it is: at a thousand kilometres a
 *  frame, a three-kilometre rise is nothing to the eye without it. */
export const EXAGGERATION = 2;

/** The grain of the ground (landscape plan wave 8, §9.5): the texture that
 *  says what one is standing on -- stone, sand, ice, turf -- laid over the
 *  hillshade on the near frames.
 *
 *  It decides nothing, and it is not the facet drawn (§9.2: "зерно от
 *  шейдера ничего не решает... ему разрешено быть просто красивым"). The
 *  face a find wears is the engine's (`engine/facet.py`), off the field's
 *  own numbers; what the eye sees here is the same ground's character read
 *  off the landform and the rock -- so the two agree in kind without the
 *  shader recomputing a choice it must not make.
 *
 *  A grain cell is metres of the surface, not of the screen: it holds still
 *  under a drag and grows under a zoom, as the ground does. How many metres
 *  is the frame's business (`grainMetres`) -- one size cannot serve a frame
 *  four hundred metres across and one four kilometres across, and a texture
 *  read at the wrong size is either four blobs or grey noise. */
/** What a grain cell measures at its finest and its coarsest, metres: the
 *  stone under a boot, and the patch a facet is (`biome.facet_axes.wave_m`
 *  is 200 m) -- past that it would be a landform, and the shape of the
 *  ground is the hillshade's to tell. */
export const GRAIN_MIN_M = 2;
export const GRAIN_MAX_M = 200;
/** How wide a grain cell is drawn, in device pixels: fewer and the texture
 *  is noise, more and it is a handful of blobs. Pixels rather than a share
 *  of the frame, because the frame the ladder speaks of (`frameMetres`) is
 *  how far the ground layer reaches, not how much of it the eye sees --
 *  what a metre measures on screen is `u_units`, and nothing else. */
export const GRAIN_PX = 28;
/** From what frame width the grain shows at all, and where it is whole.
 *  It fades in exactly where the vector lines begin (`CLOSE_FRAME_M`): the
 *  same step of the ladder, so one frame does not gain a texture while
 *  another gains its lines. On the planet's disk a texture of forty metres
 *  is a screen of noise, and there is none. */
export const GRAIN_FROM_M = CLOSE_FRAME_M;
export const GRAIN_FULL_M = 4_000;
/** How much of the tone the grain may take at its strongest. */
export const GRAIN_DEPTH = 0.22;
/** How many grain cells the lattice repeats in. The lattice counts to the
 *  planet's radius -- some hundred and sixty thousand cells across Terra --
 *  and a hash of numbers that large loses its chaos to the float's seven
 *  digits. Wrapped, the texture repeats every twenty kilometres: four times
 *  the widest frame that draws it, so no eye sees the seam. */
export const GRAIN_WRAP = 512;
/** What the value noise is multiplied by to fill -1..1: a blend of eight
 *  uniform draws heaps about its middle, and untouched it swings a tenth of
 *  the way -- a texture nobody would see. Measured, not guessed: the spread
 *  of the blend is about a seventh of the range. */
export const NOISE_GAIN = 3;
/** How far the biome may be read astray of the pixel, in cells of the
 *  raster, and over what length of ground that wander itself waves. Under a
 *  cell, so no biome moves anywhere -- the edge between two of them stops
 *  being a straight staircase and becomes a line the ground could have
 *  drawn. */
export const EDGE_CELLS = 0.8;
export const EDGE_M = 300;
/** And never further than this share of what is on screen. A cell is five
 *  hundred metres, so eight tenths of one is four hundred: on the node's
 *  frame, which is two hundred metres across, that would not roughen an
 *  edge -- it would read the biome of somewhere else for every pixel at
 *  once, and the whole ground would come out the colour of a neighbour the
 *  inspector does not name. */
export const EDGE_SHARE = 0.05;
/** And nothing at all until a cell is this many pixels wide: under that the
 *  class changes inside a pixel and there is no staircase to break. */
export const EDGE_SEEN_PX = 3;

/**
 * How far the biome is read astray on this frame, in cells of the raster.
 *
 * The roughening is for one thing only -- a cell edge that is a **line on
 * screen** -- and it fades out at both ends of that. Far out a cell is
 * under a pixel and there is nothing to break; near in a cell is wider than
 * the frame and there is no edge on screen at all. It rides its own ramp
 * rather than the grain's, because the frames where a staircase shows worst
 * are the ones where the grain has barely come on.
 */
export function edgeCells(frameM: number, stepM: number, framePx: number): number {
  if (!(frameM > 0) || !(stepM > 0) || !(framePx > 0)) return 0;
  const cellPx = (stepM * framePx) / frameM;
  const seen = Math.min(1, Math.max(0, (cellPx - 1) / (EDGE_SEEN_PX - 1)));
  return seen * Math.min(EDGE_CELLS, (frameM * EDGE_SHARE) / stepM);
}

/** How strong the grain is at a frame of this width, 0..1. */
export function grainStrength(frameM: number): number {
  if (!Number.isFinite(frameM)) return 0;
  return Math.min(1, Math.max(0, (GRAIN_FROM_M - frameM) / (GRAIN_FROM_M - GRAIN_FULL_M)));
}

/**
 * How many metres a grain cell measures when a device pixel measures this
 * many: about `GRAIN_PX` pixels of it, rounded to a power of two and held
 * between the boot and the facet's patch.
 *
 * Powers of two so that the texture does not breathe: a size that followed
 * the zoom smoothly would crawl over the ground all the way in, and one
 * that steps at octaves stands still through most of a zoom and re-reads
 * itself at a stroke, which the eye takes for coming closer.
 */
export function grainMetres(metresPerPixel: number): number {
  if (!Number.isFinite(metresPerPixel) || metresPerPixel <= 0) return GRAIN_MAX_M;
  const octave = Math.pow(2, Math.round(Math.log2(metresPerPixel * GRAIN_PX)));
  return Math.min(GRAIN_MAX_M, Math.max(GRAIN_MIN_M, octave));
}

export const FRAGMENT = `#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;
precision highp usampler2D;

uniform sampler2D u_height;
uniform usampler2D u_biome;
uniform usampler2D u_form;
uniform sampler2D u_rock;
uniform vec2 u_size;
uniform vec2 u_origin;
uniform float u_units;
uniform float u_radius;
uniform vec2 u_eye;
uniform vec2 u_cells;
uniform float u_step;
uniform float u_relief;
uniform float u_deep;
uniform float u_high_from;
uniform int u_shore;
uniform vec3 u_light;
uniform vec3 u_biomes[${PALETTE_SLOTS}];
uniform vec3 u_sea_shallow;
uniform vec3 u_sea_deep;
uniform vec3 u_lake;
uniform vec3 u_high;
uniform uvec3 u_water_forms;
uniform uvec4 u_cliff_forms;
uniform uvec4 u_stone_forms;
uniform uvec2 u_sand_forms;
uniform uvec3 u_ice_forms;
uniform float u_grain;
uniform float u_grain_m;
uniform float u_edge;

out vec4 o_color;

const float PI = 3.141592653589793;
const float TAU = 6.283185307179586;
const float EXAGGERATION = ${EXAGGERATION.toFixed(1)};
const float GRAIN_DEPTH = ${GRAIN_DEPTH.toFixed(2)};
const float GRAIN_WRAP = ${GRAIN_WRAP.toFixed(1)};
const float NOISE_GAIN = ${NOISE_GAIN.toFixed(1)};
const float EDGE_M = ${EDGE_M.toFixed(1)};
const float UNITS_PER_METRE = ${UNITS_PER_METRE.toFixed(1)};

float heightAt(vec2 uv) { return texture(u_height, uv).r; }

//: Value noise on the sphere: the corners of a lattice cell hashed and
//: blended smoothly. The lattice is wrapped to GRAIN_WRAP before it is
//: hashed, and only there -- the cell's own fraction is taken first, so
//: the wrap costs nothing but the seam nobody reaches.
float hash3(vec3 cell) {
  vec3 c = mod(cell, GRAIN_WRAP);
  return fract(sin(dot(c, vec3(127.1, 311.7, 74.7))) * 43758.5453123);
}

float vnoise(vec3 p) {
  vec3 i = floor(p);
  vec3 f = p - i;
  f = f * f * (3.0 - 2.0 * f);
  float n00 = mix(hash3(i), hash3(i + vec3(1.0, 0.0, 0.0)), f.x);
  float n10 = mix(hash3(i + vec3(0.0, 1.0, 0.0)), hash3(i + vec3(1.0, 1.0, 0.0)), f.x);
  float n01 = mix(hash3(i + vec3(0.0, 0.0, 1.0)), hash3(i + vec3(1.0, 0.0, 1.0)), f.x);
  float n11 = mix(hash3(i + vec3(0.0, 1.0, 1.0)), hash3(i + vec3(1.0, 1.0, 1.0)), f.x);
  return mix(mix(n00, n10, f.y), mix(n01, n11, f.y), f.z);
}

//: The noise about zero and spread over the whole of -1..1. Value noise is
//: a blend of eight uniform draws and so heaps about a half: taken raw, its
//: swing is a tenth, and a texture built on it comes out invisible. The
//: gain is that heap widened, and the clamp keeps the tails honest.
float wave(vec3 p) {
  return clamp((vnoise(p) - 0.5) * NOISE_GAIN, -1.0, 1.0);
}

//: What the ground is made of, in one number about zero: rock speckles
//: coarsely, sand lies in waves across the wind, ice cracks in thin dark
//: lines, and everything that grows mottles softly. The hardness sharpens
//: whatever it is -- hard ground breaks into grains, soft ground smears.
float grainOf(vec3 sphere, uint form, float rock) {
  vec3 p = sphere * (u_radius / UNITS_PER_METRE / u_grain_m);
  bool stone = form == u_stone_forms.x || form == u_stone_forms.y
    || form == u_stone_forms.z || form == u_stone_forms.w;
  bool sand = form == u_sand_forms.x || form == u_sand_forms.y;
  bool ice = form == u_ice_forms.x || form == u_ice_forms.y || form == u_ice_forms.z;
  float grain;
  if (sand) {
    //: The lattice squeezed along one way, so the noise runs in ridges
    //: across it, as dunes lie across the wind.
    vec3 lie = vec3(p.x, p.y * 0.18, p.z);
    grain = 0.7 * wave(lie) + 0.3 * wave(lie * 2.7);
  } else if (ice) {
    //: A ridge of the noise, thin and dark: a crack, not a speckle. It
    //: goes one way only -- ice is white and cracks are lines in it.
    float ridge = 1.0 - abs(wave(p * 0.7));
    grain = -pow(ridge, 6.0);
  } else if (stone) {
    //: Grains of rock: fine and hard-edged, two sizes at once.
    grain = 0.6 * wave(p * 1.6) + 0.4 * wave(p * 4.3);
  } else {
    //: Turf, field, forest floor: a soft mottle at twice the size.
    grain = 0.7 * wave(p * 0.8) + 0.3 * wave(p * 2.1);
  }
  return grain * (0.7 + 0.6 * rock);
}

void main() {
  vec2 px = vec2(gl_FragCoord.x, u_size.y - gl_FragCoord.y);
  vec2 p = (px - u_origin) * u_units;
  float X = p.x / u_radius;
  float Y = -p.y / u_radius;
  float rho = length(vec2(X, Y));
  float edge = max(fwidth(rho), 1e-6);
  if (rho > 1.0 + edge) discard;
  float rc = min(rho, 1.0);
  float c = asin(rc);
  float sc = sin(c);
  float cc = cos(c);
  float lat0 = u_eye.x;
  float lon0 = u_eye.y;
  float lat = lat0;
  float lon = lon0;
  if (rc > 0.0) {
    lat = asin(clamp(cc * sin(lat0) + Y * sc * cos(lat0) / rc, -1.0, 1.0));
    lon = lon0 + atan(X * sc, rc * cc * cos(lat0) - Y * sc * sin(lat0));
  }
  vec2 uv = vec2(fract(lon / TAU + 0.5), clamp(lat / PI + 0.5, 0.0, 1.0));

  vec2 du = vec2(1.0 / u_cells.x, 0.0);
  vec2 dv = vec2(0.0, 1.0 / u_cells.y);
  float h = heightAt(uv);
  float dx = u_step * max(cos(lat), 0.05);
  float dy = u_step;
  float slopeX = (heightAt(uv + du) - heightAt(uv - du)) / (2.0 * dx);
  float slopeY = (heightAt(uv + dv) - heightAt(uv - dv)) / (2.0 * dy);
  vec3 n = normalize(vec3(-slopeX * EXAGGERATION, -slopeY * EXAGGERATION, 1.0));
  float shade = max(dot(n, u_light), 0.0);

  uint b = texture(u_biome, uv).r;
  uint f = texture(u_form, uv).r;
  vec3 sphere = vec3(cos(lat) * cos(lon), cos(lat) * sin(lon), sin(lat));
  //: The colour's edge, roughened (wave 8). A biome is a class of a cell
  //: five hundred metres wide, and on a near frame its edge is a straight
  //: staircase across the ground -- the one thing on the map that says
  //: "raster" out loud. The class is not blended (plan §9.3 keeps it a
  //: class, not a mean of two): what wanders is the *point the class is
  //: read at*, by less than a cell and by less than a slice of the frame
  //: (see edgeCells), so the boundary between two biomes comes out ragged
  //: as a real one is. It comes on with the grain and on the same ramp, or
  //: a straight edge would turn ragged at a stroke on the way in. Only
  //: between two lands: a sample
  //: strayed onto the water would paint a shore where there is none, and
  //: the water's own edge is the height's, cut to the pixel by the coast.
  if (u_edge > 0.0 && b != ${NO_BIOME}u) {
    vec3 j = sphere * (u_radius / UNITS_PER_METRE / EDGE_M);
    vec2 astray = vec2(wave(j), wave(j + vec3(11.3, 7.1, 3.9))) * u_edge;
    vec2 juv = vec2(fract(uv.x + astray.x / u_cells.x), clamp(uv.y + astray.y / u_cells.y, 0.0, 1.0));
    uint near = texture(u_biome, juv).r;
    if (near != ${NO_BIOME}u) b = near;
  }
  vec3 col;
  //: The sea is where the height, read between the cells, is under zero:
  //: the same zero the vector coast is drawn on, so the water's edge and
  //: its line agree to the pixel. A lake stands on land above zero and is
  //: told by its form, cell by cell.
  if (h < 0.0 || (b == ${NO_BIOME}u && f == u_water_forms.y)) {
    if (h >= 0.0) {
      col = u_lake;
    } else {
      col = mix(u_sea_shallow, u_sea_deep, clamp(-h / u_deep, 0.0, 1.0));
    }
    col *= 0.85 + 0.15 * shade;
  } else {
    //: A sea cell whose height, read between the cells, has come up over
    //: zero is the shore's last strip: it takes the coast's colour.
    int code = b == ${NO_BIOME}u ? u_shore : int(b);
    col = u_biomes[code < 0 ? ${PALETTE_SLOTS - 1} : min(code, ${PALETTE_SLOTS - 1})];
    float share = clamp(h / u_relief, 0.0, 1.0);
    col = mix(col, u_high, 0.6 * smoothstep(u_high_from, 1.0, share));
    bool cliff = f == u_cliff_forms.x || f == u_cliff_forms.y || f == u_cliff_forms.z || f == u_cliff_forms.w;
    float tone = 0.5 + 0.5 * shade;
    if (cliff) tone *= 0.7;
    //: The grain, on the near frames alone (wave 8): the ground says what
    //: it is made of, while the hillshade goes on saying what shape it is.
    if (u_grain > 0.0) {
      float rock = texture(u_rock, uv).r;
      tone *= 1.0 + GRAIN_DEPTH * u_grain * grainOf(sphere, f, rock);
    }
    col *= tone;
  }
  float alpha = 1.0 - smoothstep(1.0 - edge, 1.0 + edge, rho);
  o_color = vec4(col * alpha, alpha);
}
`;

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
  seaShallow: [number, number, number];
  seaDeep: [number, number, number];
  lake: [number, number, number];
  high: [number, number, number];
};

/** What is asked of the theme for the water and the heights, as the map's
 *  own tones are spelt in `map.css` -- the same expressions the SVG ground
 *  uses, so the two paths draw one sea. */
export const TONES = {
  seaShallow: "var(--gl-sea-shallow)",
  seaDeep: "var(--gl-sea-deep)",
  lake: "var(--gl-lake)",
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
  return {
    biomes: table,
    seaShallow: read(TONES.seaShallow),
    seaDeep: read(TONES.seaDeep),
    lake: read(TONES.lake),
    high: read(TONES.high),
  };
}

/** The form codes the shader tells water and cliffs by, off the passport's
 *  table; a form the table lacks is a code no cell carries. The river's
 *  code is named but not painted: a river is a line of the vector layer
 *  (plan §9.2, wave 6), not a cell of colour. */
export function formCodes(passport: RasterPassport): {
  water: [number, number, number];
  cliff: [number, number, number, number];
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
    water: [code("sea"), code("lake"), code("river")],
    cliff: [code("cliff"), code("coast_cliff"), code("canyon"), code("scree")],
    stone: [code("scree"), code("rocky_desert"), code("cliff"), code("coast_cliff")],
    sand: [code("dunes"), code("beach")],
    ice: [code("ice"), code("glacial"), code("fjord")],
    shore: passport.biomes.indexOf("coast"),
  };
}

/** The heights as the texture wants them: metres as floats, row 0 the south. */
export function heightsOf(bytes: ArrayBuffer): Float32Array {
  return Float32Array.from(new Int16Array(bytes));
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
 */
export function mipChain(
  level0: Float32Array,
  cols: number,
  rows: number,
): { data: Float32Array; cols: number; rows: number }[] {
  const chain = [{ data: level0, cols, rows }];
  let { data, cols: w, rows: h } = chain[0];
  while (w > 1 || h > 1) {
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
