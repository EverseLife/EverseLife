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
  byteChain,
  PALETTE_SLOTS,
  deepOf,
  edgeStrength,
  FLUID_TONES,
  formCodes,
  grainStrength,
  heightsOf,
  mipChain,
  paletteOf,
  parseColor,
  sunDirection,
  AA_PX,
  BANK_SHARE,
  catmullRom,
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
    const palette = paletteOf(probe, "terra", ["forest", "nowhere"], "water", computed);
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

  it("asks for lava's own tones where the planet's fluid is lava", () => {
    const probe = {
      style: { color: "", setProperty: () => {} },
    } as unknown as HTMLElement;
    const asked: string[] = [];
    const computed = (el: HTMLElement) => {
      asked.push(el.style.color);
      return "rgb(0, 0, 0)";
    };
    paletteOf(probe, "pyroxis", [], "lava", computed);
    //: Its own three, not the sea repainted orange.
    expect(asked).toContain(FLUID_TONES.lava.seaDeep);
    expect(asked).not.toContain(FLUID_TONES.water.seaDeep);
  });

  it("falls back to water for a fluid this build has not heard of", () => {
    const probe = {
      style: { color: "", setProperty: () => {} },
    } as unknown as HTMLElement;
    const asked: string[] = [];
    const computed = (el: HTMLElement) => {
      asked.push(el.style.color);
      return "rgb(0, 0, 0)";
    };
    //: A vault ahead of this build is a blue sea, not a hole in the map: an
    //: unknown word must give a wrong answer rather than no answer at all.
    paletteOf(probe, "terra", [], "quicksilver" as "water", computed);
    expect(asked).toContain(FLUID_TONES.water.seaDeep);
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
    expect(Array.from(heightsOf(bytes, 1))).toEqual([-2000, 0, 3000]);
    expect(deepOf(heightsOf(bytes, 1))).toBe(2000);
    //: The raster counts decimetres (`height_unit_m` of the passport), and
    //: the metres come out of the unit.
    expect(Array.from(heightsOf(bytes, 0.1))).toEqual([-200, 0, 300]);
    expect(deepOf(Float32Array.from([0, 5]))).toBe(1);
  });
});

describe("formCodes and the sun", () => {
  it("tells cliffs by the passport's table, and no code for a missing form", () => {
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
      fluid: "water",
    });
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

  it("cuts the river ribbon at a half, where the vault puts its bank", () => {
    //: The pair the ribbon is made of: the vault writes a share that falls
    //: to a half at the bank (`field/pipeline.ribbon`), and this is the
    //: threshold that reads it. Written from **half** the width against the
    //: same threshold, the knife stood at a quarter and the ribbon came out
    //: twice as narrow as the vault's own numbers said -- on every river but
    //: the largest that painted the channel cells alone, which is the chain
    //: of whole cells with right angles the raster was made to leave behind.
    //: Neither repository can import the other; they meet here and in the
    //: vault's `test_the_river_ribbon_is_half_gone_at_its_bank`.
    expect(BANK_SHARE).toBe(0.5);
    //: Read at the finest level by the hardware's own blend, and nothing
    //: cleverer: the ribbon is written so that the blend is right (the
    //: vault's `ribbon`, the distance to the channel's line on a gentle
    //: ramp with the bank at a half), and a cubic spline tried here
    //: narrowed the diagonal reaches.
    expect(FRAGMENT).toContain("textureLod(u_stream, tuv, 0.0).r > BANK_SHARE");
  });

  it("is written into the shader without a gate on its strength", () => {
    //: The grain and the roughened edge each used to hang off an if on
    //: their uniform, and with both on the frames stalled for a second
    //: apiece on the near frames -- a pathology of the driver's, measured
    //: 2026-09-11 (ANGLE over D3D11), gone with the gates. A strength of
    //: nought is a multiply by nought now.
    expect(FRAGMENT).not.toContain("if (u_grain > 0.0)");
    expect(FRAGMENT).not.toContain("if (u_edge > 0.0");
    //: Both strengths still reach the shader, as factors.
    expect(FRAGMENT).toContain("* u_grain *");
    expect(FRAGMENT).toContain("* u_edge)");
    //: And the pixel is the mean of four taps a few pixels apart on the
    //: glass -- of the colour and of the wetness alike: picked once at its
    //: own point, a cell of a few pixels was a diamond (owner, 2026-09-11).
    expect(AA_PX).toBeGreaterThan(1);
    expect(FRAGMENT).toContain("const float AA_PX = 3.0;");
  });
});

