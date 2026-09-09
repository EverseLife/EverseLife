// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The lines of the relief (landscape plan wave 6), held without a DOM: the
 * window a frame reads and its stride, marching squares closing round a
 * hill, the coast styled by the land it touches, a hachure pointing down
 * the slope, the rivers joined cell to cell, the contour ladder.
 */

import { describe, expect, it } from "vitest";

import {
  BIN_DEG,
  CLOSE_FRAME_M,
  closeFrame,
  CONTOUR_LADDER,
  SAMPLE_BUDGET,
  binned,
  coast,
  contourInterval,
  contours,
  frameLines,
  hachures,
  isolines,
  planetLines,
  quantisedEye,
  rivers,
  samplesOf,
  underFrame,
  wholeWindow,
  windowAbout,
} from "../panels/map/contours";
import type { Rasters } from "../panels/map/rasters";
import { UNITS_PER_METRE } from "../panels/map/globe";
import { frameMetres } from "../panels/map/contours";

const FORMS = ["sea", "lake", "plain", "hills", "cliff", "coast_cliff", "beach"];
const WATER = ["land", "sea", "lake", "river"];

/** A small planet: `rows` by `2 * rows` cells, the heights given by a function. */
function planet(rows: number, height: (r: number, c: number) => number, form?: (r: number, c: number) => string) {
  const cols = 2 * rows;
  const rasters: Rasters = {
    height: new Float32Array(rows * cols),
    biome: new Uint8Array(rows * cols),
    form: new Uint8Array(rows * cols),
    water: new Uint8Array(rows * cols),
  };
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      rasters.height[r * cols + c] = height(r, c);
      //: A "river" form in a test planet is a river cell of the water raster on a plain.
      const named = form ? form(r, c) : height(r, c) < 0 ? "sea" : "plain";
      rasters.form[r * cols + c] = FORMS.indexOf(named === "river" ? "plain" : named);
      rasters.water[r * cols + c] = WATER.indexOf(named === "river" ? "river" : named === "sea" ? "sea" : named === "lake" ? "lake" : "land");
    }
  }
  const passport = { rows, cols, step_m: 500, relief_m: 3000, biomes: [], forms: FORMS, water: WATER };
  return { rasters, passport };
}

describe("windowAbout", () => {
  it("reads the whole hemisphere at a stride within the budget from the planet frame", () => {
    const win = windowAbout({ rows: 600, cols: 1200 }, { lat: 10, lon: 20 }, 1e6, undefined);
    expect(win.r0).toBe(0);
    expect(win.r1).toBe(599);
    expect(win.count).toBe(1200);
    const samples = samplesOf({ rows: 600, cols: 1200 }, win);
    expect(samples.nr * samples.nc).toBeLessThanOrEqual(SAMPLE_BUDGET * 1.05);
    expect(win.stride).toBeGreaterThan(1);
  });
  it("closes a window round the whole planet on itself: no seam on the first column", () => {
    //: A ring of land round the equator, the sea elsewhere: the coast must
    //: cross every column, the first included.
    const { rasters, passport } = planet(8, (r) => (r === 3 || r === 4 ? 100 : -100));
    const samples = samplesOf(passport, wholeWindow(passport));
    expect(samples.nc).toBe(passport.cols + 1);
    const { shores } = coast(rasters, samples, FORMS);
    const all = [...shores.rock, ...shores.beach, ...shores.shore];
    //: Two coasts, one north and one south of the ring, each `cols` segments.
    expect(all).toHaveLength(2 * passport.cols);
    const lons = all.map(([a]) => a.lon);
    expect(Math.min(...lons)).toBeLessThan(-160);
    expect(Math.max(...lons)).toBeGreaterThan(160);
  });
  it("reads a close frame whole, wrapping the columns round the planet", () => {
    const radius = 1e6;
    //: A frame of a few cells about a point near the date line.
    const win = windowAbout({ rows: 600, cols: 1200 }, { lat: 0, lon: 179.9 }, radius, radius * 0.01);
    expect(win.stride).toBe(1);
    expect(win.count).toBeLessThan(1200);
    const samples = samplesOf({ rows: 600, cols: 1200 }, win);
    const lons = [];
    for (let j = 0; j < samples.nc; j++) lons.push(samples.geo(0, j).lon);
    expect(Math.max(...lons)).toBeLessThanOrEqual(180);
    expect(Math.min(...lons)).toBeGreaterThanOrEqual(-180);
    expect(lons.some((l) => l > 170) && lons.some((l) => l < -170)).toBe(true);
  });
});

