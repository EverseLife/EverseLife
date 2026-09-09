// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The pure parts of the GPU ground (landscape plan wave 5): what can be
 * held without a GPU -- the palette off the theme, the height's mip
 * chain, the codes the shader tells water and cliffs by. The shader itself
 * is checked by eye (plan §9.9).
 */

import { describe, expect, it } from "vitest";

import {
  MISSING,
  NO_BIOME,
  PALETTE_SLOTS,
  deepOf,
  formCodes,
  heightsOf,
  mipChain,
  paletteOf,
  parseColor,
  sunDirection,
} from "../panels/map/shade";

describe("parseColor", () => {
  it("reads what the browser computes: rgb, rgba, color(srgb)", () => {
    expect(parseColor("rgb(255, 0, 51)")).toEqual([1, 0, 0.2]);
    expect(parseColor("rgba(0, 128, 255, 0.5)")).toEqual([0, 128 / 255, 1]);
    expect(parseColor("color(srgb 0.5 0.25 1)")).toEqual([0.5, 0.25, 1]);
    expect(parseColor("color(srgb 0.5 0.25 1 / 0.3)")).toEqual([0.5, 0.25, 1]);
    expect(parseColor("rgb(100% 50% 0%)")).toEqual([1, 0.5, 0]);
  });
  it("names no colour for what is not one", () => {
    expect(parseColor("")).toBe(null);
    expect(parseColor("magenta")).toBe(null);
    expect(parseColor("rgb(a, b, c)")).toBe(null);
  });
});

describe("paletteOf", () => {
  it("reads each biome's token through the probe and marks a missing one", () => {
    const seen: string[] = [];
    const probe = {
      style: {
        color: "",
        setProperty: (name: string, value: string) => seen.push(`${name}=${value}`),
      },
    } as unknown as HTMLElement;
    const computed = (el: HTMLElement) => {
      const asked = el.style.color;
      if (asked.includes("--biome-forest")) return "rgb(0, 255, 0)";
      if (asked.includes("--gl-lake")) return "color(srgb 0 0 1)";
      if (asked.includes("--biome-")) return "rgb(255, 0, 255)";
      return "rgb(10, 20, 30)";
    };
    const palette = paletteOf(probe, "terra", ["forest", "nowhere"], computed);
    expect(seen).toEqual(["--pc=var(--planet-terra)"]);
    expect(palette.biomes.length).toBe(PALETTE_SLOTS * 3);
    expect(Array.from(palette.biomes.subarray(0, 3))).toEqual([0, 1, 0]);
    //: A biome without a token is magenta, never a guess at land.
    expect(Array.from(palette.biomes.subarray(3, 6))).toEqual(MISSING);
    //: An empty slot is magenta too.
    expect(Array.from(palette.biomes.subarray(6, 9))).toEqual(MISSING);
    expect(palette.lake).toEqual([0, 0, 1]);
    expect(palette).not.toHaveProperty("river");
    expect(palette.seaDeep).toEqual([10 / 255, 20 / 255, 30 / 255]);
  });
});

describe("mipChain", () => {
  it("halves to one cell, averaging two by two and folding an odd edge in", () => {
    const level0 = Float32Array.from([1, 2, 3, 4, 5, 6, 7, 8, 9]);
    const chain = mipChain(level0, 3, 3);
    expect(chain.map((l) => [l.cols, l.rows])).toEqual([
      [3, 3],
      [1, 1],
    ]);
    //: The one cell of level 1 is the mean of the top-left two by two.
    expect(chain[1].data[0]).toBe((1 + 2 + 4 + 5) / 4);
    const wide = mipChain(Float32Array.from([0, 2, 4, 6]), 4, 1);
    expect(wide.map((l) => l.cols)).toEqual([4, 2, 1]);
    expect(Array.from(wide[1].data)).toEqual([1, 5]);
    expect(wide[2].data[0]).toBe(3);
  });
  it("reads the wire's signed metres as floats", () => {
    const bytes = new Int16Array([-2000, 0, 3000]).buffer;
    expect(Array.from(heightsOf(bytes))).toEqual([-2000, 0, 3000]);
    expect(deepOf(heightsOf(bytes))).toBe(2000);
    expect(deepOf(Float32Array.from([0, 5]))).toBe(1);
  });
});

describe("formCodes and the sun", () => {
  it("tells water and cliffs by the passport's table, and no code for a missing form", () => {
    const codes = formCodes({
      rows: 1,
      cols: 1,
      step_m: 500,
      relief_m: 3000,
      biomes: [],
      forms: ["sea", "lake", "plain", "cliff", "canyon"],
    });
    expect(codes.water).toEqual([0, 1, NO_BIOME]);
    expect(codes.cliff).toEqual([3, NO_BIOME, 4, NO_BIOME]);
  });
  it("lights from the north-west, forty-five degrees up", () => {
    const [east, north, up] = sunDirection();
    expect(east).toBeLessThan(0);
    expect(north).toBeGreaterThan(0);
    expect(up).toBeCloseTo(Math.SQRT1_2, 6);
    expect(Math.hypot(east, north, up)).toBeCloseTo(1, 6);
  });
});
