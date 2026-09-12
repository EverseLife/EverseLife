// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The weather in TypeScript (D-335): the law the fragment draws by
 * (`weatherGlsl.ts`) and the engine reads (`climate.weather_at`), in
 * arithmetic the probe and the tests can call. One law in three places,
 * so the numbers are the vault's (`weather.*`) and the hash is on whole
 * numbers: what this says is raining is what the map shows and the engine
 * reads, to the blend's float.
 */

import { WX_DRIFT_2, WX_HASH, WX_MIX, WX_OCTAVE_2, WX_SLICE_2 } from "./weatherGlsl";

export type WeatherLaw = {
  /** The unit ball scaled to the vault's cell: the planet's radius over `weather.cell_km`. */
  scale: number;
  /** The wind's drift of the lattice about the pole, radians a real day. */
  windPerDay: number;
  /** Over how many real days the systems form and dissolve: one slice of the field. */
  changeDays: number;
  /** How much the ground's own rain share pulls the cover, about a half. */
  bias: number;
  /** The gates: cover to cloud from..full, cover to rain from..full. */
  cloudFrom: number;
  cloudFull: number;
  rainFrom: number;
  rainFull: number;
  /** How far the noise's heap about a half is spread before the gates (`weather.gain`). */
  gain: number;
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
};

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
  return {
    scale: radiusM / cellM,
    windPerDay: (num("weather.wind_deg_per_day", 0) * Math.PI) / 180,
    changeDays: Math.max(num("weather.change_days", 1), 1e-3),
    bias: num("weather.wet_bias", 0),
    cloudFrom: num("weather.cloud_from", 2),
    cloudFull: num("weather.cloud_full", 3),
    rainFrom: num("weather.rain_from", 2),
    rainFull: num("weather.rain_full", 3),
    gain: num("weather.gain", 1),
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

function wxNoise(x: number, y: number, z: number, w: number): number {
  const ix = Math.floor(x);
  const iy = Math.floor(y);
  const iz = Math.floor(z);
  const fx = smooth(x - ix);
  const fy = smooth(y - iy);
  const fz = smooth(z - iz);
  const mix = (a: number, b: number, t: number) => a + (b - a) * t;
  const n00 = mix(wxHash(ix, iy, iz, w), wxHash(ix + 1, iy, iz, w), fx);
  const n10 = mix(wxHash(ix, iy + 1, iz, w), wxHash(ix + 1, iy + 1, iz, w), fx);
  const n01 = mix(wxHash(ix, iy, iz + 1, w), wxHash(ix + 1, iy, iz + 1, w), fx);
  const n11 = mix(wxHash(ix, iy + 1, iz + 1, w), wxHash(ix + 1, iy + 1, iz + 1, w), fx);
  return mix(mix(n00, n10, fy), mix(n01, n11, fy), fz);
}

/** The cover of the sky over a point of the unit ball at so many real days
 *  since the epoch, nought to one, before the ground's wetness pulls it. */
export function weatherCover(law: WeatherLaw, p: readonly [number, number, number], days: number): number {
  //: West to east: the sample point is turned the other way.
  const drift = -law.windPerDay * days;
  const slice = days / law.changeDays;
  const wi = Math.floor(slice);
  const wf = smooth(slice - wi);
  const turned = (a: number, k: number): [number, number, number] => [
    (p[0] * Math.cos(a) - p[1] * Math.sin(a)) * k,
    (p[0] * Math.sin(a) + p[1] * Math.cos(a)) * k,
    p[2] * k,
  ];
  const p1 = turned(drift, law.scale);
  const p2 = turned(drift * WX_DRIFT_2, law.scale * 2);
  const mix = (a: number, b: number, t: number) => a + (b - a) * t;
  const n1 = mix(wxNoise(p1[0], p1[1], p1[2], wi), wxNoise(p1[0], p1[1], p1[2], wi + 1), wf);
  const n2 = mix(
    wxNoise(p2[0], p2[1], p2[2], wi + WX_SLICE_2),
    wxNoise(p2[0], p2[1], p2[2], wi + WX_SLICE_2 + 1),
    wf,
  );
  return Math.min(1, Math.max(0, 0.5 + ((1 - WX_OCTAVE_2) * n1 + WX_OCTAVE_2 * n2 - 0.5) * law.gain));
}

const smoothstep = (lo: number, hi: number, x: number) => {
  const t = Math.min(1, Math.max(0, (x - lo) / Math.max(hi - lo, 1e-9)));
  return t * t * (3 - 2 * t);
};

/** The weather at a point: how clouded the sky is and how hard it rains,
 *  nought to one each, from the cover pulled by the ground's rain share
 *  (the annual raster, nought to one). */
export function weatherAt(
  law: WeatherLaw,
  latDeg: number,
  lonDeg: number,
  rain01: number,
  days: number,
): { cloud: number; rain: number } {
  const lat = (latDeg * Math.PI) / 180;
  const lon = (lonDeg * Math.PI) / 180;
  const p: [number, number, number] = [Math.cos(lat) * Math.cos(lon), Math.cos(lat) * Math.sin(lon), Math.sin(lat)];
  const cover = weatherCover(law, p, days) + law.bias * (rain01 - 0.5);
  return {
    cloud: smoothstep(law.cloudFrom, law.cloudFull, cover),
    rain: smoothstep(law.rainFrom, law.rainFull, cover),
  };
}

/** What the fragment is handed for the moment: the drift and the slice,
 *  with the law's own numbers. */
export function weatherMoment(law: WeatherLaw, days: number): { drift: number; slice: number } {
  return { drift: -law.windPerDay * days, slice: days / law.changeDays };
}
