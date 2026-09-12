// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The GLSL of the ground (landscape plan wave 5, sec. 9.4 level A): the vertex
 * and the fragment shader as strings, written from the constants of
 * `shade.ts`, so that the pure parts over there -- the palette read off the
 * theme, the mip chains, the weights of the cubic -- are tested without a
 * GPU and the shader reads the very same numbers. Split out of `shade.ts`
 * on 2026-09-12, when the two together stood past the eight-hundred-line
 * bar: this file is the picture's GLSL and nothing else, and it imports
 * from `shade.ts`, never the other way round.
 *
 * No backticks in any comment inside the strings: this is GLSL inside a
 * template string, and a pair of them closes it.
 */

import { UNITS_PER_METRE } from "./globe";
import {
  AA_PX,
  AMBIENT,
  BANK_SHARE,
  CATMULL_ROM,
  EDGE_CELLS,
  EDGE_M,
  EXAGGERATION,
  FAR_WATER_DEEP,
  LEGEND_AMBIENT,
  LEGEND_WET_DIM,
  LEGEND_WET_EDGE,
  LIGHT_ALT_MIN_DEG,
  NIGHT_TINT,
  NO_BIOME,
  PALETTE_SLOTS,
  RAMPS,
  RELIEF_DEPTH,
  RELIEF_LEVELS,
  NIGHT_WATER,
  RELIEF_M,
  RIVER_FAINT,
  RIVER_FULL,
  SHADOW_ALT_MIN_DEG,
  SHADOW_FAR_FROM,
  CLOUD_TONE,
  CLOUD_OPACITY,
  CLOUD_SHADE,
  CLOUD_KM,
  CLOUD_NEAR_MPX,
  CLOUD_FAR_MPX,
  CLOUD_SUN_MIN,
  CLOUD_NIGHT,
  WX_HAZE,
  SHADOW_LEVEL_MAX,
  SHADOW_DEPTH,
  SHADOW_FULL_SIN,
  SHADOW_LOW,
  SHADOW_SOFT,
  SHADOW_STEPS,
  TWILIGHT,
  glslRamp,
  glslWeight,
} from "./shade";
import {
  SNOW_OVER_GRAIN,
  SNOW_TONE,
  ICE_TONE,
} from "./season";
import { GRAIN_GLSL } from "./grainGlsl";
import { LAYERS_GLSL } from "./layersGlsl";
import { WEATHER_GLSL } from "./weatherGlsl";
import {
  GRAIN_DEPTH,
  GRAIN_FALL,
  GRAIN_FULL_PX,
  GRAIN_M,
  GRAIN_OCTAVES,
  GRAIN_SEEN_PX,
  GRAIN_WRAP,
  NOISE_GAIN,
  GRAIN_SHAPE,
  grainWhole,
} from "./grain";

