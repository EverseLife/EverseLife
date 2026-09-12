// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The GLSL of the weather (D-335, D-336): cloud and rain as a field over
 * the sphere that is a function of the place and the moment, and nothing
 * else.
 *
 * The same law the engine reads (`climate.weather_cover`) on the same
 * numbers of the book, so what the picture shows raining is what the
 * engine reads as rain there. The lattice is the unit ball scaled to the
 * vault's cell (`weather.cell_km`), turned about the pole by the wind of
 * the latitude's belt -- west in the trades and the polar easterlies, east
 * in the westerlies, the rain march's own belts -- for the age of each
 * slice of time, and the field is two slices of a value noise on an
 * **integer hash** blended over `weather.change_days`: an integer hash,
 * because float sines differ between a GPU and a CPU and a lattice of
 * whole numbers does not -- the three copies of this law (here,
 * `weather.weatherAt`, the engine) agree to the last bit of the hash and
 * differ only by the blend's float. Two octaves, the finer drifting a
 * little faster, so the systems turn as they go: that is all a cyclone is
 * at this size. Where the belts meet, the westward and the eastward
 * fields crossfade over the belt's edge; over the high ground a standing
 * field is blended in (`weather.block`), so the systems stall and pile up
 * on the ranges and stream past them -- blends, never a turn that varies
 * with the place, which would stretch the cells into threads along the
 * parallels (D-336). The value noise heaps about a half, and the vault's
 * gain spreads it so the gates are reachable at both ends.
 *
 * Glued into the fragment after its uniforms: it names u_wx_scale,
 * u_wx_spin, u_wx_slice, u_wx_belts, u_wx_block, u_wx_gain and u_relief.
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

//: The rain march's own edge: a smooth step a belt's edge wide, centred.
float wxBelt(float x) {
  float t = clamp(x + 0.5, 0.0, 1.0);
  return t * t * (3.0 - 2.0 * t);
}

//: How much of the westerlies blow at the ball's z: one between the
//: belts' edges, nought in the trades and past the westerlies.
float wxWest(float z) {
  float a = abs(asin(clamp(z, -1.0, 1.0)));
  return wxBelt((a - u_wx_belts.x) / u_wx_belts.z) * (1.0 - wxBelt((a - u_wx_belts.y) / u_wx_belts.z));
}

//: How much of the deck stands still over ground this high, metres:
//: nought on the plain, the block over the tallest ground.
float wxHold(float h_m) {
  return u_wx_block.x * smoothstep(u_wx_block.y, u_wx_block.z, clamp(h_m / u_relief, 0.0, 1.0));
}

vec3 wxTurned(vec3 p, float a, float k) {
  return vec3(p.x * cos(a) - p.y * sin(a), p.x * sin(a) + p.y * cos(a), p.z) * k;
}

//: One field, nought to one before the gain: two slices of time blended,
//: each carried for its own age by a turn of so many radians a slice's
//: life (signed: east positive; nought stands still), two octaves.
float wxField(vec3 p, float turn) {
  int wi = int(floor(u_wx_slice));
  float age0 = u_wx_slice - float(wi);
  float age1 = age0 - 1.0;
  float wf = age0 * age0 * (3.0 - 2.0 * age0);
  float n1 = mix(wxNoise(wxTurned(p, -turn * age0, u_wx_scale), wi), wxNoise(wxTurned(p, -turn * age1, u_wx_scale), wi + 1), wf);
  float n2 = mix(
    wxNoise(wxTurned(p, -turn * age0 * ${WX_DRIFT_2.toFixed(1)}, u_wx_scale * 2.0), wi + ${WX_SLICE_2}),
    wxNoise(wxTurned(p, -turn * age1 * ${WX_DRIFT_2.toFixed(1)}, u_wx_scale * 2.0), wi + ${WX_SLICE_2 + 1}),
    wf
  );
  return ${(1 - WX_OCTAVE_2).toFixed(2)} * n1 + ${WX_OCTAVE_2.toFixed(2)} * n2;
}

//: The cover of the sky over a point of the ball, nought to one, before
//: the ground's own wetness stretches it: the westward and the eastward
//: fields crossfaded over the belt's edge, the standing field blended in
//: by the hold of the ground -- one field where neither applies.
float wxCover(vec3 p, float hold) {
  float west = wxWest(p.z);
  float moving;
  if (west <= 0.0) moving = wxField(p, -u_wx_spin);
  else if (west >= 1.0) moving = wxField(p, u_wx_spin);
  else moving = mix(wxField(p, -u_wx_spin), wxField(p, u_wx_spin), west);
  float raw = hold > 0.0 ? mix(moving, wxField(p, 0.0), hold) : moving;
  return clamp(0.5 + (raw - 0.5) * u_wx_gain, 0.0, 1.0);
}
`;
