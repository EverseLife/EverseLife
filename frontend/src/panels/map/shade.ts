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
/** The slope is drawn steeper than it is: at hundreds of kilometres a
 *  frame, a rise of seven hundred metres is nothing to the eye without it. */
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
export const EDGE_CELLS = 1.1;
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
uniform vec2 u_atlas;
uniform float u_nside;
uniform float u_border;
uniform float u_across;
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
const float UNITS_PER_METRE = ${UNITS_PER_METRE.toFixed(1)};
const float THIRD_TWO = 0.6666666666666666;

//: Where a point of the sphere sits on the picture's texture (D-328).
//:
//: The field is HEALPix: twelve square faces of u_nside cells a side, all
//: of the same area. The projection is arithmetic -- two families of
//: slanting lines over the belt, a Collignon diamond over each cap -- and
//: it is the same arithmetic the vault cut the field by and the server
//: reads it by (src/healpix.py). What comes back is not the cell but the
//: **place inside the face**, a fraction: whole cells sit at halves, and
//: the blending between them is then the hardware's own.
//:
//: This is where the grid pays for itself. On the old lattice of latitude
//: and longitude a column at the pole was a hundredth of a column at the
//: equator, everything read across it was noise, and it took a floor on the
//: cosine and a cap on the step to keep the shading from tearing. Here the
//: pole is not a place at all: it is the middle of four ordinary cells.
vec2 atlasUV(vec3 p) {
  float z = clamp(p.z, -1.0, 1.0);
  float phi = atan(p.y, p.x);
  if (phi < 0.0) phi += TAU;
  float za = abs(z);
  float turns = phi / (PI * 0.5);
  float n = u_nside;
  float u;
  float v;
  int face;
  if (za <= THIRD_TWO) {
    //: The belt: which side of the rising and the falling line the point
    //: falls on says which of the twelve faces it is on.
    float first = n * (0.5 + turns);
    float second = n * z * 0.75;
    float up = first - second;
    float down = first + second;
    int over = int(floor(up / n));
    int under = int(floor(down / n));
    if (over == under) face = (over & 3) + 4;
    else if (over < under) face = over & 3;
    else face = (under & 3) + 8;
    u = down - n * floor(down / n);
    v = n - (up - n * floor(up / n));
  } else {
    //: The caps: the point goes into the Collignon diamond of its quarter.
    float quarter = min(3.0, floor(turns));
    float along = turns - quarter;
    float reach = n * sqrt(max(0.0, 3.0 * (1.0 - za)));
    float a = clamp(along * reach, 0.0, n);
    float b = clamp((1.0 - along) * reach, 0.0, n);
    if (z >= 0.0) { face = int(quarter); u = n - b; v = n - a; }
    else { face = int(quarter) + 8; u = a; v = b; }
  }
  //: The atlas: the faces laid out u_across wide, each with a border of
  //: u_border cells taken from the face over the edge, so the blending
  //: never reaches into the tile of a stranger.
  float side = n + 2.0 * u_border;
  int across = int(u_across);
  float column = float(face - (face / across) * across);
  float row = float(face / across);
  return vec2(column * side + u_border + u, row * side + u_border + v) / u_atlas;
}

