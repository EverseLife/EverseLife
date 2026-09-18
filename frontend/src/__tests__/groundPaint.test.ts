// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * How often and how finely the GPU ground paints (2026-09-18, the phones):
 * the pace it learns of the GPU (`map/sharpness`), the camera's word on
 * which frames it shows from inside a frame of its own (`map/camera`), and
 * the weather the fragment reads only where it is drawn (`map/cloudsGlsl`).
 */

import { describe, expect, it } from "vitest";

import type { RasterPassport } from "../api";
import { retile, widen } from "../panels/map/atlas";
import { createCamera } from "../panels/map/camera";
import { FRAGMENT } from "../panels/map/fragment";
import { GROUND_RASTERS, buffersOf, groundOf, prepare } from "../panels/map/groundPrep";
import type { Rasters } from "../panels/map/rasters";
import { LAYERS, byteChain, deepOf, mipChain, topChain } from "../panels/map/shade";
import { WEATHER_GLSL } from "../panels/map/weatherGlsl";
import {
  JUDGE_MS,
  LATE_IN_A_ROW,
  PATIENCE_MAX,
  PATIENCE_MIN,
  SETTLE_MS,
  SHARP_DPR_MAX,
  SHARP_START,
  SHARP_STEPS,
  judged,
  ratioOf,
  type Sharpness,
} from "../panels/map/sharpness";

/** The pace after a run of verdicts. */
function after(pace: Sharpness, verdicts: boolean[]): Sharpness {
  return verdicts.reduce(judged, pace);
}

const late = (n: number) => Array<boolean>(n).fill(false);
const onTime = (n: number) => Array<boolean>(n).fill(true);

