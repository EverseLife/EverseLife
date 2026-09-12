// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The weather in TypeScript (D-335, D-336): the law the fragment draws by
 * (`weatherGlsl.ts`) and the engine reads (`climate.weather_at`), in
 * arithmetic the probe and the tests can call. One law in three places,
 * so the numbers are the vault's (`weather.*`, `terrain.wind_belts`) and
 * the hash is on whole numbers: what this says is raining is what the map
 * shows and the engine reads, to the blend's float -- and, over the high
 * ground, to the height each side reads (the shader its frame's mip, the
 * engine and the probe the exact one).
 *
 * The field is carried by the wind of the latitude's belt -- west in the
 * trades and the polar easterlies, east in the westerlies, the same belts
 * the vault's rain march blows by -- and each slice of it by its own age.
 * Where the belts meet, and over the high ground that holds the deck, the
 * fields are **blended**, never sheared: a field turned by an angle that
 * varies with the place stretches its cells into threads along the
 * parallels, and two fields crossfaded do not (D-336).
 */

import { WX_DRIFT_2, WX_HASH, WX_MIX, WX_OCTAVE_2, WX_SLICE_2 } from "./weatherGlsl";

export type WeatherLaw = {
  /** The unit ball scaled to the vault's cell: the planet's radius over `weather.cell_km`. */
  scale: number;
  /** The wind's drift of the lattice about the pole, radians a real day. */
  windPerDay: number;
  /** Over how many real days the systems form and dissolve: one slice of the field. */
  changeDays: number;
  /** How far the ground's own rain share stretches the cover, about a half (D-336: a factor, not a summand). */
  bias: number;
  /** The gates: cover to cloud from..full, cover to rain from..full. */
  cloudFrom: number;
  cloudFull: number;
  rainFrom: number;
  rainFull: number;
  /** How far the noise's heap about a half is spread before the gates (`weather.gain`). */
  gain: number;
  /** The belts of the wind (`terrain.wind_belts`), radians of latitude: to
   *  the trades' edge the wind is west, to the westerlies' edge east, past
   *  it west again; the two fields crossfade over the belts' own edge. */
  tradeLat: number;
  westerlyLat: number;
  beltEdge: number;
  /** How much of the deck stands still over the tallest ground
   *  (`weather.block`), from this share of the planet's rise to that one. */
  block: number;
  blockFrom: number;
  blockFull: number;
};

/** A law with nothing in it: no cloud and no rain anywhere. */
export const WEATHER_FALLBACK: WeatherLaw = {
  scale: 1,
  windPerDay: 0,
  changeDays: 1,
  bias: 0,
  cloudFrom: 2,
  cloudFull: 3,
  rainFrom: 2,
  rainFull: 3,
  gain: 1,
  tradeLat: 0,
  westerlyLat: Math.PI / 2,
  beltEdge: 1,
  block: 0,
  blockFrom: 0,
  blockFull: 1,
};

const RAD = Math.PI / 180;

/** The law off the book for a planet of this radius, metres. */
export function weatherLaw(
  constants: Record<string, unknown> | null | undefined,
  radiusM: number,
): WeatherLaw {
  if (!constants) return WEATHER_FALLBACK;
  const num = (key: string, fallback: number) => {
    const value = Number(constants[key]);
    return Number.isFinite(value) ? value : fallback;
  };
  const cellM = num("weather.cell_km", 0) * 1000;
  if (!(cellM > 0) || !(radiusM > 0)) return WEATHER_FALLBACK;
  //: The belts are the rain march's (`terrain.wind_belts`), a table of
  //: three: the clouds blow by the same wind the rain fell by, and turn
  //: over the same edge.
  const belts = constants["terrain.wind_belts"];
  const beltOf = (key: string, fallback: number) => {
    const value = belts && typeof belts === "object" ? Number((belts as Record<string, unknown>)[key]) : NaN;
    return Number.isFinite(value) ? value : fallback;
  };
  const blockFrom = num("weather.block_from", 0);
  return {
    scale: radiusM / cellM,
    windPerDay: num("weather.wind_deg_per_day", 0) * RAD,
    changeDays: Math.max(num("weather.change_days", 1), 1e-3),
    bias: num("weather.wet_bias", 0),
    cloudFrom: num("weather.cloud_from", 2),
    cloudFull: num("weather.cloud_full", 3),
    rainFrom: num("weather.rain_from", 2),
    rainFull: num("weather.rain_full", 3),
    gain: num("weather.gain", 1),
    tradeLat: beltOf("trade_lat", 0) * RAD,
    westerlyLat: beltOf("westerly_lat", 90) * RAD,
    beltEdge: Math.max(beltOf("edge_deg", 1), 1e-3) * RAD,
    block: num("weather.block", 0),
    blockFrom,
    //: A step with no width is undefined in the GLSL: the full mark stands
    //: past the from mark whatever the book says.
    blockFull: Math.max(num("weather.block_full", 1), blockFrom + 1e-3),
  };
}