//: The height, at a level of the texture chosen by the caller.
//:
//: **Chosen**, and never left to the hardware: it picks the level from how
//: fast the texture's coordinates run across the screen, and atlasUV jumps
//: by a quarter of the atlas at every edge of a face. A level chosen from
//: that jump is the coarsest there is, so along all twelve seams -- and at
//: the pole, where four faces meet -- the ground would be drawn at the mean
//: height of half a planet. The old grid had the same fault on exactly one
//: meridian, where the longitude wrapped; twelve of them is a picture.
float heightAt(vec2 uv, float lod) { return textureLod(u_height, uv, lod).r; }
//: The height at a point of the sphere. Every reading goes through the
//: projection, so a sample that steps off the edge of a face lands on
//: whatever face is really there -- there is no wrapping to get wrong.
float heightOf(vec3 p, float lod) { return heightAt(atlasUV(normalize(p)), lod); }

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
  //: This pixel's own point on the ball, and the ground's compass there.
  //: Built from the eye's frame rather than from the latitude and the
  //: longitude that were just found: apart is the careful difference and
  //: adding it back costs nothing.
  vec3 here = normalize(up + apart);
  vec3 sideways = cross(vec3(0.0, 0.0, 1.0), here);
  float turn = length(sideways);
  //: At the pole itself every direction is east; any one of them will do,
  //: and no reading depends on which, because the ground there is a cell
  //: like every other one (D-328).
  vec3 pe = turn > 1e-6 ? sideways / turn : vec3(1.0, 0.0, 0.0);
  vec3 pn = cross(here, pe);
  vec2 uv = atlasUV(here);
  //: How much ground one pixel covers, taken from the point on the ball --
  //: which runs smoothly across the screen everywhere, seams included --
  //: and turned into a level of the texture. One cell to the pixel is level
  //: nought; every doubling is one level up.
  float metres = u_radius / UNITS_PER_METRE;
  float across = max(length(dFdx(apart)), length(dFdy(apart))) * metres;
  float lod = max(0.0, log2(max(across / u_step, 1.0)));

  //: The slope, taken a cell of the ground east and north of the point --
  //: **metres of the ground**, not steps of the raster. On the old lattice
  //: of latitude and longitude a step of one column was cos(lat) of a step
  //: of one row, and near the pole a slope read across it was not a slope
  //: but the noise of the interpolation: that was the fan of streaks that
  //: stood over the pole. Here the two steps are the same length of ground
  //: everywhere, and the pole needs no special case at all.
  //: The slope is read a cell of the ground apart on the near frames and a
  //: pixel's worth of ground apart on the far ones: read a cell apart at a
  //: frame where a pixel covers ten, the two samples fall in the same texel
  //: of the level being drawn and the shading goes flat.
  float reach = max(u_step, across);
  float span = reach / metres;
  float h = heightAt(uv, lod);
  float slopeX = (heightOf(here + pe * span, lod) - heightOf(here - pe * span, lod)) / (2.0 * reach);
  float slopeY = (heightOf(here + pn * span, lod) - heightOf(here - pn * span, lod)) / (2.0 * reach);
  vec3 n = normalize(vec3(-slopeX * EXAGGERATION, -slopeY * EXAGGERATION, 1.0));
  float shade = max(dot(n, u_light), 0.0);

  uint b = texture(u_biome, uv).r;
  uint f = texture(u_form, uv).r;
  //: The landform wanders with the biome, and for the same reason. The
  //: picture darkens a cliff and roughens the ground by what the form says,
  //: and a cliff is often a single cell: read at the pixel's own point it
  //: came out as a hard diamond of shadow, scattered along a mountain front
  //: like beads -- the shape of a cell and nothing of the country.
  //: The colour's edge, roughened (wave 8). A biome is a class of a cell
  //: a hundred metres wide, and on a near frame its edge is a straight
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
    //: The wander is a walk over the **ground** -- so many cells east and
    //: north -- and not a walk over the raster's own axes: on this grid a
    //: face's lattice stands at an angle to the compass that changes over
    //: the sphere, and a wander along it would have followed the faces.
    vec3 strayed = here + (pe * astray.x + pn * astray.y) * span;
    vec2 juv = atlasUV(normalize(strayed));
    uint near = texture(u_biome, juv).r;
    if (near != ${NO_BIOME}u) b = near;
    f = texture(u_form, juv).r;
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
    seaShallow: "var(--gl-sea-shallow)",
    seaDeep: "var(--gl-sea-deep)",
    lake: "var(--gl-lake)",
  },
  lava: {
    seaShallow: "var(--gl-lava-shallow)",
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
    seaShallow: read(tones.seaShallow),
    seaDeep: read(tones.seaDeep),
    lake: read(tones.lake),
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
