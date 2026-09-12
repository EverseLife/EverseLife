// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The GLSL of the ground's grain: the value noise on the sphere, its
 * octaves, the shapes a biome's grain takes and the grain of the pixel.
 *
 * Glued into the fragment (`fragment.ts`) after its constants, which it
 * names as the fragment does -- GRAIN_M, GRAIN_WRAP, NOISE_GAIN, the
 * octaves, the shapes -- and the uniforms u_grains, u_grain_at, u_units,
 * u_radius and the landform codes. Cut out of `fragment.ts` on 2026-09-12
 * with the grain by biome (D-331 addendum), when the file crossed the
 * bar; the numbers it shapes with are the picture's (`grain.ts`).
 */

import { PALETTE_SLOTS } from "./shade";

export const GRAIN_GLSL = `
//: The wrap is the lattice's own: a biome's lattice is the base one scaled
//: and squeezed (grainOf), and its wrap is the base wrap scaled and
//: squeezed the same, so a wrap of the eye's place is a wrap of every
//: lattice and the ground does not jump when the eye crosses one.
float hash3(vec3 cell, vec3 wrap) {
  vec3 c = mod(cell, wrap);
  return fract(sin(dot(c, vec3(127.1, 311.7, 74.7))) * 43758.5453123);
}

float vnoise(vec3 p, vec3 wrap) {
  vec3 i = floor(p);
  vec3 f = p - i;
  f = f * f * (3.0 - 2.0 * f);
  float n00 = mix(hash3(i, wrap), hash3(i + vec3(1.0, 0.0, 0.0), wrap), f.x);
  float n10 = mix(hash3(i + vec3(0.0, 1.0, 0.0), wrap), hash3(i + vec3(1.0, 1.0, 0.0), wrap), f.x);
  float n01 = mix(hash3(i + vec3(0.0, 0.0, 1.0), wrap), hash3(i + vec3(1.0, 0.0, 1.0), wrap), f.x);
  float n11 = mix(hash3(i + vec3(0.0, 1.0, 1.0), wrap), hash3(i + vec3(1.0, 1.0, 1.0), wrap), f.x);
  return mix(mix(n00, n10, f.y), mix(n01, n11, f.y), f.z);
}

//: The noise about zero and spread over the whole of -1..1. Value noise is
//: a blend of eight uniform draws and so heaps about a half: taken raw, its
//: swing is a tenth, and a texture built on it comes out invisible. The
//: gain is that heap widened, and the clamp keeps the tails honest.
float wave(vec3 p, vec3 wrap) {
  return clamp((vnoise(p, wrap) - 0.5) * NOISE_GAIN, -1.0, 1.0);
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
//: metre_px is the pixel in metres of the lattice's own cell: a lattice
//: scaled finer has its octaves worth fewer pixels each.
//: The octaves take the lattice's axes in turn (xyz, yzx, zxy): octaves
//: laid on one lattice line up, and the sum reads as a grid along the
//: diagonals of the cells (seen on the scree, 2026-09-12); on turned
//: axes each octave's cells cut across the last one's. The wrap turns
//: with the axes, so every octave still wraps where the eye's place does.
float fractal(vec3 p, vec3 wrap, float metre_px) {
  float sum = 0.0;
  float amp = 1.0;
  float step = 1.0;
  for (int o = 0; o < GRAIN_OCTAVES; o++) {
    float cell_px = (GRAIN_M / step) / metre_px;
    float seen = clamp((cell_px - GRAIN_SEEN_PX) / (GRAIN_FULL_PX - GRAIN_SEEN_PX), 0.0, 1.0);
    if (seen > 0.0) {
      vec3 q = p * step;
      vec3 w = wrap * step;
      int turn = o - (o / 3) * 3;
      if (turn == 1) { q = q.yzx; w = w.yzx; }
      else if (turn == 2) { q = q.zxy; w = w.zxy; }
      sum += amp * seen * wave(q, w);
    }
    amp *= GRAIN_FALL;
    step *= 2.0;
  }
  return sum / GRAIN_WHOLE;
}

//: The raw noise shaped to what a biome's ground looks like from above
//: (grain.GRAIN_SHAPE): crowns are plateaus with soft edges, cracks and
//: frost polygons are thin lines about the noise's nought, pools lie in
//: its low and scrub stands in its high, scree is the noise sharpened.
float shapeOf(float f, int shape) {
  if (shape == SHAPE_CLUMPS) return smoothstep(-0.7, 0.7, f) * 2.0 - 1.0;
  if (shape == SHAPE_CRACKS) return -pow(1.0 - abs(f), 6.0);
  if (shape == SHAPE_POOLS) return mix(f * 0.5, -1.0, 1.0 - smoothstep(-0.55, -0.25, f));
  if (shape == SHAPE_PATCHES) return mix(f * 0.4, -0.9, smoothstep(0.3, 0.6, f));
  if (shape == SHAPE_NET) return -pow(1.0 - abs(f), 4.0) * 0.7 + 0.3 * f;
  if (shape == SHAPE_SPECKLE) return clamp(f * 1.5, -1.0, 1.0);
  return f;
}

//: The grain of the biome under the pixel (u_grains, off the vault's
//: biome.grain: owner, 2026-09-12 -- the noise was one for every biome),
//: with the landform as the material under it: dunes lie in waves
//: whatever grows on them, ice cracks, and hard rock sharpens whatever the
//: biome's own grain is. The squeeze is along one axis of the lattice,
//: which is the ball's own and not the wind's: the waves run one way over
//: the planet, and where that axis stands off the ground they fade.
float grainOf(vec3 apart, uint form, int code, float rock) {
  vec4 g = u_grains[clamp(code, 0, ${PALETTE_SLOTS - 1})];
  float scale = g.x;
  float stretch = g.y;
  float contrast = g.z;
  int shape = int(g.w + 0.5);
  bool stone = form == u_stone_forms.x || form == u_stone_forms.y
    || form == u_stone_forms.z || form == u_stone_forms.w;
  bool sand = form == u_sand_forms.x || form == u_sand_forms.y;
  bool ice = form == u_ice_forms.x || form == u_ice_forms.y || form == u_ice_forms.z;
  if (sand) stretch = min(stretch, 0.25);
  if (ice) shape = SHAPE_CRACKS;
  if (stone) contrast *= 1.5;
  float metre_px = u_units / UNITS_PER_METRE * scale;
  vec3 squeeze = vec3(1.0, stretch, 1.0);
  vec3 p = (u_grain_at + apart * (u_radius / UNITS_PER_METRE / GRAIN_M)) * scale * squeeze;
  vec3 wrap = vec3(GRAIN_WRAP * scale) * squeeze;
  float grain = clamp(shapeOf(fractal(p, wrap, metre_px), shape) * contrast, -1.0, 1.0);
  return grain * (0.7 + 0.6 * rock);
}
`;
