// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The weather in TypeScript (D-335, D-336): the law the fragment draws by
 * (`weatherGlsl.ts`) and the engine reads (`climate.weather_at`), in
 * arithmetic the probe and the tests can call. One law in three places,
 * so the numbers are the vault's (`weather.*`, `terrain.wind_belts`) and
 * the hash is on whole numbers: what this says is raining is what the map
 * shows and the engine reads, to the blend's float.
 *
 * The sky is made of systems (D-336 item 13): each cell of a lattice in
 * latitude and longitude bears one cloud system per life, drifting with
 * the wind of its own row, spinning where the wind shears, born and gone
 * on a phase of its own. What `weatherGlsl.ts` says of it holds here.
 */

import {
  WX_CLEAR_HIGH,
  WX_HASH,
  WX_LIFE_SLICES,
  WX_MIX,
  WX_OCTAVE_2,
  WX_SHEAR_DEG,
  WX_SIZE_MAX,
  WX_SIZE_MIN,
  WX_TEX_FLOOR,
} from "./weatherGlsl";

export type WeatherLaw = {
  /** A cell of the lattice, degrees of arc: `weather.cell_km` on the planet's radius. */
  cellDeg: number;
  /** The wind's drift, degrees a real day. */
  windDeg: number;
  /** Over how many real days the systems form and dissolve: one slice; a system lives WX_LIFE_SLICES of them. */
  changeDays: number;
  /** How far the ground's own rain share stretches the cover, about a half (D-336: a factor, not a summand). */
  bias: number;
  /** The gates: cover to cloud from..full, cover to rain from..full. */
  cloudFrom: number;
  cloudFull: number;
  rainFrom: number;
  rainFull: number;
  /** How far the cover's heap about a half is spread before the gates (`weather.gain`). */
  gain: number;
  /** The belts of the wind (`terrain.wind_belts`), radians of latitude: to
   *  the trades' edge the wind is west, to the westerlies' edge east, past
   *  it west again; the wind turns over `weather.belt_edge_deg`, and that
   *  is where it shears and the systems spin. */
  tradeLat: number;
  westerlyLat: number;
  beltEdge: number;
  /** How fast a system spins at full shear, radians a real day (`weather.eddy_turn_deg`). */
  spin: number;
};

/** A law with nothing in it: no cloud and no rain anywhere. */
export const WEATHER_FALLBACK: WeatherLaw = {
  cellDeg: 30,
  windDeg: 0,
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
  spin: 0,
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
  //: The belts are the rain march's (`terrain.wind_belts`): the systems
  //: drift by the same wind the rain fell by.
  const belts = constants["terrain.wind_belts"];
  const beltOf = (key: string, fallback: number) => {
    const value = belts && typeof belts === "object" ? Number((belts as Record<string, unknown>)[key]) : NaN;
    return Number.isFinite(value) ? value : fallback;
  };
  return {
    cellDeg: (360 * cellM) / (2 * Math.PI * radiusM),
    windDeg: num("weather.wind_deg_per_day", 0),
    changeDays: Math.max(num("weather.change_days", 1), 1e-3),
    bias: num("weather.wet_bias", 0),
    cloudFrom: num("weather.cloud_from", 2),
    cloudFull: num("weather.cloud_full", 3),
    rainFrom: num("weather.rain_from", 2),
    rainFull: num("weather.rain_full", 3),
    gain: num("weather.gain", 1),
    tradeLat: beltOf("trade_lat", 0) * RAD,
    westerlyLat: beltOf("westerly_lat", 90) * RAD,
    beltEdge: Math.max(num("weather.belt_edge_deg", 1), 1e-3) * RAD,
    spin: num("weather.eddy_turn_deg", 0) * RAD,
  };
}

const MS_PER_REAL_DAY = 86_400_000;

/** Real days since the world's epoch at the moment; nought without one. */
export function daysSince(epoch: string | null, atMs: number): number {
  return epoch ? (atMs - new Date(epoch).getTime()) / MS_PER_REAL_DAY : 0;
}