describe("isolines and contours", () => {
  it("closes a ring round a hill and counts the levels up to the summit", () => {
    const rows = 12;
    const { rasters, passport } = planet(rows, (r, c) => {
      const d = Math.hypot(r - 6, c - 12);
      return d > 5 ? -100 : 1000 * (1 - d / 5);
    });
    const win = windowAbout(passport, { lat: 0, lon: 0 }, 1e5, undefined);
    const samples = samplesOf(passport, win);
    expect(win.stride).toBe(1);
    const rings = contours(rasters, samples, 250);
    expect(rings.map((r) => r.level)).toEqual([250, 500, 750]);
    expect(rings.map((r) => r.index)).toEqual([false, false, false]);
    //: Each segment's ends lie on the level: read back, the height is the level.
    for (const ring of rings) {
      expect(ring.segments.length).toBeGreaterThan(4);
      //: A closed ring: every end is shared with another segment's end.
      const ends = ring.segments.flatMap(([a, b]) => [`${a.lat.toFixed(6)},${a.lon.toFixed(6)}`, `${b.lat.toFixed(6)},${b.lon.toFixed(6)}`]);
      const seen = new Map<string, number>();
      for (const e of ends) seen.set(e, (seen.get(e) ?? 0) + 1);
      expect([...seen.values()].every((n) => n === 2)).toBe(true);
    }
    expect(contours(rasters, samples, 1250)).toEqual([]);
    expect(contours(rasters, samples, Infinity)).toEqual([]);
  });
  it("marks every fifth contour as an index", () => {
    const { rasters, passport } = planet(8, (r) => r * 500);
    const win = windowAbout(passport, { lat: 0, lon: 0 }, 1e5, undefined);
    const rings = contours(rasters, samplesOf(passport, win), 100);
    const index = rings.filter((r) => r.index).map((r) => r.level);
    expect(index).toEqual([500, 1000, 1500, 2000, 2500, 3000]);
  });
  it("splits a saddle by the mean of its corners", () => {
    const samples = samplesOf({ rows: 2, cols: 4 }, { r0: 0, r1: 1, c0: 0, count: 2, stride: 1 });
    //: Top-left and bottom-right high (10), the other two low (0): the
    //: mean is 5, so at level 5 the middle counts high and the high corners
    //: join -- the two cuts go round the low corners, top-right and
    //: bottom-left.
    const high = (i: number, j: number) => ((i + j) % 2 === 0 ? 10 : 0);
    const cuts = isolines(samples, high, 5);
    expect(cuts).toHaveLength(2);
    const top = samples.geo(0, 0.5);
    const right = samples.geo(0.5, 1);
    const bottom = samples.geo(1, 0.5);
    const left = samples.geo(0.5, 0);
    expect(cuts[0]).toEqual([top, right]);
    expect(cuts[1]).toEqual([left, bottom]);
    //: At a level over the mean the middle is low and each high corner is
    //: cut off on its own: top-left by left-top, bottom-right by right-bottom
    //: -- the cuts now four tenths from the high corners.
    const apart = isolines(samples, high, 6);
    expect(apart[0]).toEqual([samples.geo(0.4, 0), samples.geo(0, 0.4)]);
    expect(apart[1]).toEqual([samples.geo(0.6, 1), samples.geo(1, 0.6)]);
  });
});

describe("coast", () => {
  it("styles the shore by the land it touches: rock, beach, plain", () => {
    const rows = 6;
    //: The sea on the west half, land on the east; the land's first column
    //: a sea cliff in the north rows, a beach in the south rows.
    const { rasters, passport } = planet(
      rows,
      (r, c) => (c < 6 ? -50 : 100),
      (r, c) => (c < 6 ? "sea" : c === 6 ? (r < 3 ? "coast_cliff" : "beach") : "plain"),
    );
    const win = windowAbout(passport, { lat: 0, lon: 0 }, 1e5, undefined);
    const { shores, lakes } = coast(rasters, samplesOf(passport, win), FORMS);
    expect(shores.rock.length).toBeGreaterThan(0);
    expect(shores.beach.length).toBeGreaterThan(0);
    //: The planet closes on itself: the land's last column meets the sea's
    //: first across the date line, plain shore there, one stretch a row.
    expect(shores.shore).toHaveLength(rows - 1);
    for (const [a, b] of shores.shore) for (const p of [a, b]) expect(Math.abs(p.lon)).toBeGreaterThan(160);
    expect(lakes).toEqual([]);
    //: Every stretch stands on the zero, cut between the sea column's centre
    //: (-15, at -50 m) and the land's (15, at 100 m): a third of the way.
    for (const [a, b] of [...shores.rock, ...shores.beach]) {
      for (const p of [a, b]) expect(p.lon).toBeCloseTo(-5, 6);
    }
  });
  it("draws a lake's shore off the form raster", () => {
    const { rasters, passport } = planet(6, () => 100, (r, c) => (r === 3 && c === 6 ? "lake" : "plain"));
    const win = windowAbout(passport, { lat: 0, lon: 0 }, 1e5, undefined);
    const { shores, lakes } = coast(rasters, samplesOf(passport, win), FORMS);
    expect(shores.rock.length + shores.beach.length + shores.shore.length).toBe(0);
    expect(lakes.length).toBe(4);
  });
});