describe("how finely the ground is drawn", () => {
  it("never finer than two pixels a CSS pixel, and the screen's own below that", () => {
    //: A phone at three: the ground at two, the svg over it at three.
    expect(ratioOf(3, SHARP_START, false)).toBe(SHARP_DPR_MAX);
    expect(ratioOf(1.5, SHARP_START, false)).toBe(1.5);
    expect(ratioOf(1, SHARP_START, false)).toBe(1);
    //: A browser that says nothing sensible is taken at one.
    expect(ratioOf(0, SHARP_START, false)).toBe(1);
  });

  it("draws the still ground whole, whatever it has learned in motion", () => {
    const slow = after(SHARP_START, late(LATE_IN_A_ROW * 3));
    expect(slow.step).toBe(3);
    expect(ratioOf(2, slow, true)).toBeCloseTo(2 * SHARP_STEPS[3], 9);
    expect(ratioOf(2, slow, false)).toBe(2);
  });

  it("steps from the finest down, two steps a half of the pixels", () => {
    expect(SHARP_STEPS[0]).toBe(1);
    for (let i = 1; i < SHARP_STEPS.length; i++) {
      expect(SHARP_STEPS[i]).toBeLessThan(SHARP_STEPS[i - 1]);
    }
    for (let i = 2; i < SHARP_STEPS.length; i++) {
      const pixels = (SHARP_STEPS[i] / SHARP_STEPS[i - 2]) ** 2;
      expect(pixels).toBeGreaterThan(0.45);
      expect(pixels).toBeLessThan(0.55);
    }
  });

  it("does not step down for one late paint alone", () => {
    //: The first paint after a still one waits for the still one to end on
    //: the GPU: that is a hitch, not the pace of the device.
    expect(after(SHARP_START, late(LATE_IN_A_ROW - 1)).step).toBe(0);
    expect(after(SHARP_START, [false, true, false, true]).step).toBe(0);
    expect(after(SHARP_START, late(LATE_IN_A_ROW)).step).toBe(1);
  });

  it("stops at the coarsest step and at the finest", () => {
    const bottom = after(SHARP_START, late(LATE_IN_A_ROW * (SHARP_STEPS.length + 4)));
    expect(bottom.step).toBe(SHARP_STEPS.length - 1);
    //: A GPU that always keeps up never leaves the top step.
    const top = after(SHARP_START, onTime(PATIENCE_MAX * 2));
    expect(top.step).toBe(0);
  });

  it("tries a finer step after a run on time", () => {
    const down = after(SHARP_START, late(LATE_IN_A_ROW * 2));
    expect(down.step).toBe(2);
    expect(after(down, onTime(PATIENCE_MIN - 1)).step).toBe(2);
    const tried = after(down, onTime(PATIENCE_MIN));
    expect(tried.step).toBe(1);
    expect(tried.trying).toBe(true);
  });

  it("waits twice as long after a try that failed, up to a limit", () => {
    let pace = after(SHARP_START, late(LATE_IN_A_ROW));
    expect(pace.patience).toBe(PATIENCE_MIN);
    //: Up, and straight back down: the edge of what the GPU can do.
    pace = after(pace, onTime(PATIENCE_MIN));
    expect(pace.step).toBe(0);
    pace = after(pace, late(LATE_IN_A_ROW));
    expect(pace.step).toBe(1);
    expect(pace.patience).toBe(PATIENCE_MIN * 2);
    //: The next try comes only after the longer run.
    expect(after(pace, onTime(PATIENCE_MIN * 2 - 1)).step).toBe(1);
    expect(after(pace, onTime(PATIENCE_MIN * 2)).step).toBe(0);
    //: And the patience has a ceiling: a device is not written off for good.
    for (let i = 0; i < 20; i++) {
      pace = after(pace, onTime(pace.patience));
      pace = after(pace, late(LATE_IN_A_ROW));
    }
    expect(pace.patience).toBe(PATIENCE_MAX);
  });

  it("does not double the patience for a step down that was no try", () => {
    //: Slowness met on the way down from the finest is news, not a failed try.
    const pace = after(SHARP_START, late(LATE_IN_A_ROW * 3));
    expect(pace.patience).toBe(PATIENCE_MIN);
  });

  it("takes a tried step that has held for the device's own", () => {
    let pace = after(SHARP_START, late(LATE_IN_A_ROW));
    pace = after(pace, onTime(PATIENCE_MIN));
    expect(pace).toMatchObject({ step: 0, trying: true });
    //: Held as long as a try is given, it is no longer a try: a pair of
    //: hitches minutes later steps down without doubling the patience.
    pace = after(pace, onTime(PATIENCE_MIN));
    expect(pace.trying).toBe(false);
    pace = after(pace, late(LATE_IN_A_ROW));
    expect(pace).toMatchObject({ step: 1, patience: PATIENCE_MIN });
  });

  it("judges a paint within a sixty-hertz frame, and settles after the globe's own step", () => {
    //: The verdict comes at the next frame of a sixty-hertz screen and the
    //: second of a hundred-and-twenty-hertz one: the same time on both.
    expect(JUDGE_MS).toBeLessThan(1000 / 60);
    expect(JUDGE_MS).toBeGreaterThan(1000 / 120);
    //: The entry globe paints fifteen times a second (`EntryGlobe`,
    //: SPIN_FPS): the ground turning by itself is in motion, not still
    //: between its steps -- on any screen, since the wait is in time.
    expect(SETTLE_MS).toBeGreaterThan(2 * (1000 / 15));
  });
});

