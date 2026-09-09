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
  GRAIN_PX,
  GRAIN_FROM_M,
  GRAIN_FULL_M,
  GRAIN_MAX_M,
  GRAIN_MIN_M,
  MISSING,
  NO_BIOME,
  PALETTE_SLOTS,
  deepOf,
  edgeCells,
  formCodes,
  grainMetres,
  grainStrength,
  heightsOf,
  mipChain,
  paletteOf,
  parseColor,
  sunDirection,
} from "../panels/map/shade";
import { CLOSE_FRAME_M } from "../panels/map/contours";

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
  it("shows from the frame the relief's lines show from, and no sooner", () => {
    //: One step of the ladder, not two: a frame that gains a texture is the
    //: frame that gains its lines (plan §9.7).
    expect(GRAIN_FROM_M).toBe(CLOSE_FRAME_M);
    expect(grainStrength(Infinity)).toBe(0);
    expect(grainStrength(GRAIN_FROM_M * 2)).toBe(0);
    expect(grainStrength(GRAIN_FROM_M)).toBe(0);
  });
  it("comes on smoothly and stands whole from the node's frame in", () => {
    const half = (GRAIN_FROM_M + GRAIN_FULL_M) / 2;
    expect(grainStrength(half)).toBeCloseTo(0.5, 6);
    expect(grainStrength(GRAIN_FULL_M)).toBe(1);
    expect(grainStrength(GRAIN_FULL_M / 10)).toBe(1);
    expect(grainStrength(0)).toBe(1);
  });
  it("draws a cell about `GRAIN_PX` wide, by octaves, between boot and patch", () => {
    //: A tenth of a metre to a pixel at the node's frame, three metres at
    //: the city's: the cell follows, and stays a texture rather than
    //: becoming four blobs or a screen of noise.
    for (const perPixel of [0.1, 0.4, 1, 3]) {
      const cell = grainMetres(perPixel);
      expect(cell).toBeGreaterThanOrEqual(GRAIN_MIN_M);
      expect(cell).toBeLessThanOrEqual(GRAIN_MAX_M);
      //: Rounding to an octave moves the width by at most a factor of the
      //: square root of two either way.
      const px = cell / perPixel;
      expect(px).toBeGreaterThan(GRAIN_PX / Math.SQRT2);
      expect(px).toBeLessThan(GRAIN_PX * Math.SQRT2);
      expect(Math.log2(cell) % 1).toBe(0);
    }
    //: Held at both ends: never finer than the stone under a boot, never
    //: coarser than the patch a facet is -- past that it would be a
    //: landform, and the shape of the ground is the hillshade's to tell.
    expect(grainMetres(1e-6)).toBe(GRAIN_MIN_M);
    expect(grainMetres(1e6)).toBe(GRAIN_MAX_M);
    expect(grainMetres(Infinity)).toBe(GRAIN_MAX_M);
    //: And it steps, it does not breathe: a zoom of a few per cent leaves
    //: the texture exactly where it was.
    expect(grainMetres(1)).toBe(grainMetres(1.05));
  });
  it("is written into the shader behind one gate apiece", () => {
    //: The grain and the roughened edge cost nothing on a far frame: the
    //: whole of each hangs off a uniform, and a zero there is a branch not
    //: taken. Both gates are named here because a grain drawn unconditionally
    //: would be paid for on the planet's disk, where it is invisible.
    expect(FRAGMENT).toContain("if (u_grain > 0.0)");
    expect(FRAGMENT).toContain("if (u_edge > 0.0");
  });
});

describe("the roughened edge of the colour", () => {
  //: The field's own cell in metres, and a frame of eight hundred pixels.
  const STEP = 500;
  const PX = 800;

  it("is whole on the frames where a cell of the raster is a line on screen", () => {
    //: Twenty-six kilometres across eight hundred pixels: a cell is fifteen
    //: of them, its edge a visible staircase, and that is what the wander
    //: is for.
    expect(edgeCells(26_000, STEP, PX)).toBe(EDGE_CELLS);
    expect(edgeCells(45_000, STEP, PX)).toBe(EDGE_CELLS);
  });

  it("all but vanishes where a cell is wider than the frame", () => {
    //: The node's frame is two hundred metres and the cell five hundred:
    //: there is no edge on screen to roughen, and a wander of eight tenths
    //: of a cell would read the biome of somewhere else for every pixel at
    //: once -- the whole ground the colour of a neighbour the inspector
    //: does not name.
    expect(edgeCells(200, STEP, PX) * STEP).toBeLessThan(20);
    expect(edgeCells(900, STEP, PX) * STEP).toBeLessThan(50);
  });

  it("goes out again where a cell falls under a pixel", () => {
    //: On the planet's disk the class changes inside a pixel: there is no
    //: staircase left to break, and the reading costs a texture fetch for
    //: nothing.
    expect(edgeCells(STEP * PX, STEP, PX)).toBe(0);
    expect(edgeCells(625_000, STEP, PX)).toBe(0);
    //: And it comes back smoothly rather than at a stroke, so no frame of
    //: the way in turns a straight edge ragged in one step.
    const ramp = [2, 2.5, 3, 4].map((cellPx) => edgeCells((STEP * PX) / cellPx, STEP, PX));
    expect(ramp[0]).toBeGreaterThan(0);
    for (let i = 1; i < ramp.length; i++) expect(ramp[i]).toBeGreaterThanOrEqual(ramp[i - 1]);
    expect(ramp[ramp.length - 1]).toBe(EDGE_CELLS);
  });

  it("is nothing at all without a frame, a raster or a screen", () => {
    expect(edgeCells(0, STEP, PX)).toBe(0);
    expect(edgeCells(4_000, 0, PX)).toBe(0);
    expect(edgeCells(4_000, STEP, 0)).toBe(0);
    expect(edgeCells(NaN, STEP, PX)).toBe(0);
    expect(edgeCells(Infinity, STEP, PX)).toBe(0);
  });
});