describe("hachures and rivers", () => {
  it("ticks a cliff cell down its slope", () => {
    //: The land rises to the east: the slope falls west.
    const { rasters, passport } = planet(6, (r, c) => c * 100, (r, c) => (r === 3 && c === 6 ? "cliff" : "plain"));
    const win = windowAbout(passport, { lat: 0, lon: 0 }, 1e5, undefined);
    const ticks = hachures(rasters, samplesOf(passport, win), passport, win, 1e5);
    expect(ticks).toHaveLength(1);
    const [from, to] = ticks[0];
    expect(to.lon).toBeLessThan(from.lon);
    expect(Math.abs(to.lat - from.lat)).toBeLessThan(1e-9);
  });
  it("joins river cells to their river neighbours once", () => {
    const { rasters, passport } = planet(6, () => 100, (r, c) => (r === 2 && c >= 4 && c <= 6 ? "river" : r === 3 && c === 7 ? "river" : "plain"));
    const win = windowAbout(passport, { lat: 0, lon: 0 }, 1e5, undefined);
    const threads = rivers(rasters, samplesOf(passport, win), WATER);
    //: 4-5, 5-6 along the row, 6 to the south-east 7.
    expect(threads).toHaveLength(3);
  });
});

describe("the ladder and the window's eye", () => {
  it("steps the contour interval down as the frame narrows, none from the planet", () => {
    expect(contourInterval(Infinity)).toBe(Infinity);
    expect(contourInterval(CONTOUR_LADDER[0][0] + 1)).toBe(Infinity);
    //: Terra's bounded frames: the region (83 km) bare, the next (42 km) at
    //: 250 m, then 100 m twice (21 and 10 km), the nearest (5 km) at 50 m.
    expect(contourInterval(83_000)).toBe(Infinity);
    expect(contourInterval(42_000)).toBe(250);
    expect(contourInterval(21_000)).toBe(100);
    expect(contourInterval(10_000)).toBe(100);
    expect(contourInterval(5_000)).toBe(50);
  });
  it("holds the window's eye still under a small drag and moves it under a big one", () => {
    const radius = 1e6;
    const within = radius * 0.1;
    const a = quantisedEye({ lat: 10.0, lon: 20.0 }, radius, within);
    const b = quantisedEye({ lat: 10.05, lon: 20.05 }, radius, within);
    const c = quantisedEye({ lat: 14.0, lon: 26.0 }, radius, within);
    expect(b).toEqual(a);
    expect(c).not.toEqual(a);
  });
  it("draws lines on a near frame alone: the planet's and the region's have none", () => {
    //: The frame the map hands the layer: the planet's has no width.
    expect(closeFrame(frameMetres(undefined))).toBe(false);
    expect(closeFrame(CLOSE_FRAME_M * 2)).toBe(false);
    expect(closeFrame(CLOSE_FRAME_M)).toBe(true);
    expect(closeFrame(CLOSE_FRAME_M / 4)).toBe(true);
  });
  it("draws the hachures only from a close frame, and the planet's lines at any", () => {
    const { rasters, passport } = planet(6, (r, c) => c * 100, (r, c) => (r === 3 && c === 6 ? "cliff" : r === 2 && c > 3 ? "river" : "plain"));
    const far = frameLines(rasters, passport, { lat: 0, lon: 0 }, 1e6, CLOSE_FRAME_M * UNITS_PER_METRE);
    expect(far.hachures).toEqual([]);
    expect(far.contours).toEqual([]);
    const near = frameLines(rasters, passport, { lat: 0, lon: 0 }, 1e6, (CLOSE_FRAME_M / 4) * UNITS_PER_METRE);
    expect(near.hachures.length).toBeGreaterThan(0);
    expect(near.contours.length).toBeGreaterThan(0);
    const own = planetLines(rasters, passport);
    expect([...own.rivers.values()].flat().length).toBeGreaterThan(0);
    //: The whole planet from the planet frame, the bins under a frame otherwise.
    expect(underFrame(own.rivers, { lat: 0, lon: 0 }, 1e6, undefined).length).toBe(
      [...own.rivers.values()].flat().length,
    );
  });
  it("bins the segments by their first end and hands a frame the bins under it", () => {
    const bins = binned([
      [{ lat: 1, lon: 1 }, { lat: 2, lon: 2 }],
      [{ lat: 1 + BIN_DEG, lon: 1 }, { lat: 2, lon: 2 }],
      [{ lat: -80, lon: 179 }, { lat: -80, lon: -179 }],
    ]);
    expect(bins.size).toBe(3);
    //: A frame a few degrees about the equator sees the first two, not the pole's.
    const radius = 1e6;
    const near = underFrame(bins, { lat: 3, lon: 1 }, radius, radius * 0.1);
    expect(near).toHaveLength(2);
    expect(underFrame(bins, { lat: -80, lon: 178 }, radius, radius * 0.05)).toHaveLength(1);
  });
});