describe("the camera's frames", () => {
  it("says which frames it shows from an animation frame of its own", () => {
    let clock = 0;
    let booked: ((t: number) => void)[] = [];
    const shown: boolean[] = [];
    const cam = createCamera({
      onFrame: (_frame, inFrame) => shown.push(inFrame),
      now: () => clock,
      raf: (step) => {
        booked.push(step);
        return booked.length;
      },
      cancel: () => {
        booked = [];
      },
    });
    //: A hand -- a pan, a zoom, a pinch -- moves the frame between frames:
    //: the ground books its paint for the next one.
    cam.panTo(10, 10);
    cam.zoomOnMiddle(2);
    cam.cut({ x: 0, y: 0 });
    expect(shown).toEqual([false, false, false]);
    //: The chase is shown from inside its own frame: the ground paints at
    //: once and moves with the svg, not a frame behind it.
    cam.aimAt({ x: 600, y: 0 });
    clock += 16;
    const due = booked;
    booked = [];
    for (const step of due) step(clock);
    expect(shown.slice(3)).toEqual([true]);
    //: The field's resize is told inside the frame that resized: shown at
    //: once, or the ground would keep the old shape a frame.
    cam.reshape(540, 400);
    expect(shown.slice(4)).toEqual([true]);
  });

  it("leaves a cut on the way down to the descent's own next step", () => {
    let booked: ((t: number) => void)[] = [];
    let shown = 0;
    const cam = createCamera({
      onFrame: () => shown++,
      now: () => 0,
      raf: (step) => {
        booked.push(step);
        return booked.length;
      },
      cancel: () => {
        booked = [];
      },
    });
    cam.zoomToward(0.25);
    //: Shown by the cut and again by the step in the same frame, the ground
    //: would be painted twice over.
    cam.cut({ x: 10, y: 10 });
    expect(shown).toBe(0);
    expect(booked.length).toBe(1);
    const due = booked;
    booked = [];
    for (const step of due) step(16);
    expect(shown).toBe(1);
  });
});

describe("the weather in the fragment", () => {
  it("is read only where something drawn reads it", () => {
    //: The law walks nine systems a reading, and it was read twice over
    //: every pixel to be multiplied by nought on most of them. The cloud
    //: and the rain belong to the moisture and weather layers; the cloud's
    //: shadow to the far frames' sky by day.
    expect(LAYERS.indexOf("moisture")).toBeGreaterThan(0);
    expect(FRAGMENT).toContain(
      `if (u_layer == ${LAYERS.indexOf("moisture")} || u_layer == ${LAYERS.indexOf("weather")}) {\n    float cover = clamp(wxCover(here)`,
    );
    expect(FRAGMENT).toContain("if (far_sky > 0.0 && high > 0.0 && u_sunlit > 0.0) {");
    //: Two readings of the ground's law and no more: one per gate.
    expect(FRAGMENT.split("wxCover(").length - 1).toBe(3);
  });

  it("gates nothing but arithmetic", () => {
    //: The stalls of 2026-09-11 were gates on uniforms with texture reads
    //: under them; a gate here holds the law alone -- hashes and sums, no
    //: texture, no derivative.
    //: The code alone: the comments may well speak of a cloud's texture.
    const code = (glsl: string) => glsl.replace(/\/\/.*$/gm, "");
    const body = (gate: string) => {
      const from = FRAGMENT.indexOf(gate);
      expect(from).toBeGreaterThan(0);
      return code(FRAGMENT.slice(from, FRAGMENT.indexOf("\n  }\n", from)));
    };
    for (const gate of [`if (u_layer == ${LAYERS.indexOf("moisture")}`, "if (far_sky > 0.0 && high > 0.0"]) {
      const inside = body(gate);
      expect(inside).toContain("wxCover(");
      expect(inside).not.toMatch(/texture|dFd|fwidth/);
    }
    //: And the law the gates call reads nothing but its numbers.
    expect(code(WEATHER_GLSL)).toContain("wxNoise2(");
    expect(code(WEATHER_GLSL)).not.toMatch(/texture|dFd|fwidth/);
    //: The layer's own colour is still a sum of weights: this is the one
    //: gate on the layer in the whole fragment.
    expect(FRAGMENT.split("if (u_layer").length - 1).toBe(1);
  });
});