const MS_PER_REAL_DAY = 86_400_000;

/** Real days since the world's epoch at the moment; nought without one. */
export function daysSince(epoch: string | null, atMs: number): number {
  return epoch ? (atMs - new Date(epoch).getTime()) / MS_PER_REAL_DAY : 0;
}

/** The integer hash of a lattice corner and a slice of time, nought to one
 *  in steps of a sixteen-millionth: `Math.imul` is the GPU's uint multiply. */
export function wxHash(x: number, y: number, z: number, w: number): number {
  let n =
    (Math.imul(x, WX_HASH[0]) ^ Math.imul(y, WX_HASH[1]) ^ Math.imul(z, WX_HASH[2]) ^ Math.imul(w, WX_HASH[3])) >>> 0;
  n = Math.imul(n ^ (n >>> 16), WX_MIX) >>> 0;
  n = Math.imul(n ^ (n >>> 16), WX_MIX) >>> 0;
  n = (n ^ (n >>> 16)) >>> 0;
  return (n & 0xffffff) / 16777216;
}

const smooth = (f: number) => f * f * (3 - 2 * f);
/** The GPU's own mix: exact at both ends, so a blend of nought is the one field. */
const lerp = (a: number, b: number, t: number) => a * (1 - t) + b * t;

function wxNoise(x: number, y: number, z: number, w: number): number {
  const ix = Math.floor(x);
  const iy = Math.floor(y);
  const iz = Math.floor(z);
  const fx = smooth(x - ix);
  const fy = smooth(y - iy);
  const fz = smooth(z - iz);
  const n00 = lerp(wxHash(ix, iy, iz, w), wxHash(ix + 1, iy, iz, w), fx);
  const n10 = lerp(wxHash(ix, iy + 1, iz, w), wxHash(ix + 1, iy + 1, iz, w), fx);
  const n01 = lerp(wxHash(ix, iy, iz + 1, w), wxHash(ix + 1, iy, iz + 1, w), fx);
  const n11 = lerp(wxHash(ix, iy + 1, iz + 1, w), wxHash(ix + 1, iy + 1, iz + 1, w), fx);
  return lerp(lerp(n00, n10, fy), lerp(n01, n11, fy), fz);
}

/** The rain march's own edge: a smooth step a belt's edge wide, centred. */
const belt = (x: number) => {
  const t = Math.min(1, Math.max(0, x + 0.5));
  return t * t * (3 - 2 * t);
};

/** How much of the westerlies blow at a latitude given by the ball's z:
 *  one between the belts' edges, nought in the trades and past the
 *  westerlies, the edge's own step between. */
export function windWest(law: WeatherLaw, z: number): number {
  const a = Math.abs(Math.asin(Math.min(1, Math.max(-1, z))));
  return belt((a - law.tradeLat) / law.beltEdge) * (1 - belt((a - law.westerlyLat) / law.beltEdge));
}

const smoothstep = (lo: number, hi: number, x: number) => {
  const t = Math.min(1, Math.max(0, (x - lo) / Math.max(hi - lo, 1e-9)));
  return t * t * (3 - 2 * t);
};

/** How much of the deck stands still over ground this high, nought to one
 *  of the planet's rise: nought on the plain, `block` over the tallest
 *  ground -- the systems pile up on the ranges and stream past them. */