export const VERTEX = `#version 300 es
in vec2 a_pos;
void main() { gl_Position = vec4(a_pos, 0.0, 1.0); }
`;

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
uniform vec3 u_sea_deep;
uniform vec3 u_lake;
uniform vec3 u_high;
uniform sampler2D u_stream;
uniform vec3 u_sun;
uniform float u_sunlit;
uniform sampler2D u_temp;
uniform sampler2D u_rain;
uniform int u_layer;
uniform float u_temp_min;
uniform float u_temp_step;
uniform float u_temp_cold;
uniform float u_temp_hot;
uniform vec4 u_dry;
uniform float u_reach_m;
uniform sampler2D u_river;
//: The top of the ground, the max chain of the height (shade.topChain):
//: what the cast shadow reads a stretch of ground by; and the planet's
//: tallest ground, past which no shadow reaches.
uniform sampler2D u_top;
uniform float u_top_m;
//: The season (D-334): the swing of the mean temperature at the pole as
//: of now, and the lines of the snow and the ice with the band between.
uniform float u_season_c;
uniform vec3 u_snow;
//: A dry cold keeps only u_snow_dry.y of its snow below u_snow_dry.x of
//: the rain scale (season.snow_dry_rain, season.snow_dry_share).
uniform vec2 u_snow_dry;
//: The weather (D-335): the lattice's scale, the wind's drift so far, the
//: slice of time, the gates cover -> cloud (xy) and cover -> rain (zw),
//: how far the ground's own rain pulls the cover, and whether the clouds
//: are drawn at all (the overlay).
uniform float u_wx_scale;
uniform float u_wx_drift;
uniform float u_wx_slice;
uniform vec4 u_wx_gates;
uniform float u_wx_bias;
uniform float u_wx_gain;
uniform float u_clouds;
//: The grain of each biome, by the raster's code: scale, stretch,
//: contrast, shape (grain.grainTable off the vault's biome.grain).
uniform vec4 u_grains[${PALETTE_SLOTS}];
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
//: The roughened edge's lattice is the base one, unscaled: it wraps with it.
const vec3 EDGE_WRAP = vec3(GRAIN_WRAP);
const float NOISE_GAIN = ${NOISE_GAIN.toFixed(1)};
const float GRAIN_M = ${GRAIN_M.toFixed(1)};
const int GRAIN_OCTAVES = ${GRAIN_OCTAVES};
const float GRAIN_FALL = ${GRAIN_FALL.toFixed(2)};
const float GRAIN_WHOLE = ${grainWhole().toFixed(4)};
const float GRAIN_SEEN_PX = ${GRAIN_SEEN_PX.toFixed(2)};
const float GRAIN_FULL_PX = ${GRAIN_FULL_PX.toFixed(2)};
const float EDGE_M = ${EDGE_M.toFixed(1)};
const float AA_PX = ${AA_PX.toFixed(1)};
const float BANK_SHARE = ${BANK_SHARE.toFixed(2)};
const float RIVER_FAINT = ${RIVER_FAINT.toFixed(2)};
const float RIVER_FULL = ${RIVER_FULL.toFixed(2)};
const float NIGHT_WATER = ${NIGHT_WATER.toFixed(2)};
const float AMBIENT = ${AMBIENT.toFixed(2)};
const float LEGEND_AMBIENT = ${LEGEND_AMBIENT.toFixed(2)};
const float LEGEND_WET_DIM = ${LEGEND_WET_DIM.toFixed(2)};
const float LEGEND_WET_EDGE = ${LEGEND_WET_EDGE.toFixed(2)};
const float FAR_WATER_DEEP = ${FAR_WATER_DEEP.toFixed(2)};
const float LIGHT_ALT_MIN = ${((LIGHT_ALT_MIN_DEG * Math.PI) / 180).toFixed(4)};
const float TWILIGHT = ${TWILIGHT.toFixed(2)};
const vec3 NIGHT_TINT = vec3(${NIGHT_TINT.map((v) => v.toFixed(2)).join(", ")});
const vec3 SNOW_TONE = vec3(${SNOW_TONE.map((v) => v.toFixed(2)).join(", ")});
const vec3 ICE_TONE = vec3(${ICE_TONE.map((v) => v.toFixed(2)).join(", ")});
const float SNOW_OVER_GRAIN = ${SNOW_OVER_GRAIN.toFixed(2)};
const vec3 CLOUD_TONE = vec3(${CLOUD_TONE.map((v) => v.toFixed(2)).join(", ")});
const float CLOUD_OPACITY = ${CLOUD_OPACITY.toFixed(2)};
const float CLOUD_SHADE = ${CLOUD_SHADE.toFixed(2)};
const float CLOUD_KM = ${CLOUD_KM.toFixed(2)};
const float CLOUD_NEAR_MPX = ${CLOUD_NEAR_MPX.toFixed(1)};
const float CLOUD_FAR_MPX = ${CLOUD_FAR_MPX.toFixed(1)};
const float CLOUD_SUN_MIN = ${CLOUD_SUN_MIN.toFixed(2)};
const float CLOUD_NIGHT = ${CLOUD_NIGHT.toFixed(2)};
const float WX_HAZE = ${WX_HAZE.toFixed(2)};
const int SHADOW_STEPS = ${SHADOW_STEPS};
const float SHADOW_CLIMB_MIN = ${Math.tan((SHADOW_ALT_MIN_DEG * Math.PI) / 180).toFixed(4)};
const float SHADOW_SOFT = ${SHADOW_SOFT.toFixed(3)};
const float SHADOW_DEPTH = ${SHADOW_DEPTH.toFixed(2)};
const float SHADOW_LEVEL_MAX = ${SHADOW_LEVEL_MAX.toFixed(1)};
const float SHADOW_FAR_FROM = ${SHADOW_FAR_FROM.toFixed(1)};
const int SHAPE_CLUMPS = ${GRAIN_SHAPE.clumps};
const int SHAPE_CRACKS = ${GRAIN_SHAPE.cracks};
const int SHAPE_POOLS = ${GRAIN_SHAPE.pools};
const int SHAPE_PATCHES = ${GRAIN_SHAPE.patches};
const int SHAPE_NET = ${GRAIN_SHAPE.net};
const int SHAPE_SPECKLE = ${GRAIN_SHAPE.speckle};
const float SHADOW_LOW = ${SHADOW_LOW.toFixed(2)};
const float SHADOW_FULL_SIN = ${SHADOW_FULL_SIN.toFixed(2)};
const float RELIEF_LEVELS = ${RELIEF_LEVELS.toFixed(1)};
const float RELIEF_M = ${RELIEF_M.toFixed(1)};
const float RELIEF_DEPTH = ${RELIEF_DEPTH.toFixed(2)};
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
vec3 facePlace(vec3 p) {
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
  return vec3(u, v, float(face));
}

