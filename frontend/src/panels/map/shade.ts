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
 *  The numbers are set against what they hide: the staircase of a raster
 *  cell, five hundred metres of it. A wander of three fifths of a cell,
 *  waving every three hundred metres, turns that staircase into a line the
 *  ground could have drawn; much less and the steps show through. */
export const EDGE_CELLS = 0.6;
export const EDGE_M = 300;
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
/** How near the pole the cosine of the latitude is allowed to get before it
 *  is held: a thousandth, below which a column of the raster is centimetres
 *  of ground and nothing read across it means anything. */
export const COS_FLOOR = 0.001;
/** And how far east the hillshade may reach for its slope, as a share of
 *  the planet's turn: at the pole itself the honest step would be the whole
 *  circle, and a tenth of it is already the width of the cap. */
export const POLE_SPAN = 0.1;
export const FRAGMENT = `#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;
precision highp usampler2D;

uniform sampler2D u_height;
uniform usampler2D u_biome;
uniform usampler2D u_form;
uniform sampler2D u_rock;
uniform sampler2D u_wet;
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
uniform float u_edge;
uniform vec3 u_grain_at;
uniform vec3 u_edge_at;

out vec4 o_color;

const float PI = 3.141592653589793;
const float TAU = 6.283185307179586;
const float EXAGGERATION = ${EXAGGERATION.toFixed(1)};
const float GRAIN_DEPTH = ${GRAIN_DEPTH.toFixed(2)};
const float GRAIN_WRAP = ${GRAIN_WRAP.toFixed(1)};
const float NOISE_GAIN = ${NOISE_GAIN.toFixed(1)};
const float GRAIN_M = ${GRAIN_M.toFixed(1)};
const int GRAIN_OCTAVES = ${GRAIN_OCTAVES};
const float GRAIN_FALL = ${GRAIN_FALL.toFixed(2)};
const float GRAIN_WHOLE = ${grainWhole().toFixed(4)};
const float GRAIN_SEEN_PX = ${GRAIN_SEEN_PX.toFixed(2)};
const float GRAIN_FULL_PX = ${GRAIN_FULL_PX.toFixed(2)};
const float EDGE_M = ${EDGE_M.toFixed(1)};
const float EDGE_CELLS = ${EDGE_CELLS.toFixed(2)};
const float COS_FLOOR = ${COS_FLOOR};
const float POLE_SPAN = ${POLE_SPAN};
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
//:
//: apart is how far the pixel stands from the eye, on the unit ball; the
//: eye's own place in the lattice comes as u_grain_at, already wrapped to
//: GRAIN_WRAP by the frame. So the lattice coordinate is a number of a
//: few hundred rather than of five figures, and every figure of it is the
//: ground. The octaves are whole doublings for the same reason: a wrap of
//: the eye's place then lands on a wrap of every octave, and the texture
//: does not jump when the eye crosses one.
//: The ground's texture at every size it has, added up: each octave half
//: the last and a little quieter, and each drawn only so far as its own
//: cell is worth pixels. Divided by what they all come to, so the loudest
//: is the same at every zoom -- what the zoom changes is which octaves are
//: there to be seen, never the shape of the ones already visible.
float fractal(vec3 p, float metre_px) {
  float sum = 0.0;
  float amp = 1.0;
  float step = 1.0;
  for (int o = 0; o < GRAIN_OCTAVES; o++) {
    float cell_px = (GRAIN_M / step) / metre_px;
    float seen = clamp((cell_px - GRAIN_SEEN_PX) / (GRAIN_FULL_PX - GRAIN_SEEN_PX), 0.0, 1.0);
    if (seen > 0.0) sum += amp * seen * wave(p * step);
    amp *= GRAIN_FALL;
    step *= 2.0;
  }
  return sum / GRAIN_WHOLE;
}

float grainOf(vec3 apart, uint form, float rock) {
  float metre_px = u_units / UNITS_PER_METRE;
  vec3 p = u_grain_at + apart * (u_radius / UNITS_PER_METRE / GRAIN_M);
  bool stone = form == u_stone_forms.x || form == u_stone_forms.y
    || form == u_stone_forms.z || form == u_stone_forms.w;
  bool sand = form == u_sand_forms.x || form == u_sand_forms.y;
  bool ice = form == u_ice_forms.x || form == u_ice_forms.y || form == u_ice_forms.z;
  float grain;
  if (sand) {
    //: The lattice squeezed along one way, so the ground runs in ridges
    //: across it, as dunes lie across the wind.
    grain = fractal(vec3(p.x, p.y * 0.25, p.z), metre_px);
  } else if (ice) {
    //: A ridge of the ground, thin and dark: a crack, not a speckle. It
    //: goes one way only -- ice is white and cracks are lines in it.
    grain = -pow(1.0 - abs(fractal(p, metre_px)), 6.0);
  } else if (stone) {
    //: Grains of rock: the same ground, harder-edged.
    grain = clamp(fractal(p, metre_px) * 1.5, -1.0, 1.0);
  } else {
    //: Turf, field, forest floor.
    grain = fractal(p, metre_px);
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
  //: The angle from the eye is never taken: what the projection wants is
  //: its sine and its cosine, and both are the radius itself. The sine of
  //: the arc IS rho -- that is what an orthographic projection is -- and
  //: the cosine is the root of one less its square. Asked for through asin
  //: and sin instead, the pair came back with an error of an absolute size
  //: about a value of a vanishing one, which is a relative error of tens of
  //: per cent at the middle of the frame; the grain's lattice multiplied it
  //: by the planet's radius over its cell, and a star of rays stood over
  //: the eye. Not an approximation -- the shorter road is the exact one.
  float cc = sqrt(max(0.0, 1.0 - rc * rc));
  float lat0 = u_eye.x;
  float lon0 = u_eye.y;
  float lat = asin(clamp(cc * sin(lat0) + Y * cos(lat0), -1.0, 1.0));
  float lon = lon0 + atan(X, cc * cos(lat0) - Y * sin(lat0));
  vec2 uv = vec2(fract(lon / TAU + 0.5), clamp(lat / PI + 0.5, 0.0, 1.0));
  //: The eye's own frame: which way is up, east and north where it stands.
  vec3 up = vec3(cos(lat0) * cos(lon0), cos(lat0) * sin(lon0), sin(lat0));
  vec3 east = vec3(-sin(lon0), cos(lon0), 0.0);
  vec3 north = vec3(-sin(lat0) * cos(lon0), -sin(lat0) * sin(lon0), cos(lat0));
  //: How far this pixel's point stands from the eye's own, on the unit
  //: ball. A difference from the start rather than a place and a
  //: subtraction: the drop of the chord, cos - 1, is taken as
  //: -rho^2 / (1 + cos), which keeps its figures where the plain
  //: difference would have lost them all. The eye's own place never enters
  //: the fragment at all: what the lattices need of it is a small number
  //: the frame hands over ready-made (u_grain_at, u_edge_at).
  vec3 apart = up * (-rc * rc / (1.0 + cc)) + east * X + north * Y;

  //: A step east of the same length of ground as the step north. On a grid
  //: of latitude and longitude a column is cos(lat) as wide as a row is
  //: tall: at eighty-eight degrees one column is a metre of ground against
  //: five hundred for one row, and a slope taken across it is not a slope
  //: but the noise of the interpolation -- which is what stood at the pole
  //: as a fan of streaks radiating from it. Stepping as many columns as it
  //: takes to cover the same ground makes the two derivatives comparable
  //: and the fan goes. Held to a tenth of the planet's turn, so the step
  //: stays a step and does not reach round the world at the pole itself.
  float squeeze = min(1.0 / max(cos(lat), COS_FLOOR), u_cells.x * POLE_SPAN);
  vec2 du = vec2(squeeze / u_cells.x, 0.0);
  vec2 dv = vec2(0.0, 1.0 / u_cells.y);
  float h = heightAt(uv);
  float dx = u_step * squeeze * max(cos(lat), COS_FLOOR);
  float dy = u_step;
  float slopeX = (heightAt(uv + du) - heightAt(uv - du)) / (2.0 * dx);
  float slopeY = (heightAt(uv + dv) - heightAt(uv - dv)) / (2.0 * dy);
  vec3 n = normalize(vec3(-slopeX * EXAGGERATION, -slopeY * EXAGGERATION, 1.0));
  float shade = max(dot(n, u_light), 0.0);

  uint b = texture(u_biome, uv).r;
  uint f = texture(u_form, uv).r;
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
    vec3 j = u_edge_at + apart * (u_radius / UNITS_PER_METRE / EDGE_M);
    //: Two ways to wander, and they must not be one way twice. Read off the
    //: one lattice a step apart, the two came out of the same ridges and
    //: the edge wandered along a diagonal, holding the right angles of the
    //: raster it was meant to hide. Turned into its own lattice and taken
    //: at two sizes each, they are two motions and the edge is a line.
    vec3 k = vec3(j.z, j.x, j.y) * 1.7 + vec3(19.7, 5.3, 31.1);
    vec2 astray = vec2(
      0.65 * wave(j) + 0.35 * wave(j * 2.0),
      0.65 * wave(k) + 0.35 * wave(k * 2.0)
    ) * (EDGE_CELLS * u_edge);
    vec2 juv = vec2(fract(uv.x + astray.x / u_cells.x), clamp(uv.y + astray.y / u_cells.y, 0.0, 1.0));
    uint near = texture(u_biome, juv).r;
    if (near != ${NO_BIOME}u) b = near;
  }
  vec3 col;
  //: The sea is where the height, read between the cells, is under zero:
  //: the same zero the vector coast is drawn on, so the water's edge and
  //: its line agree to the pixel. A lake stands on land above zero and is
  //: told by its form, cell by cell.
  //: A lake is cut where its own share passes a half, read between the
  //: cells: as a class it was whole five-hundred-metre cells, and a lake
  //: of blue rectangles with right angles is not a lake (owner,
  //: 2026-09-10). The sea is the height's own zero, as it was.
  if (h < 0.0 || texture(u_wet, uv).r > 0.5) {
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
      tone *= 1.0 + GRAIN_DEPTH * u_grain * grainOf(apart, f, rock);
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