describe("the rasters made ready for the GPU", () => {
  //: A small planet of the server's own layout: a border of one cell.
  const n = 8;
  const served = { nside: n, border: 1, across: 4, cols: 4 * (n + 2), rows: 3 * (n + 2) } as RasterPassport;
  const size = served.cols * served.rows;
  const bytes = (seed: number) => Uint8Array.from({ length: size }, (_, i) => (i * seed + 7) % 256);
  const rasters: Rasters = {
    height: Float32Array.from({ length: size }, (_, i) => ((i * 37) % 200) - 60),
    biome: bytes(3),
    form: bytes(5),
    water: bytes(7),
    rock: bytes(11),
    province: bytes(13),
    flow: bytes(17),
    lake: bytes(19),
    stream: bytes(23),
    temperature: bytes(29),
    rain: bytes(31),
    river: bytes(37),
  };

  //: Level by level and texel by texel -- a million texels a level, which
  //: a deep equality walks too slowly to be a test.
  type Chain = { data: Float32Array | Uint8Array; cols: number; rows: number }[];
  const same = (a: Chain, b: Chain): boolean =>
    a.length === b.length &&
    a.every((level, i) => {
      const other = b[i];
      if (level.cols !== other.cols || level.rows !== other.rows || level.data.length !== other.data.length) return false;
      if (level.data.constructor !== other.data.constructor) return false;
      for (let k = 0; k < level.data.length; k++) if (level.data[k] !== other.data[k]) return false;
      return true;
    });

  it("is the chains of the laid-out rasters, each by what its bytes mean", () => {
    const ready = prepare(served, groundOf(rasters));
    const wide = widen(served);
    expect(wide.map).not.toBeNull();
    expect(ready.passport).toEqual(wide.passport);
    const { cols, rows, nside, border } = wide.passport;
    const tile = nside + 2 * border;
    const laid = <T extends Float32Array | Uint8Array>(raster: T): T => retile(wide.map!, raster);
    const heights = laid(rasters.height);
    expect(same(ready.height, mipChain(heights, cols, rows, tile))).toBe(true);
    const top = topChain(heights, cols, rows, tile);
    expect(same(ready.top, top)).toBe(true);
    expect(ready.topM).toBe(Math.max(...top[top.length - 1].data));
    expect(ready.deep).toBe(deepOf(heights));
    //: A class is picked, a share is averaged, the ribbon is cut first.
    for (const key of ["biome", "form"] as const) {
      expect(same(ready[key], byteChain(laid(rasters[key]), cols, rows, "pick", tile))).toBe(true);
    }
    for (const key of ["rock", "lake", "temperature", "rain", "river"] as const) {
      expect(same(ready[key], byteChain(laid(rasters[key]), cols, rows, "mean", tile))).toBe(true);
    }
    expect(same(ready.stream, byteChain(laid(rasters.stream), cols, rows, "cut", tile))).toBe(true);
    //: And the three ways differ on these bytes, or the test proves nothing.
    expect(same(ready.stream, byteChain(laid(rasters.stream), cols, rows, "mean", tile))).toBe(false);
    expect(same(ready.biome, byteChain(laid(rasters.biome), cols, rows, "mean", tile))).toBe(false);
  });

  it("hands over every level's buffer once, and nothing else", () => {
    const ready = prepare(served, groundOf(rasters));
    const handed = buffersOf(ready);
    expect(new Set(handed).size).toBe(handed.length);
    const levels = new Set<ArrayBufferLike>();
    for (const key of [...GROUND_RASTERS, "top"] as const) {
      for (const level of ready[key]) levels.add(level.data.buffer);
    }
    expect(new Set(handed)).toEqual(levels);
    //: The height and its top share their finest level: one buffer, once.
    expect(ready.top[0].data.buffer).toBe(ready.height[0].data.buffer);
  });

  it("takes the GPU's nine rasters and the page's own arrays, untouched", () => {
    const nine = groundOf(rasters);
    expect(Object.keys(nine).sort()).toEqual([...GROUND_RASTERS].sort());
    for (const key of GROUND_RASTERS) expect(nine[key]).toBe(rasters[key]);
    prepare(served, nine);
    expect(rasters.height.length).toBe(size);
    expect(rasters.river.length).toBe(size);
  });
});