//: Where a place of a face sits on the atlas: the faces laid out u_across
//: wide, each with a border of u_border cells taken from the face over
//: the edge, so the blending never reaches into the tile of a stranger.
//: The margin keeps the place so many cells inside its face: the border
//: covers the finest level's blend and no more, and a read at a coarser
//: level -- a texel of exp2(level) cells, blended with the texel over --
//: would reach past it into the tile beside, another face entirely, laid
//: there by the atlas and not by the sphere. The shadow's far reads did,
//: and the shadow was cut by a straight line along every seam -- and so
//: the client lays the atlas out with a border wide enough for every
//: level (atlas.ts); the margin is the rule that layout is built to.
vec2 atlasAt(vec3 place, float margin) {
  float n = u_nside;
  float m = min(margin, n * 0.5);
  vec2 uv = clamp(place.xy, vec2(m), vec2(n - m));
  float side = n + 2.0 * u_border;
  int across = int(u_across);
  int face = int(place.z);
  float column = float(face - (face / across) * across);
  float row = float(face / across);
  return vec2(column * side + u_border + uv.x, row * side + u_border + uv.y) / u_atlas;
}

vec2 atlasUV(vec3 p) { return atlasAt(facePlace(p), 0.0); }

//: A level's margin: half a texel of it -- what a blend reaches past the
//: sample -- less the border already there. With the wide border the
//: client lays the atlas out with (atlas.ts) this is nought for every
//: level a frame reads; it stays as the rule the layout is built to.
float marginOf(float level) { return max(0.0, exp2(level) * 0.5 - u_border); }

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
//: The top of the ground about a point, at a level: the highest cell
//: under the texel, kept inside the point's own face (shade.topChain).
float topOf(vec3 p, float lod) {
  //: Normalised first, as every reading is: what comes in need not stand
  //: on the ball, and facePlace takes the height of a point as its z.
  return textureLod(u_top, atlasAt(facePlace(normalize(p)), marginOf(lod)), lod).r;
}
//: The height at a point of the sphere. Every reading goes through the
//: projection, so a sample that steps off the edge of a face lands on
//: whatever face is really there -- there is no wrapping to get wrong.
float heightOf(vec3 p, float lod) {
  return heightAt(atlasAt(facePlace(normalize(p)), marginOf(lod)), lod);
}

//: Catmull-Rom along one axis: the weights of the four texels about a
//: place t of the way from one texel centre to the next, written from
//: the one table (CATMULL_ROM). They sum to one and pass through the
//: samples -- the curve is the texels' own values joined smoothly, not a
//: blur of them (a B-spline shrank every river's diagonal reach by a
//: sixth when it was tried on the ribbon, plan sec. 17).
vec4 cubicWeights(float t) {
  float t2 = t * t;
  float t3 = t2 * t;
  return vec4(
    ${CATMULL_ROM.map(glslWeight).join(",\n    ")}
  );
}

//: The height between the cells read **cubically**, at the finest level.
//:
//: The hardware's bilinear blend is smooth inside a cell and breaks its
//: slope at every cell's edge: on the near frames, where a cell is a
//: quarter of the screen, the water's edge was a chain of arcs with a kink
//: at each cell, and a lone cell of land in the sea was a diamond -- the
//: last of the diamonds (owner, 2026-09-12), the one the field cannot
//: take away because it is made by the reading. Sixteen texels weighed by
//: Catmull-Rom join with their slope; the two middle texels of each axis
//: are read as one bilinear tap placed between them by their weights, so
//: the sixteen cost nine fetches. A tap is held inside the face's own tile
//: (border included): the border is one cell wide, and the outer taps
//: would otherwise reach a stranger's face across the atlas.
float heightCubic(vec2 uv) {
  vec2 p = uv * u_atlas - 0.5;
  vec2 base = floor(p);
  vec2 t = p - base;
  vec4 wx = cubicWeights(t.x);
  vec4 wy = cubicWeights(t.y);
  vec2 w12 = vec2(wx.y + wx.z, wy.y + wy.z);
  vec2 mid = base + vec2(wx.z, wy.z) / w12 + 0.5;
  float side = u_nside + 2.0 * u_border;
  vec2 tile = floor(uv * u_atlas / side) * side;
  vec2 lo = tile + 0.5;
  vec2 hi = tile + side - 0.5;
  vec2 a = clamp(base - 0.5, lo, hi);
  vec2 b = clamp(mid, lo, hi);
  vec2 c = clamp(base + 2.5, lo, hi);
  vec3 wxs = vec3(wx.x, w12.x, wx.w);
  vec3 wys = vec3(wy.x, w12.y, wy.w);
  vec3 xs = vec3(a.x, b.x, c.x);
  vec3 ys = vec3(a.y, b.y, c.y);
  float sum = 0.0;
  for (int j = 0; j < 3; j++) {
    float row = 0.0;
    for (int i = 0; i < 3; i++) {
      row += wxs[i] * textureLod(u_height, vec2(xs[i], ys[j]) / u_atlas, 0.0).r;
    }
    sum += wys[j] * row;
  }
  return sum;
}

