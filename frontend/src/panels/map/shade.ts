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

export const FRAGMENT = `#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;
precision highp usampler2D;

uniform sampler2D u_height;
uniform usampler2D u_biome;
uniform usampler2D u_form;
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

out vec4 o_color;

const float PI = 3.141592653589793;
const float TAU = 6.283185307179586;
const float EXAGGERATION = ${EXAGGERATION.toFixed(1)};

float heightAt(vec2 uv) { return texture(u_height, uv).r; }

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
