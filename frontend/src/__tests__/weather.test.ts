// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The weather (D-335, D-336): the law in TypeScript against the engine's
 * goldens, its belts, its shear and its systems, and what of it the
 * fragment carries. Out of `shade.test.ts` on 2026-09-12, at the
 * eight-hundred-line bar.
 */

import { describe, expect, it } from "vitest";

import { FRAGMENT } from "../panels/map/fragment";
import {
  WEATHER_FALLBACK,
  skyWetness,
  weatherAt,
  weatherCover,
  weatherLaw,
  weatherSky,
  windEast,
  windShear,
  windWest,
  wxHash,
} from "../panels/map/weather";
import {
  WEATHER_GLSL,
  WX_HASH,
  WX_LIFE_SLICES,
  WX_MIX,
  WX_OCTAVE_2,
  WX_SEA_WET,
  WX_SIZE_MAX,
  WX_SIZE_MIN,
  WX_TEX_FLOOR,
} from "../panels/map/weatherGlsl";

describe("the weather", () => {
  const law = weatherLaw(
    {
      "weather.cell_km": 3,
      "weather.wind_deg_per_day": 90,
      "weather.change_days": 1.5,
      "weather.wet_bias": 0.4,
      "weather.cloud_from": 0.45,
      "weather.cloud_full": 0.65,
      "weather.rain_from": 0.6,
      "weather.rain_full": 0.85,
      "weather.gain": 2.0,
      "terrain.wind_belts": { trade_lat: 30, westerly_lat: 60, edge_deg: 8 },
      "weather.belt_edge_deg": 25,
      "weather.eddy_turn_deg": 90,
    },
    12_000,
  );

  it("is the engine's own law to the last bit of the hash (D-335)", () => {
    //: The numbers the engine prints for the same corners and the same
    //: points (`climate._wx_hash`, `climate.weather_cover` on a law of a
    //: cell of 3 km on a radius of 12 km): the two repositories cannot
    //: import each other, and they meet here.
    expect(wxHash(3, -7, 12, 5)).toBeCloseTo(0.4038313031196594, 12);
    expect(wxHash(0, 0, 0, 0)).toBe(0);
    expect(law.cellDeg).toBeCloseTo(14.32394487827058, 9);
    expect(weatherCover(law, 32.66, -105.56, 0)).toBeCloseTo(GOLD[0], 9);
    expect(weatherCover(law, 32.66, -105.56, 0.74)).toBeCloseTo(GOLD[1], 9);
    expect(weatherCover(law, -60, 20, 3.3)).toBeCloseTo(GOLD[2], 9);
    expect(weatherCover(law, 0, 0, 12.25)).toBeCloseTo(GOLD[3], 9);
    expect(weatherCover(law, 45, 10, 2.2)).toBeCloseTo(GOLD[4], 9);
  });

  it("blows by the vault's belts and shears at their edges (D-336)", () => {
    const z = (lat: number) => Math.sin((lat * Math.PI) / 180);
    const rad = (lat: number) => (lat * Math.PI) / 180;
    //: West in the trades and past the westerlies, east between, and a
    //: turn the clouds' own edge wide centred on each belt's edge.
    expect(windWest(law, z(10))).toBe(0);
    expect(windWest(law, z(30))).toBeCloseTo(0.5, 12);
    expect(windWest(law, z(45))).toBe(1);
    expect(windWest(law, z(60))).toBeCloseTo(0.5, 12);
    expect(windWest(law, z(80))).toBe(0);
    expect(windEast(law, rad(10))).toBe(-1);
    expect(windEast(law, rad(45))).toBe(1);
    //: The shear: cyclonic on the polar front, anticyclonic on the
    //: subtropical edge, nought in the middle of a belt, alike in both
    //: hemispheres.
    expect(windShear(law, rad(60))).toBeGreaterThan(0.5);
    expect(windShear(law, rad(30))).toBeLessThan(-0.5);
    expect(windShear(law, rad(45))).toBeCloseTo(0, 12);
    expect(windShear(law, rad(-60))).toBe(windShear(law, rad(60)));
  });

  it("is made of systems that live, drift and never jump", () => {
    //: The law's shape the systems are built to: a system never reaches
    //: past the three cells about a point, and the cover is continuous
    //: over place and time.
    expect(WX_SIZE_MAX).toBeLessThanOrEqual(1.5);
    expect(WX_SIZE_MIN).toBeGreaterThan(0);
    expect(WX_TEX_FLOOR).toBeGreaterThan(0);
    expect(WX_LIFE_SLICES).toBe(2);
    for (const [lat, lon, t] of [
      [20, 40, 1.5],
      [-33, 170, 7.25],
      [61, -10, 0.3],
      [0, 0, 12.25],
    ] as const) {
      const here = weatherSky(law, lat, lon, t);
      expect(Math.abs(weatherSky(law, lat + 1e-3, lon, t) - here)).toBeLessThan(2e-3);
      expect(Math.abs(weatherSky(law, lat, lon + 1e-3, t) - here)).toBeLessThan(2e-3);
      expect(Math.abs(weatherSky(law, lat, lon, t + 1e-4) - here)).toBeLessThan(2e-3);
      expect(here).toBeGreaterThanOrEqual(0);
      expect(here).toBeLessThanOrEqual(1);
    }
    //: The same place another day is another sky.
    expect(weatherSky(law, 10, 10, 2)).not.toBe(weatherSky(law, 10, 10, 5));
  });

  it("gates the cover to cloud and to rain, stretched by the ground's own rain", () => {
    const wet = weatherAt(law, 32.66, -105.56, { rain01: 1, sea: false }, 0);
    const dry = weatherAt(law, 32.66, -105.56, { rain01: 0, sea: false }, 0);
    expect(wet.cloud).toBeGreaterThanOrEqual(dry.cloud);
    expect(wet.rain).toBeGreaterThanOrEqual(dry.rain);
    for (const v of [wet.cloud, wet.rain, dry.cloud, dry.rain]) {
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThanOrEqual(1);
    }
    //: No book, no weather: nothing is clouded, nothing rains.
    expect(weatherAt(WEATHER_FALLBACK, 10, 10, { rain01: 1, sea: false }, 5)).toEqual({ cloud: 0, rain: 0 });
    expect(weatherLaw(null, 12_000)).toBe(WEATHER_FALLBACK);
    //: The sea's rain share is a hole in the raster, not a measure: the sky
    //: over it reads the neutral half, whatever the raster says.
    expect(skyWetness({ rain01: 0, sea: true })).toBe(WX_SEA_WET);
    expect(weatherAt(law, 10, 10, { rain01: 0, sea: true }, 0).cloud).toBeCloseTo(
      weatherAt(law, 10, 10, { rain01: 0.5, sea: false }, 0).cloud,
      12,
    );
  });

  it("is drawn in the fragment: the systems, the clouds on the far frames, their shadow, the hour's rain layer", () => {
    expect(FRAGMENT).toContain("uniform float u_wx_cell;");
    expect(FRAGMENT).toContain("uniform vec2 u_wx_time;");
    expect(FRAGMENT).toContain("float wxHash(ivec3 c, int w)");
    //: The law's shape is written once and put into the GLSL: the numbers
    //: the TypeScript law is built of are the ones the shader carries.
    for (const k of WX_HASH) expect(WEATHER_GLSL).toContain(`${k}u`);
    expect(WEATHER_GLSL).toContain(`${WX_MIX}u`);
    expect(WEATHER_GLSL).toContain(`const float WX_SEA_WET = ${WX_SEA_WET.toFixed(2)};`);
    expect(WEATHER_GLSL).toContain(`${(1 - WX_OCTAVE_2).toFixed(2)} * wxNoise2(u, sid, 21)`);
    expect(WEATHER_GLSL).toContain(`${WX_SIZE_MIN.toFixed(2)} + ${(WX_SIZE_MAX - WX_SIZE_MIN).toFixed(2)} * wxHash`);
    expect(WEATHER_GLSL).toContain("void wxSky(vec3 p, out float cover, out float grain)");
    expect(WEATHER_GLSL).toContain("float wxShear(float lat)");
    expect(WEATHER_GLSL).toContain("* u_wx_gain");
    expect(FRAGMENT).toContain("float cover = clamp(wxCover(here) * (1.0 + u_wx_bias * (2.0 * wet_here - 1.0)), 0.0, 1.0);");
    expect(FRAGMENT).toContain("float wet_sky = h_sky < 0.0 ? WX_SEA_WET : textureLod(u_rain, uv_sky, lod_sky).r;");
    expect(FRAGMENT).toContain("wxSky(sky, raw_sky, fine);");
    expect(FRAGMENT).toContain("smoothstep(CLOUD_NEAR_MPX, CLOUD_FAR_MPX, u_units / UNITS_PER_METRE) * u_clouds");
    expect(FRAGMENT).toContain("tone *= 1.0 - CLOUD_SHADE * cloud_over * far_sky * smoothstep(0.0, TWILIGHT, high) * u_sunlit;");
    //: The clouds are lit past the ground's terminator by their own height
    //: and never by the ground's cast shadow (D-336), on their own shell.
    expect(FRAGMENT).toContain("cloud_sun = mix(1.0, smoothstep(-lift, -lift + TWILIGHT, high_sky), u_sunlit);");
    expect(FRAGMENT).not.toContain("(1.0 - CLOUD_NIGHT) * lit");
    expect(FRAGMENT).toContain("if (rho > (far_sky > 0.0 ? shell : 1.0) + edge) discard;");
    expect(FRAGMENT).toContain("vec3 sky = normalize(up * zc + east * X + north * Y);");
    expect(FRAGMENT).toContain("cover_sky + CLOUD_DETAIL * (fine - 0.5)");
    expect(FRAGMENT).toContain("wx_col * w_weather");
    //: The temperature layer and the soil's moisture read the moment's
    //: temperature now (D-334), not the year's mean.
    expect(FRAGMENT).toContain("clamp((t_now - u_temp_cold)");
    expect(FRAGMENT).toContain("u_dry.z * (t_now - u_dry.w)");
  });
});

//: The engine's numbers for the five points above, in its own order.
const GOLD = [0.2320697855030931, 0, 0.09181303824912512, 0.7960877399788777, 0];
