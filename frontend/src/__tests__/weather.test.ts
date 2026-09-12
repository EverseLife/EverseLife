// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The weather (D-335, D-336): the law in TypeScript against the engine's
 * goldens, its belts and its hold, and what of it the fragment carries.
 * Out of `shade.test.ts` on 2026-09-12, at the eight-hundred-line bar.
 */

import { describe, expect, it } from "vitest";

import { FRAGMENT } from "../panels/map/fragment";
import {
  WEATHER_FALLBACK,
  weatherAt,
  weatherCover,
  weatherHold,
  weatherLaw,
  windWest,
  wxHash,
} from "../panels/map/weather";
import { WEATHER_GLSL, WX_DRIFT_2, WX_HASH, WX_MIX, WX_OCTAVE_2, WX_SLICE_2 } from "../panels/map/weatherGlsl";

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
      "weather.gain": 2.4,
      "terrain.wind_belts": { trade_lat: 30, westerly_lat: 60, edge_deg: 8 },
      "weather.block": 0.5,
      "weather.block_from": 0.25,
      "weather.block_full": 0.9,
    },
    12_000,
  );
  const point = (lat: number, lon: number): [number, number, number] => {
    const r = (lat * Math.PI) / 180;
    const l = (lon * Math.PI) / 180;
    return [Math.cos(r) * Math.cos(l), Math.cos(r) * Math.sin(l), Math.sin(r)];
  };

  it("is the engine's own law to the last bit of the hash (D-335)", () => {
    //: The numbers the engine prints for the same lattice corners and the
    //: same points (`climate._wx_hash`, `climate.weather_cover` on a law of
    //: scale four): the two repositories cannot import each other, and
    //: they meet here.
    expect(wxHash(3, -7, 12, 5)).toBeCloseTo(0.4038313031196594, 12);
    expect(wxHash(0, 0, 0, 0)).toBe(0);
    expect(law.scale).toBe(4);
    expect(weatherCover(law, point(32.66, -105.56), 0)).toBeCloseTo(0.7076900709491378, 9);
    expect(weatherCover(law, point(32.66, -105.56), 0.74)).toBeCloseTo(0.488165850892282, 9);
    expect(weatherCover(law, point(-60, 20), 3.3)).toBeCloseTo(0.15980205323178104, 9);
    expect(weatherCover(law, point(0, 0), 12.25)).toBeCloseTo(0.07784397429914314, 9);
    //: Held by the high ground (D-336): half the deck standing, all of it.
    expect(weatherCover(law, point(32.66, -105.56), 0.74, 0.5)).toBeCloseTo(0.4554350318900343, 9);
    expect(weatherCover(law, point(45, 10), 2.2, 1)).toBeCloseTo(0.5266620650409106, 9);
  });

  it("blows by the vault's belts and is held by the high ground (D-336)", () => {
    const z = (lat: number) => Math.sin((lat * Math.PI) / 180);
    //: West in the trades and past the westerlies, east between, and the
    //: march's own edge, eight degrees, centred on each -- the rain
    //: march's belts, so the clouds go the way the rain fell.
    expect(windWest(law, z(10))).toBe(0);
    expect(windWest(law, z(-10))).toBe(0);
    expect(windWest(law, z(30))).toBeCloseTo(0.5, 12);
    expect(windWest(law, z(32))).toBeCloseTo(0.84375, 12);
    expect(windWest(law, z(45))).toBe(1);
    expect(windWest(law, z(60))).toBeCloseTo(0.5, 12);
    expect(windWest(law, z(75))).toBe(0);
    expect(weatherHold(law, 0)).toBe(0);
    expect(weatherHold(law, 0.25)).toBe(0);
    expect(weatherHold(law, 0.575)).toBeCloseTo(0.25, 12);
    expect(weatherHold(law, 1)).toBe(0.5);
    //: A slice is carried for its own age: on the day it is born the
    //: field is the same whatever holds it, and a day later it is not;
    //: and the field is continuous where one slice hands over to the next.
    expect(weatherCover(law, point(45, 10), 0, 0)).toBe(weatherCover(law, point(45, 10), 0, 1));
    expect(weatherCover(law, point(45, 10), 1, 0)).not.toBe(weatherCover(law, point(45, 10), 1, 1));
    expect(Math.abs(weatherCover(law, point(20, 40), 1.5 - 1e-7) - weatherCover(law, point(20, 40), 1.5 + 1e-7))).toBeLessThan(1e-4);
    //: The wet ground stretches the cover as a factor, not a summand: with
    //: no spread the field is a half everywhere, and the driest ground
    //: thins it to 0.3 (clear), the wettest thickens it to 0.7 (overcast),
    //: the middling leaves it at the half -- a quarter of the way into
    //: the cloud's gate.
    const flat = { ...law, gain: 0 };
    expect(weatherAt(flat, 10, 10, 0, 0, 0).cloud).toBe(0);
    expect(weatherAt(flat, 10, 10, 1, 0, 0).cloud).toBe(1);
    expect(weatherAt(flat, 10, 10, 0.5, 0, 0).cloud).toBeCloseTo(0.15625, 12);
  });

  it("gates the cover to cloud and to rain, pulled by the ground's own rain", () => {
    const wet = weatherAt(law, 32.66, -105.56, 1, 0, 0);
    const dry = weatherAt(law, 32.66, -105.56, 0, 0, 0);
    expect(wet.cloud).toBeGreaterThanOrEqual(dry.cloud);
    expect(wet.rain).toBeGreaterThanOrEqual(dry.rain);
    for (const v of [wet.cloud, wet.rain, dry.cloud, dry.rain]) {
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThanOrEqual(1);
    }
    //: No book, no weather: nothing is clouded, nothing rains.
    expect(weatherAt(WEATHER_FALLBACK, 10, 10, 1, 0, 5)).toEqual({ cloud: 0, rain: 0 });
    expect(weatherLaw(null, 12_000)).toBe(WEATHER_FALLBACK);
  });

  it("is drawn in the fragment: the field, the clouds on the far frames, their shadow, the hour's rain layer", () => {
    expect(FRAGMENT).toContain("uniform float u_wx_scale;");
    expect(FRAGMENT).toContain("float wxHash(ivec3 c, int w)");
    //: The law's shape is written once and put into the GLSL: the numbers
    //: the TypeScript law is built of are the ones the shader carries.
    for (const k of WX_HASH) expect(WEATHER_GLSL).toContain(`${k}u`);
    expect(WEATHER_GLSL).toContain(`${WX_MIX}u`);
    expect(WEATHER_GLSL).toContain(`age0 * ${WX_DRIFT_2.toFixed(1)}`);
    expect(WEATHER_GLSL).toContain("float wxWest(float z)");
    expect(WEATHER_GLSL).toContain("float wxHold(float h_m)");
    expect(WEATHER_GLSL).toContain("float wxField(vec3 p, float turn)");
    expect(WEATHER_GLSL).toContain(`wi + ${WX_SLICE_2}`);
    expect(WEATHER_GLSL).toContain(`${(1 - WX_OCTAVE_2).toFixed(2)} * n1 + ${WX_OCTAVE_2.toFixed(2)} * n2`);
    expect(WEATHER_GLSL).toContain("* u_wx_gain");
    expect(FRAGMENT).toContain("float cover = clamp(wxCover(here, hold) * (1.0 + u_wx_bias * (2.0 * rain01 - 1.0)), 0.0, 1.0);");
    expect(FRAGMENT).toContain("smoothstep(CLOUD_NEAR_MPX, CLOUD_FAR_MPX, u_units / UNITS_PER_METRE) * u_clouds");
    expect(FRAGMENT).toContain("tone *= 1.0 - CLOUD_SHADE * cloud_over * far_sky * smoothstep(0.0, TWILIGHT, high) * u_sunlit;");
    //: The clouds are lit past the ground's terminator by their own
    //: height and never by the ground's cast shadow (D-336).
    expect(FRAGMENT).toContain("cloud_sun = mix(1.0, smoothstep(-lift, -lift + TWILIGHT, high_sky), u_sunlit);");
    //: The clouds are drawn on their own shell, and the drawing runs to
    //: its rim on the far frames (D-336 item 7).
    expect(FRAGMENT).toContain("if (rho > (far_sky > 0.0 ? shell : 1.0) + edge) discard;");
    expect(FRAGMENT).toContain("vec3 sky = normalize(up * zc + east * X + north * Y);");
    //: The grain of a cloud (item 9) frays the drawn edge and mottles the
    //: body; the gate the probe reads is the vault's.
    expect(FRAGMENT).toContain("float wxDetail(vec3 p, float hold)");
    expect(FRAGMENT).toContain("float wxGrain(vec3 p, float turn)");
    expect(FRAGMENT).toContain("cover_sky + CLOUD_DETAIL * (fine - 0.5)");
    expect(FRAGMENT).toContain("float cloud = smoothstep(u_wx_gates.x, u_wx_gates.y, cover);");
    expect(FRAGMENT).not.toContain("(1.0 - CLOUD_NIGHT) * lit");
    expect(FRAGMENT).toContain("wx_col * w_weather");
    //: The temperature layer and the soil's moisture read the moment's
    //: temperature now (D-334), not the year's mean.
    expect(FRAGMENT).toContain("clamp((t_now - u_temp_cold)");
    expect(FRAGMENT).toContain("u_dry.z * (t_now - u_dry.w)");
  });
});