//: Value noise on the sphere: the corners of a lattice cell hashed and
//: blended smoothly. The lattice is wrapped to GRAIN_WRAP before it is
//: hashed, and only there -- the cell's own fraction is taken first, so
//: the wrap costs nothing but the seam nobody reaches.
${WEATHER_GLSL}
${GRAIN_GLSL}

//: The ramps of the climate layers, the soil's and the relief's (D-331):
//: the stops of shade.RAMPS, blended pairwise -- the same table the
//: legend in the corner draws its bar from.
${glslRamp("rampTemp", RAMPS.temperature)}
${glslRamp("rampRain", RAMPS.rain)}
${glslRamp("rampMoist", RAMPS.moisture)}
${glslRamp("rampHeight", RAMPS.height)}
${glslRamp("rampWeather", RAMPS.weather)}

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
  vec3 place = facePlace(here);
  vec2 uv = atlasAt(place, 0.0);
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
  //: The sun (owner, 2026-09-12: the shadow is to play with the relief,
  //: not darken a region). u_sun is the subsolar point on the ball, from
  //: the world's clock, and how high it stands over this pixel is its dot
  //: with the pixel's own place. The relief is lit from the sun's own
  //: side -- the shadows fall west in the morning and east in the evening
  //: -- but never from straight overhead, where every slope would read
  //: alike: for the shading the light is held LIGHT_ALT_MIN over the
  //: ground at least, while the cast shadow below takes the sun's true
  //: height. Without a clock (u_sunlit nought) the light is the map's own
  //: north-west, as on every topographic sheet.
  float high = dot(here, u_sun);
  vec3 sun_local = vec3(dot(u_sun, pe), dot(u_sun, pn), high);
  float flat_len = length(sun_local.xy);
  vec2 toward = flat_len > 1e-4 ? sun_local.xy / flat_len : normalize(u_light.xy);
  float alt = max(asin(clamp(high, -1.0, 1.0)), LIGHT_ALT_MIN);
  vec3 light = normalize(mix(u_light, vec3(toward * cos(alt), sin(alt)), u_sunlit));
  float shade = max(dot(n, light), 0.0);
  //: The cast shadow: back along the ground toward the sun in SHADOW_STEPS
  //: stretches, each from some distance to twice it, and the point is in
  //: shadow where the ground on the way stands higher than the sun's ray
  //: does there. The ray climbs at the sun's true height over the ground
  //: as the slope is drawn (EXAGGERATION), so the shadows lie long at dawn
  //: and dusk and short at noon. A stretch is read **whole**: the top of
  //: the ground over it (u_top, the max chain) at the level whose texel is
  //: the stretch, so no ridge falls between two reads -- one read a
  //: stretch, off the mean chain, missed the ridges between its points and
  //: lost the far ones in the mean, and the long shadows of the edge of
  //: the day came out cut (owner, 2026-09-12). Past SHADOW_LEVEL_MAX
  //: levels over the frame's own the texel is wider across the ray than a
  //: shadow, and the stretch is read in as many texels of that level as
  //: it holds instead. The march ends where no shadow can reach: the
  //: tallest ground there is (u_top_m), over this pixel, at the sun's
  //: climb -- a few reads at noon, the whole way only at the edge of the
  //: day. No branch on the uniform: at night and without a clock the
  //: depth is multiplied away.
  float climb = max(tan(asin(clamp(high, 0.0, 1.0))), SHADOW_CLIMB_MIN) / EXAGGERATION;
  //: The pixel's own height off the same chain the stretches are read by:
  //: the mean of a far frame's pixel against the top of the texel beside
  //: it shaded every hill of a range at a low sun.
  float h_top = textureLod(u_top, atlasAt(place, marginOf(lod)), lod).r;
  //: How far a shadow can reach at all. The planet is a ball, and a small
  //: one: the ground falls away under the ray as the square of the
  //: distance (along^2 / 2R), so even the shadow of the edge of the day
  //: ends where the ray clears the tallest ground there is -- the horizon
  //: of the tallest peak, a few kilometres on Terra. Nought on the night
  //: side and with no clock: the march ends before its first read, and
  //: no branch on the uniform is needed for it.
  float over = max(u_top_m - h_top, 0.0);
  //: In the drawn space, as the climb is: the heights stand EXAGGERATION
  //: times taller over the same ball, so the ball falls away under the ray
  //: by that much less of a drawn height (review, 2026-09-12: without it
  //: the far shadows of the edge of the day were a third short).
  float longest = metres * EXAGGERATION * (sqrt(climb * climb + 2.0 * over / (metres * EXAGGERATION)) - climb) * step(0.0, high) * u_sunlit;
  float dark = 0.0;
  float dist = reach;
  vec3 sunward = pe * toward.x + pn * toward.y;
  for (int k = 0; k < SHADOW_STEPS; k++) {
    if (dist > longest || dark >= 1.0) break;
    float up = min(float(k), SHADOW_LEVEL_MAX + max(0.0, float(k) - SHADOW_FAR_FROM));
    float level = lod + up;
    int taps = int(exp2(float(k) - up) + 0.5);
    for (int j = 0; j < taps; j++) {
      float along = dist * (1.0 + (float(j) + 0.5) / float(taps));
      //: Along the great circle, not the tangent: a run of a few
      //: kilometres on a ball twenty-five kilometres across stands well off it.
      float turn = along / metres;
      vec3 q = here * cos(turn) + sunward * sin(turn);
      float rise = topOf(q, level) - h_top - climb * along - along * along / (2.0 * metres * EXAGGERATION);
      //: The penumbra: the ray is aimed at the sun's centre, so ground
      //: level with it hides half the disc, and a rise of the disc's width
      //: at this distance (SHADOW_SOFT) hides it all. The edge is soft in
      //: proportion to the distance to what casts it: sharp under a bank,
      //: soft a valley away.
      dark = max(dark, rise / (along * SHADOW_SOFT) + 0.5);
    }
    dist *= 2.0;
  }
  //: The depth of the shadow by the sun's height: the low sun's light
  //: comes through more air and the sky lights what it does not reach, so
  //: the long shadow of the evening is a paler one than the short shadow of
  //: noon -- SHADOW_LOW of the depth at the horizon, the whole from
  //: SHADOW_FULL_SIN up.
  float depth = SHADOW_DEPTH * mix(SHADOW_LOW, 1.0, smoothstep(0.0, SHADOW_FULL_SIN, high));
  //: And in over the twilight, not at a step on the horizon: with the
  //: shadow switched off at nought the ground under a range at the edge
  //: of the day jumped a quarter brighter across one line, and every long
  //: shadow ended on it (owner, 2026-09-12: a sharp line of day and night,
  //: shadows cut). The sun rising through the twilight band lights the
  //: ground more and shades it more together.
  float lit = 1.0 - clamp(dark, 0.0, 1.0) * depth * u_sunlit * smoothstep(0.0, TWILIGHT, high);
  //: Day and night without an edge: the terminator is a band TWILIGHT wide
  //: in the sine of the sun's height, over which the day's colour fades to
  //: the night's tint -- and the night is not black: the relief reads in
  //: the dark, as it did when the night was a flat path laid over the map.
  float daylight = mix(1.0, smoothstep(-TWILIGHT, TWILIGHT, high), u_sunlit);

  //: The level is named, not guessed. A class raster has a chain now, and
  //: left to the plain texture() the hardware picks its level off the
  //: derivative of uv -- which on this projection jumps at every seam of the
  //: atlas and reads a far coarser level than the frame wants: the ground
  //: came out in flat blotches with no rivers in them (seen in the running
  //: game, 2026-09-11). The lod above is the frame's own answer to "how much
  //: ground is a pixel", and the height has been read by it from day one.
  //:
  //: No backticks in this comment, and none anywhere in here: this is GLSL
  //: inside a template string, and a pair of them closes it.
  //: The colour's edge, roughened (wave 8): the class is read at a point
  //: that wanders by less than a cell or two (EDGE_CELLS) over a lattice
  //: EDGE_M wide, so the boundary between two biomes comes out ragged as a
  //: real one is, and not as the staircase of the raster. Two ways to
  //: wander, and they must not be one way twice: read off one lattice a
  //: step apart, the two came out of the same ridges and the edge wandered
  //: along a diagonal, holding the right angles it was meant to hide.
  //: Turned into its own lattice and taken at two sizes each, they are two
  //: motions and the edge is a line. The wander is a walk over the
  //: **ground** -- so many cells east and north -- and not over the
  //: raster's own axes, which stand at an angle to the compass that changes
  //: over the sphere. No branch on the strength: with the wander and the
  //: grain each behind an if on its uniform, the frames stalled for a
  //: second apiece on the near frames whenever both were on (measured
  //: 2026-09-11, ANGLE over D3D11), and a strength of nought is a multiply
  //: by nought.
  vec3 j = u_edge_at + apart * (u_radius / UNITS_PER_METRE / EDGE_M);
  //: The second motion on the same lattice turned and doubled -- a whole
  //: number, so a wrap of the eye's place is a wrap of it too (1.7 was
  //: not, and the edge shifted when the eye crossed one).
  vec3 k = vec3(j.z, j.x, j.y) * 2.0 + vec3(19.7, 5.3, 31.1);
  vec2 astray = vec2(
    0.65 * wave(j, EDGE_WRAP) + 0.35 * wave(j * 2.0, EDGE_WRAP * 2.0),
    0.65 * wave(k, EDGE_WRAP * 2.0) + 0.35 * wave(k * 2.0, EDGE_WRAP * 4.0)
  ) * (EDGE_CELLS * u_edge);
  vec3 wander = (pe * astray.x + pn * astray.y) * span;

  //: Four taps on a rotated grid AA_PX wide on the glass, and the pixel is
  //: their mean: of the ground's colour, each tap a **picked** class (the
  //: plan keeps the class a class, sec. 9.3), and of its wetness, each tap a
  //: yes or no. One pick at the pixel's own point drew every cell as a
  //: diamond -- the grid's shape on the screen -- wherever a cell is a few
  //: pixels: boundaries of biomes and the shores of lakes were rows of them
  //: (owner, 2026-09-11, twice). The taps are on the glass and not on the
  //: raster, so the edge is softened by the same pixels at every frame: a
  //: hair on the near ones, a cell or two on the far. Only between two
  //: lands, as before: a tap's wander that strayed onto the water keeps the
  //: tap's own class, so no shore is painted where there is none.
  float metre_px = u_units / UNITS_PER_METRE;
  //: The taps stand AA_PX apart where a cell is many pixels -- there the
  //: spread is a hair on the cell and what it softens is the cell's own
  //: shape -- and a single pixel apart where a cell is a pixel or less:
  //: on the far frames three pixels of spread turned every river into a
  //: band four pixels wide and every coast into a blur (2026-09-12).
  float aa_px = clamp(u_step / across, 1.0, AA_PX);
  //: How far from the finest level the frame stands, nought to one: the
  //: near frames cut, the far ones read shares, and this is the blend.
  float s = min(lod, 1.0);
  float wet = 0.0;
  vec3 ground = vec3(0.0);
  for (int t = 0; t < 4; t++) {
    vec2 o = aa_px * (t == 0 ? vec2(0.375, 0.125) : t == 1 ? vec2(-0.125, 0.375) : t == 2 ? vec2(-0.375, -0.125) : vec2(0.125, -0.375)) * 2.0;
    vec3 p = here + (pe * o.x + pn * o.y) * (metre_px / metres);
    vec2 tuv = atlasUV(normalize(p));
    //: The water's edge is cut on the cubic reading where a cell is a
    //: pixel or more (level nought), and on the level's own bilinear blend
    //: from there out: past a cell to the pixel the cubic would alias what
    //: the mip averages, and the kink it removes is under a pixel anyway.
    //: Behind a branch on the fragment's own level, not on a uniform (the
    //: stalls of 2026-09-11 were gates on uniforms): the far frames would
    //: otherwise pay nine fetches of the finest level per tap, each a
    //: likely cache miss, for a weight of nought.
    float th = heightAt(tuv, lod);
    if (s < 1.0) th = mix(heightCubic(tuv), th, s);
    //: The water at every frame. Near, a cut: the lake's share and the
    //: river's ribbon are read at the finest level and cut at the bank
    //: (BANK_SHARE, the vault's field.pipeline.ribbon), so the shore falls
    //: between the cells. Far, a share: the levels above the first are the
    //: mean of that cut -- how much of the texel is water -- and a river
    //: narrower than a pixel is drawn as the share of the pixel it wets.
    //: Read at the finest level from a pixel that covered ten cells, a
    //: river was drawn where the one texel read happened to be channel and
    //: not where it was not: a line of dashes on the region's frame and
    //: nothing at all on the planet's (owner, 2026-09-12: the rivers
    //: break). A river's share goes through a ramp (RIVER_FAINT to
    //: RIVER_FULL): the halo the blend between texels lays beside a line
    //: is cut away and the line itself is drawn whole, as a map draws a
    //: river wider than it is so that it is seen; a lake and the sea keep
    //: their share -- they are areas.
    float lake0 = textureLod(u_wet, tuv, 0.0).r;
    float run0 = textureLod(u_stream, tuv, 0.0).r;
    float cut = (th < 0.0 || lake0 > BANK_SHARE || run0 > BANK_SHARE) ? 1.0 : 0.0;
    float lake_far = textureLod(u_wet, tuv, lod).r;
    float run_far = smoothstep(RIVER_FAINT, RIVER_FULL, textureLod(u_stream, tuv, lod).r);
    float far = max(th < 0.0 ? 1.0 : 0.0, max(lake_far, run_far));
    wet += 0.25 * mix(cut, far, s);
    uint tb = textureLod(u_biome, tuv, lod).r;
    uint near = textureLod(u_biome, atlasUV(normalize(p + wander)), lod).r;
    tb = (tb != ${NO_BIOME}u && near != ${NO_BIOME}u) ? near : tb;
    //: A sea cell whose height, read between the cells, has come up over
    //: zero is the shore's last strip: it takes the coast's colour.
    int code = tb == ${NO_BIOME}u ? u_shore : int(tb);
    ground += 0.25 * u_biomes[code < 0 ? ${PALETTE_SLOTS - 1} : min(code, ${PALETTE_SLOTS - 1})];
  }
  //: The ground's colour before any light: the biome layer draws it flat.
  vec3 flat_ground = ground;
  //: The landform, at the pixel's own wandered point: the picture roughens
  //: the ground by what the form says (the grain), and a form is often a
  //: single cell -- read straight it was a hard diamond.
  uint f = textureLod(u_form, uv, lod).r;
  //: And the biome there, for the grain, read at the same wandered point
  //: -- a grain that switched by the raster's own cells under a colour
  //: that wanders would show the diamonds again; the shore's on a sea cell.
  //: A passport with no shore biome says so with a code under nought: the
  //: spare slot, as the colour takes it, not the first biome's grain.
  int shore_code = u_shore < 0 ? ${PALETTE_SLOTS - 1} : u_shore;
  int gcode = shore_code;
  {
    uint b0 = textureLod(u_biome, uv, lod).r;
    vec2 wuv = atlasUV(normalize(here + wander));
    uint form_near = textureLod(u_form, wuv, lod).r;
    uint biome_near = textureLod(u_biome, wuv, lod).r;
    f = b0 != ${NO_BIOME}u ? form_near : f;
    gcode = b0 != ${NO_BIOME}u ? int(biome_near != ${NO_BIOME}u ? biome_near : b0) : shore_code;
  }

  //: One colour for all water at the surface, and the sea darkens only
  //: with its depth (owner, 2026-09-11: a river and the sea are one water,
  //: they may be one colour). A river used to end at the shore in the
  //: lake's tone and the sea begin in its own, and the step between them
  //: was a seam across every mouth.
  //: On the far frames the water on the land takes a share of the deep
  //: tone: a river drawn as a line reads by being darker than the ground,
  //: and the lake's own light tone made every river a bright thread.
  //: The season's temperature of the cell (D-334): the mean the field
  //: gives, swung by the sine of the latitude -- the ball's z -- and the
  //: swing of the pole as of now. Snow lies below the snow line, whole a
  //: band under it, and a dry cold keeps half; the water is ice below its
  //: own line, a little colder.
  float t_c = u_temp_min + textureLod(u_temp, uv, lod).r * 255.0 * u_temp_step;
  float rain01 = textureLod(u_rain, uv, lod).r;
  float t_now = t_c + u_season_c * here.z;
  //: Edges the right way round: a smoothstep with edge0 over edge1 is
  //: undefined by the spec, so the cold end is one minus the warm ramp.
  float snow = (1.0 - smoothstep(u_snow.x - u_snow.y, u_snow.x, t_now))
    * mix(u_snow_dry.y, 1.0, smoothstep(0.0, max(u_snow_dry.x, 1e-3), rain01));
  float ice = 1.0 - smoothstep(u_snow.z - u_snow.y, u_snow.z, t_now);
  //: The weather (D-335): the cover of the sky over this point, pulled by
  //: the ground's own rain share, gated to cloud and to rain -- the law
  //: of weatherGlsl.ts, the same the engine reads. The clouds show on the
  //: far frames alone, and only with the overlay on; a cloud's shadow
  //: falls away from the sun by the cloud's height over the sun's climb,
  //: so it is the cloud toward the sun by as much that shades this ground.
  float cover = wxCover(here) + u_wx_bias * (rain01 - 0.5);
  float cloud = smoothstep(u_wx_gates.x, u_wx_gates.y, cover);
  float rain_now = smoothstep(u_wx_gates.z, u_wx_gates.w, cover);
  //: By the frame's own scale (u_units), not the pixel's ground: toward
  //: the limb of the ball a pixel covers more ground, and gated by that
  //: the clouds showed at the edges of the globe and hid in its middle
  //: (owner, 2026-09-12).
  float far_sky = smoothstep(CLOUD_NEAR_MPX, CLOUD_FAR_MPX, u_units / UNITS_PER_METRE) * u_clouds;
  float cloud_alt = max(asin(clamp(high, 0.0, 1.0)), CLOUD_SUN_MIN);
  float cloud_turn = (CLOUD_KM * 1000.0 / tan(cloud_alt)) / metres;
  vec3 q_cloud = here * cos(cloud_turn) + sunward * sin(cloud_turn);
  float cloud_over = smoothstep(u_wx_gates.x, u_wx_gates.y, wxCover(q_cloud) + u_wx_bias * (rain01 - 0.5));
  vec3 water_col = mix(u_lake, u_sea_deep, h >= 0.0 ? FAR_WATER_DEEP * s : clamp(-h / u_deep, 0.0, 1.0));
  water_col = mix(water_col, ICE_TONE, ice);
  water_col *= 0.85 + 0.15 * shade;
  //: And the cast shadow lies on the water as on the land (owner,
  //: 2026-09-12): a lake under a range is in its shadow at dusk, and a
  //: river running out of the shadow into the light was a bright thread
  //: through a dark valley.
  water_col *= lit;
  //: Water is darker than the land in the dark: on top of the night's tint
  //: it keeps NIGHT_WATER of its light, or the rivers glow on the night side.
  water_col *= mix(NIGHT_WATER, 1.0, daylight);
  float share = clamp(h / u_relief, 0.0, 1.0);
  ground = mix(ground, u_high, 0.6 * smoothstep(u_high_from, 1.0, share));
  float tone = (AMBIENT + (1.0 - AMBIENT) * shade) * lit;
  tone *= 1.0 - CLOUD_SHADE * cloud_over * far_sky * step(0.0, high) * u_sunlit;
  //: The lie of the land at the frame's scale: how far the point stands
  //: over or under the mean of the ground about it, read RELIEF_LEVELS
  //: levels coarser -- the valley and the ridge as wholes. A valley floor
  //: darker along its length and a crest lighter, at every frame: that is
  //: what makes the shape read where one slope alone reads as nothing
  //: (owner, 2026-09-12: the relief must read without its lines). A cliff
  //: used to be cut darker by its form on top of all this and ticked with
  //: hachures in the vector layer; the owner read the pair as a smear with
  //: black lines on it, and a wall now reads by its own shadow and stone.
  float around = heightAt(atlasAt(place, marginOf(lod + RELIEF_LEVELS)), lod + RELIEF_LEVELS);
  float lie = clamp((h - around) / RELIEF_M, -1.0, 1.0);
  tone *= 1.0 + RELIEF_DEPTH * lie;
  //: The grain, on the near frames alone (wave 8): the ground says what
  //: it is made of, while the hillshade goes on saying what shape it is.
  float rock = textureLod(u_rock, uv, lod).r;
  tone *= 1.0 + GRAIN_DEPTH * u_grain * grainOf(apart, f, gcode, rock) * (1.0 - SNOW_OVER_GRAIN * snow);
  ground *= tone;
  //: The snow, over the ground and lit as the ground is.
  ground = mix(ground, SNOW_TONE * tone, snow);
  //: The pixel: its ground and its water, by how many of its taps are wet.
  vec3 col = mix(ground, water_col, wet);
  //: The clouds, lit as the ground under them is lit, before the night
  //: tints them with everything else.
  col = mix(col, CLOUD_TONE * (CLOUD_NIGHT + (1.0 - CLOUD_NIGHT) * lit), cloud * far_sky * CLOUD_OPACITY);
  col = mix(col * NIGHT_TINT, col, daylight);
${LAYERS_GLSL}
  float alpha = 1.0 - smoothstep(1.0 - edge, 1.0 + edge, rho);
  o_color = vec4(col * alpha, alpha);
}
`;
