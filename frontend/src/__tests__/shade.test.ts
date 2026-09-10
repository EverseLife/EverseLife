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
  FRAGMENT,
  EDGE_CELLS,
  EDGE_FULL_PX,
  EDGE_M,
  EDGE_SEEN_PX,
  GRAIN_FULL_PX,
  GRAIN_M,
  GRAIN_SEEN_PX,
  MISSING,
  NO_BIOME,
  PALETTE_SLOTS,
  deepOf,
  edgeStrength,
  formCodes,
  grainStrength,
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
      //: The atlas of the equal-area grid (D-328): one cell a face, borders
      //: counted -- the smallest passport there is, and the table is what
      //: this test is about.
      grid: "healpix",
      nside: 1,
      cells: 12,
      rows: 9,
      cols: 12,
      across: 4,
      down: 3,
      border: 1,
      step_m: 500,
      relief_m: 3000,
      biomes: [],
      forms: ["sea", "lake", "plain", "cliff", "canyon"],
      water: ["land", "sea", "lake", "river"],
    });
    expect(codes.water).toEqual([0, 1, NO_BIOME]);
    expect(codes.cliff).toEqual([3, NO_BIOME, 4, NO_BIOME]);
    expect(codes.shore).toBe(-1);
    //: The grain's three kinds by the same table, and a form the table
    //: lacks is a code no cell carries -- it mottles as ground does.
    expect(codes.stone).toEqual([NO_BIOME, NO_BIOME, 3, NO_BIOME]);
    expect(codes.sand).toEqual([NO_BIOME, NO_BIOME]);
    expect(codes.ice).toEqual([NO_BIOME, NO_BIOME, NO_BIOME]);
  });
  it("lights from the north-west, forty-five degrees up", () => {
    const [east, north, up] = sunDirection();
    expect(east).toBeLessThan(0);
    expect(north).toBeGreaterThan(0);
    expect(up).toBeCloseTo(Math.SQRT1_2, 6);
    expect(Math.hypot(east, north, up)).toBeCloseTo(1, 6);
  });
});

describe("the grain of the ground", () => {
  it("is the same size on the ground at every zoom", () => {
    //: The one thing the owner asked of it after seeing it (2026-09-09):
    //: the country must not be rearranged by looking closer. The cell is a
    //: length of the ground, a constant, and there is no function of the
    //: frame to ask about it.
    expect(GRAIN_M).toBeGreaterThan(0);
    expect(EDGE_M).toBeGreaterThan(0);
    //: What the zoom may change is only whether it can be seen: nothing
    //: while a cell is under a pixel, whole once it is a few.
    expect(grainStrength(0)).toBe(0);
    expect(grainStrength(GRAIN_SEEN_PX)).toBe(0);
    expect(grainStrength(GRAIN_FULL_PX)).toBe(1);
    expect(grainStrength(100)).toBe(1);
    expect(grainStrength(Infinity)).toBe(0);
  });

  it("comes in smoothly, so no zoom turns it on at a stroke", () => {
    const ramp = [1.5, 2, 2.5, 3, 3.5, 4].map(grainStrength);
    for (let i = 1; i < ramp.length; i++) expect(ramp[i]).toBeGreaterThanOrEqual(ramp[i - 1]);
    expect(ramp[0]).toBe(0);
    expect(ramp[ramp.length - 1]).toBe(1);
    //: Halfway up the ramp it is halfway on, not nearly on or nearly off.
    const middle = (GRAIN_SEEN_PX + GRAIN_FULL_PX) / 2;
    expect(grainStrength(middle)).toBeCloseTo(0.5, 6);
  });

  it("is written into the shader behind one gate apiece", () => {
    //: The grain and the roughened edge cost nothing on a frame that cannot
    //: show them: the whole of each hangs off a uniform, and a zero there
    //: is a branch not taken.
    expect(FRAGMENT).toContain("if (u_grain > 0.0)");
    expect(FRAGMENT).toContain("if (u_edge > 0.0");
  });
});

describe("the roughened edge of the colour", () => {
  it("wanders by a share of a cell, fixed in metres like the grain", () => {
    //: What it hides is the staircase of a raster cell, and it is measured
    //: against that: a good part of a cell, waving over a length of the
    //: ground. Neither number follows the frame -- the edge between two
    //: biomes is one line of the country at every zoom.
    //: Worth about one whole edge of a cell. On the equal-area grid a cell
    //: is a diamond and shows the eye its diagonal -- a run half again as
    //: long as the old square's side, and at forty-five degrees, which
    //: reads as a drawn line rather than a step. Under a cell the teeth
    //: stayed countable; over two the boundary leaves the ground it names.
    expect(EDGE_CELLS).toBeGreaterThan(0.8);
    expect(EDGE_CELLS).toBeLessThan(2);
  });

  it("goes out where its own wave falls under a pixel", () => {
    //: Neighbouring pixels would then read the wander at points too far
    //: apart to be alike, and a rough edge would come out as salt and
    //: pepper. On the planet's disk it is not wanted anyway: the cell it
    //: roughens is itself under a pixel there.
    expect(edgeStrength(0)).toBe(0);
    expect(edgeStrength(EDGE_SEEN_PX)).toBe(0);
    expect(edgeStrength(EDGE_FULL_PX)).toBe(1);
    expect(edgeStrength(1e6)).toBe(1);
    expect(edgeStrength(NaN)).toBe(0);
    const ramp = [2, 3, 4, 5, 6].map(edgeStrength);
    for (let i = 1; i < ramp.length; i++) expect(ramp[i]).toBeGreaterThanOrEqual(ramp[i - 1]);
  });
});
