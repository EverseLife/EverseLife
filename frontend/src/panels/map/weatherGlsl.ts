// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The GLSL of the weather (D-335): cloud and rain as a field over the
 * sphere that is a function of the place and the moment, and nothing else.
 *
 * The same law the engine reads (`climate.weather_cover`) on the same
 * numbers of the book, so what the picture shows raining is what the
 * engine reads as rain there. The lattice is the unit ball scaled to the
 * vault's cell (`weather.cell_km`), turned about the pole by the wind's
 * drift as the days go, and the field is two slices of a value noise on
 * an **integer hash** blended over `weather.change_days`: an integer hash,
 * because float sines differ between a GPU and a CPU and a lattice of
 * whole numbers does not -- the three copies of this law (here,
 * `weather.weatherAt`, the engine) agree to the last bit of the hash and
 * differ only by the blend's float. Two octaves, the finer drifting a
 * little faster, so the systems turn as they go: that is all a cyclone is
 * at this size. The value noise heaps about a half, and WX_GAIN spreads
 * it so the gates the vault sets are reachable at both ends.
 *
 * Glued into the fragment after its uniforms: it names u_wx_scale,
 * u_wx_drift, u_wx_slice and u_wx_gain.
 */

/** The law's shape, one on every side (D-335 item 2): the weight of the
 *  finer octave, how much faster it drifts, how far its slices of time are
 *  from the coarser's, and the hash's own multipliers. The gain that
 *  spreads the noise before the gates is the vault's (`weather.gain`).
 *  Written here once and put into the GLSL below, so the three copies
 *  cannot part on a number without a test noticing. */
export const WX_OCTAVE_2 = 0.35;
export const WX_DRIFT_2 = 1.6;
export const WX_SLICE_2 = 1000;
export const WX_HASH = [1597334677, 3812015801, 2798796415, 3367900313] as const;
export const WX_MIX = 0x45d9f3b;

export const WEATHER_GLSL = `
//: The integer hash of a lattice corner and a slice of time: whole
//: numbers in, so a CPU and a GPU agree (weatherGlsl.ts).
float wxHash(ivec3 c, int w) {
  uint n = uint(c.x) * ${WX_HASH[0]}u ^ uint(c.y) * ${WX_HASH[1]}u ^ uint(c.z) * ${WX_HASH[2]}u ^ uint(w) * ${WX_HASH[3]}u;
  n = (n ^ (n >> 16u)) * ${WX_MIX}u;
  n = (n ^ (n >> 16u)) * ${WX_MIX}u;
  n ^= n >> 16u;
  return float(n & 0xffffffu) / 16777216.0;
}

float wxNoise(vec3 p, int w) {
  vec3 i = floor(p);
  vec3 f = p - i;
  f = f * f * (3.0 - 2.0 * f);
  ivec3 c = ivec3(i);
  float n00 = mix(wxHash(c, w), wxHash(c + ivec3(1, 0, 0), w), f.x);
  float n10 = mix(wxHash(c + ivec3(0, 1, 0), w), wxHash(c + ivec3(1, 1, 0), w), f.x);
  float n01 = mix(wxHash(c + ivec3(0, 0, 1), w), wxHash(c + ivec3(1, 0, 1), w), f.x);
  float n11 = mix(wxHash(c + ivec3(0, 1, 1), w), wxHash(c + ivec3(1, 1, 1), w), f.x);
  return mix(mix(n00, n10, f.y), mix(n01, n11, f.y), f.z);
}

//: The cover of the sky over a point of the ball, nought to one, before
//: the ground's own wetness pulls it: the lattice turned by the drift,
//: two slices of time blended, two octaves.
float wxCover(vec3 p) {
  float ca = cos(u_wx_drift);
  float sa = sin(u_wx_drift);
  vec3 p1 = vec3(p.x * ca - p.y * sa, p.x * sa + p.y * ca, p.z) * u_wx_scale;
  float cb = cos(u_wx_drift * ${WX_DRIFT_2.toFixed(1)});
  float sb = sin(u_wx_drift * ${WX_DRIFT_2.toFixed(1)});
  vec3 p2 = vec3(p.x * cb - p.y * sb, p.x * sb + p.y * cb, p.z) * (u_wx_scale * 2.0);
  int wi = int(floor(u_wx_slice));
  float wf = fract(u_wx_slice);
  wf = wf * wf * (3.0 - 2.0 * wf);
  float n1 = mix(wxNoise(p1, wi), wxNoise(p1, wi + 1), wf);
  float n2 = mix(wxNoise(p2, wi + ${WX_SLICE_2}), wxNoise(p2, wi + ${WX_SLICE_2 + 1}), wf);
  return clamp(0.5 + (${(1 - WX_OCTAVE_2).toFixed(2)} * n1 + ${WX_OCTAVE_2.toFixed(2)} * n2 - 0.5) * u_wx_gain, 0.0, 1.0);
}
`;