export function weatherHold(law: WeatherLaw, relief01: number): number {
  return law.block * smoothstep(law.blockFrom, law.blockFull, relief01);
}

/** One field, nought to one before the gain: two slices of time blended,
 *  each carried for its own age by a turn of so many radians a slice's life
 *  (signed: east positive; nought stands still), two octaves. */
function wxField(law: WeatherLaw, p: readonly [number, number, number], days: number, turn: number): number {
  const slice = days / law.changeDays;
  const wi = Math.floor(slice);
  const wf = smooth(slice - wi);
  //: Each slice is carried for its own age -- the one fading out for the
  //: slice's life, the one fading in from before its birth -- against the
  //: wind's direction, so the field moves with it.
  const age0 = slice - wi;
  const age1 = age0 - 1;
  const turned = (a: number, k: number): [number, number, number] => [
    (p[0] * Math.cos(a) - p[1] * Math.sin(a)) * k,
    (p[0] * Math.sin(a) + p[1] * Math.cos(a)) * k,
    p[2] * k,
  ];
  const octave = (scale: number, drift: number, w0: number) => {
    const a = turned(-turn * age0 * drift, scale);
    const b = turned(-turn * age1 * drift, scale);
    return lerp(wxNoise(a[0], a[1], a[2], w0), wxNoise(b[0], b[1], b[2], w0 + 1), wf);
  };
  return (1 - WX_OCTAVE_2) * octave(law.scale, 1, wi) + WX_OCTAVE_2 * octave(law.scale * 2, WX_DRIFT_2, wi + WX_SLICE_2);
}

/** The cover of the sky over a point of the unit ball at so many real days
 *  since the epoch, nought to one, before the ground's wetness stretches
 *  it; `hold` is how much of the deck stands still there. The westward
 *  and the eastward fields crossfade over the belt's edge, and the
 *  standing field is blended in over the high ground: blends, never a
 *  turn that varies with the place. */
export function weatherCover(
  law: WeatherLaw,
  p: readonly [number, number, number],
  days: number,
  hold = 0,
): number {
  const spin = law.windPerDay * law.changeDays;
  const west = windWest(law, p[2]);
  const moving =
    west <= 0
      ? wxField(law, p, days, -spin)
      : west >= 1
        ? wxField(law, p, days, spin)
        : lerp(wxField(law, p, days, -spin), wxField(law, p, days, spin), west);
  const raw = hold > 0 ? lerp(moving, wxField(law, p, days, 0), hold) : moving;
  return Math.min(1, Math.max(0, 0.5 + (raw - 0.5) * law.gain));
}

/** The weather at a point: how clouded the sky is and how hard it rains,
 *  nought to one each, from the cover stretched by the ground's rain share
 *  (the annual raster, nought to one) over ground this high (a share of the
 *  planet's rise, nought to one). */
export function weatherAt(
  law: WeatherLaw,
  latDeg: number,
  lonDeg: number,
  rain01: number,
  relief01: number,
  days: number,
): { cloud: number; rain: number } {
  const lat = latDeg * RAD;
  const lon = lonDeg * RAD;
  const p: [number, number, number] = [Math.cos(lat) * Math.cos(lon), Math.cos(lat) * Math.sin(lon), Math.sin(lat)];
  //: A factor, not a summand (D-336): the wet windward slope thickens what
  //: the wind brings and the dry lee thins it, and neither makes weather
  //: of a clear sky -- the mountain's cloud comes and goes with the flow.
  const cover = Math.min(
    1,
    Math.max(0, weatherCover(law, p, days, weatherHold(law, relief01)) * (1 + law.bias * (2 * rain01 - 1))),
  );
  return {
    cloud: smoothstep(law.cloudFrom, law.cloudFull, cover),
    rain: smoothstep(law.rainFrom, law.rainFull, cover),
  };
}

/** What the fragment is handed for the moment: the slice the field is in
 *  and the wind's turn over a slice's life, with the law's own numbers. */
export function weatherMoment(law: WeatherLaw, days: number): { spin: number; slice: number } {
  return { spin: law.windPerDay * law.changeDays, slice: days / law.changeDays };
}