describe("the cubic reading of the height", () => {
  it("passes through the samples and sums to one", () => {
    //: A cubic that interpolates: at a texel centre the texel alone
    //: counts, and every weight set sums to one, so a flat field reads
    //: flat. Symmetric about the middle, so the curve does not lean.
    expect(catmullRom(0)).toEqual([0, 1, 0, 0]);
    expect(catmullRom(1).map((w) => Math.round(w * 1e9) / 1e9)).toEqual([0, 0, 1, 0]);
    for (const t of [0.1, 0.25, 0.5, 0.9]) {
      const w = catmullRom(t);
      expect(w.reduce((a, b) => a + b, 0)).toBeCloseTo(1, 12);
      const back = catmullRom(1 - t);
      expect(w[0]).toBeCloseTo(back[3], 12);
      expect(w[1]).toBeCloseTo(back[2], 12);
    }
    //: The outer weights are negative: this is Catmull-Rom, which passes
    //: through the samples, and not a B-spline, which shrinks a river.
    const [w0, , , w3] = catmullRom(0.5);
    expect(w0).toBeLessThan(0);
    expect(w3).toBeLessThan(0);
    //: A lone cell of land among sea reads land at its own centre and sea
    //: a cell away -- a hill, not a diamond: half-way to the neighbour the
    //: reading is the mean of the two (nought) with a quarter of a lift
    //: from the curve, where a bilinear read would be the mean alone.
    const line = [-2, -2, -2, 2, -2, -2, -2];
    const at = (x: number) => {
      const i = Math.floor(x);
      const w = catmullRom(x - i);
      return w[0] * line[i - 1] + w[1] * line[i] + w[2] * line[i + 1] + w[3] * line[i + 2];
    };
    expect(at(3)).toBe(2);
    expect(at(3.5)).toBeCloseTo(0.25, 12);
    expect(at(4)).toBe(-2);
  });
  it("cuts the water on the cubic reading at the near frames", () => {
    //: The shader's weights are written from the same table `catmullRom`
    //: reads, so the test above holds both; here only that the table got
    //: there, and that the cubic is read behind the fragment's own level
    //: and not on the far frames, whose weight for it is nought.
    expect(FRAGMENT).toContain("vec4 cubicWeights(float t)");
    expect(FRAGMENT).toContain("0.0 + -0.5 * t + 1.0 * t2 + -0.5 * t3");
    expect(FRAGMENT).toContain("if (s < 1.0) th = mix(heightCubic(tuv), th, s);");
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
    //: A cell or two: a good part of a cell left the diamonds of the raster
    //: showing through at the mid frames (owner, 2026-09-11).
    expect(EDGE_CELLS).toBeGreaterThan(1.5);
    expect(EDGE_CELLS).toBeLessThanOrEqual(3);
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

describe("the mip chain of a byte raster", () => {
  //: Two kinds of byte travel in these rasters and they cannot be coarsened
  //: the same way: a share is averaged, a class is picked. Without a chain
  //: at all a pixel covering ten texels read one of them and shimmered as
  //: the hand moved -- the noise on the far frames (owner, 2026-09-11).
  const level0 = () => Uint8Array.from([0, 255, 0, 255, 0, 255, 0, 255]);

  it("averages a share, because half of a half is a quarter", () => {
    const chain = byteChain(level0(), 4, 2, "mean");
    expect(chain[0].data).toEqual(level0());
    //: Two zeroes and two 255s to a texel: 128 after rounding, not 0 or 255.
    expect(Array.from(chain[1].data)).toEqual([128, 128]);
  });

  it("picks a class, because the mean of two codes is a third code", () => {
    const chain = byteChain(level0(), 4, 2, "pick");
    expect(Array.from(chain[1].data)).toEqual([0, 0]);
    //: And every code that comes out is a code that went in.
    const seen = new Set(chain[1].data);
    for (const code of seen) expect(Array.from(level0())).toContain(code);
  });

  it("stops where a face of the atlas would stop being whole texels", () => {
    //: Past that a texel is a mixture of faces from opposite sides of the
    //: planet -- the same bound the height's chain keeps.
    const wide = new Uint8Array(16 * 8);
    expect(byteChain(wide, 16, 8, "mean", 4).length).toBe(3);
    expect(byteChain(wide, 16, 8, "mean").length).toBeGreaterThan(3);
  });
});