/** The integer hash of a lattice corner and a slice, nought to one in
 *  steps of a sixteen-millionth: `Math.imul` is the GPU's uint multiply. */
export function wxHash(x: number, y: number, z: number, w: number): number {
  let n =
    (Math.imul(x, WX_HASH[0]) ^ Math.imul(y, WX_HASH[1]) ^ Math.imul(z, WX_HASH[2]) ^ Math.imul(w, WX_HASH[3])) >>> 0;
  n = Math.imul(n ^ (n >>> 16), WX_MIX) >>> 0;
  n = Math.imul(n ^ (n >>> 16), WX_MIX) >>> 0;
  n = (n ^ (n >>> 16)) >>> 0;
  return (n & 0xffffff) / 16777216;
}

const smooth = (f: number) => f * f * (3 - 2 * f);
/** The GPU's own mix: exact at both ends. */
const lerp = (a: number, b: number, t: number) => a * (1 - t) + b * t;

/** A value noise on the plane, on the system's own corners (z is the system). */
function wxNoise2(x: number, y: number, z: number, w: number): number {
  const ix = Math.floor(x);
  const iy = Math.floor(y);
  const fx = smooth(x - ix);
  const fy = smooth(y - iy);
  const n0 = lerp(wxHash(ix, iy, z, w), wxHash(ix + 1, iy, z, w), fx);
  const n1 = lerp(wxHash(ix, iy + 1, z, w), wxHash(ix + 1, iy + 1, z, w), fx);
  return lerp(n0, n1, fy);
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

/** The eastward wind at a latitude (radians), minus one to one. */
export function windEast(law: WeatherLaw, lat: number): number {
  return 2 * windWest(law, Math.sin(lat)) - 1;
}

/** The wind's shear at a latitude, minus one to one: positive where the
 *  eastward wind falls off poleward (the polar front -- cyclones), negative
 *  where it rises (the subtropical edge -- anticyclones), nought in the
 *  middle of a belt. */
export function windShear(law: WeatherLaw, lat: number): number {
  const d = WX_SHEAR_DEG * RAD;
  const a = Math.abs(lat);
  const s = (windEast(law, a + d) - windEast(law, Math.max(0, a - d))) / (2 * d);
  return Math.min(1, Math.max(-1, (-s * law.beltEdge) / 3));
}

/** The cover of the sky over a point at so many real days since the
 *  epoch, nought to one, before the gain and the ground's wetness: the
 *  union of the systems that reach the point. */
export function weatherSky(law: WeatherLaw, latDeg: number, lonDeg: number, days: number): number {
  const cell = law.cellDeg;
  const nRows = Math.max(2, Math.round(180 / cell));
  const dr = 180 / nRows;
  const r0 = Math.floor((latDeg + 90) / dr);
  let keep = 1;
  for (let r = r0 - 1; r <= r0 + 1; r++) {
    if (r < 0 || r >= nRows) continue;
    const latR = -90 + (r + 0.5) * dr;
    const nCols = Math.max(4, Math.round((360 * Math.cos(latR * RAD)) / cell));
    const dc = 360 / nCols;
    const speed = law.windDeg * windEast(law, latR * RAD);
    const c0 = Math.floor((lonDeg - speed * days) / dc);
    for (let c = c0 - 1; c <= c0 + 1; c++) {
      const cc = ((c % nCols) + nCols) % nCols;
      //: The system's life: which life the cell is on, and how far along.
      const phase = wxHash(cc, r, 0, 11);
      const life = days / (WX_LIFE_SLICES * law.changeDays) + phase;
      const k = Math.floor(life);
      const age = life - k;
      const env = Math.sin(Math.PI * age);
      const jx = wxHash(cc, r, k, 12) - 0.5;
      const jy = wxHash(cc, r, k, 13) - 0.5;
      const size = WX_SIZE_MIN + (WX_SIZE_MAX - WX_SIZE_MIN) * wxHash(cc, r, k, 14);
      const latS = latR + jy * dr;
      const lonS = (c + 0.5 + jx) * dc + speed * days;
      const dlon = ((((lonDeg - lonS + 180) % 360) + 360) % 360) - 180;
      const dx = (dlon * Math.cos(latS * RAD)) / cell;
      const dy = (latDeg - latS) / cell;
      const d2 = (dx * dx + dy * dy) / (size * size);
      if (d2 >= 1) continue;
      const fall = (1 - d2) * (1 - d2);
      const z = windShear(law, latS * RAD);
      const hemi = latS >= 0 ? 1 : -1;
      //: The spin: faster in the core than at the rim, so the texture
      //: winds into a spiral over the system's life.
      const theta = law.spin * z * hemi * age * WX_LIFE_SLICES * law.changeDays * (1 - d2);
      const cs = Math.cos(theta);
      const sn = Math.sin(theta);
      const ux = ((dx * cs + dy * sn) / size) * 2 + 7 * jx;
      const uy = ((-dx * sn + dy * cs) / size) * 2 + 7 * jy;
      const sid = (r * 4096 + cc) * 64 + (k & 63);
      let tex = (1 - WX_OCTAVE_2) * wxNoise2(ux, uy, sid, 21) + WX_OCTAVE_2 * wxNoise2(ux * 2, uy * 2, sid, 22);
      tex = WX_TEX_FLOOR + (1 - WX_TEX_FLOOR) * tex;
      const clear = 1 - WX_CLEAR_HIGH * Math.max(0, -z);
      keep *= 1 - tex * env * fall * clear;
    }
  }
  return 1 - keep;
}

/** The cover spread by the gain about a half: the law's own number, as the
 *  fragment's `wxCover`. */
export function weatherCover(law: WeatherLaw, latDeg: number, lonDeg: number, days: number): number {
  return Math.min(1, Math.max(0, 0.5 + (weatherSky(law, latDeg, lonDeg, days) - 0.5) * law.gain));
}

const smoothstep = (lo: number, hi: number, x: number) => {
  const t = Math.min(1, Math.max(0, (x - lo) / Math.max(hi - lo, 1e-9)));
  return t * t * (3 - 2 * t);
};

/** The ground under a sky: its annual rain share (the raster, nought to
 *  one), whether it is the sea -- whose rain share is a hole in the raster,
 *  not a measure -- and what the sea reads instead: the land's mean share
 *  (`passport.sea_wet`, the engine's `Field.land_rain`). */
export type Ground = { rain01: number; sea: boolean; seaWet: number };

/** The rain share the sky is stretched by: the ground's own, or the land's
 *  mean over the sea -- so no coast is drawn by the clouds. */
export function skyWetness(ground: Ground): number {
  return ground.sea ? ground.seaWet : ground.rain01;
}

/** The weather at a point: how clouded the sky is and how hard it rains,
 *  nought to one each, from the cover stretched by the ground's rain
 *  share. */
export function weatherAt(
  law: WeatherLaw,
  latDeg: number,
  lonDeg: number,
  ground: Ground,
  days: number,
): { cloud: number; rain: number } {
  //: A factor, not a summand (D-336): the wet windward slope thickens what
  //: the wind brings and the dry lee thins it, and neither makes weather
  //: of a clear sky.
  const cover = Math.min(
    1,
    Math.max(0, weatherCover(law, latDeg, lonDeg, days) * (1 + law.bias * (2 * skyWetness(ground) - 1))),
  );
  return {
    cloud: smoothstep(law.cloudFrom, law.cloudFull, cover),
    rain: smoothstep(law.rainFrom, law.rainFull, cover),
  };
}

/** What the fragment is handed for the moment: the days since the epoch
 *  and the slice's length, with the law's own numbers. */
export function weatherMoment(law: WeatherLaw, days: number): { days: number; changeDays: number } {
  return { days, changeDays: law.changeDays };
}
