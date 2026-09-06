// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** The ground of the globe (D-319, wave 4): land, water, tone and the night. */

import { describe, expect, it } from "vitest";

import type { Terrain } from "../api";
import { project, radiusUnits } from "../panels/map/globe";
import { cellPaths, nightPath, riverRuns, subsolar, toneOf } from "../panels/map/relief";

const R = radiusUnits(6371);
const EPOCH = "2026-01-01T00:00:00Z";
const HOURS = 38;
const MS = 3_600_000;

/** A small world: 4 rows by 8 columns, the northern half land, a peak, a lake. */
const world: Terrain = {
  rows: 4,
  cols: 8,
  sea_level: 0.5,
  mountain_level: 0.9,
  grid: [
    [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
    [0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2],
    [0.6, 0.6, 0.95, 0.6, 0.6, 0.6, 0.6, 0.6],
    [0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7, 0.7],
  ],
  rivers: [
    [
      [30, 0],
      [20, 5],
      [10, 10],
    ],
  ],
  lakes: [[3, 0]],
  warmth: [-10, 10, 25, 5],
};

describe("the sun", () => {
  it("stands over the meridian at the epoch's noon and moves west with the hours", () => {
    const noon = subsolar(EPOCH, HOURS, new Date(EPOCH).getTime() + (HOURS / 2) * MS);
    expect(noon?.lat).toBe(0);
    expect(noon?.lon).toBeCloseTo(0);
    //: A quarter of a day later noon has reached a quarter turn west.
    const later = subsolar(EPOCH, HOURS, new Date(EPOCH).getTime() + (HOURS * 3) / 4 * MS);
    expect(later?.lon).toBeCloseTo(-90);
  });

  it("is nowhere without an epoch or a day", () => {
    expect(subsolar(null, HOURS, 0)).toBe(null);
    expect(subsolar(EPOCH, 0, 0)).toBe(null);
  });
});

describe("the night", () => {
  it("covers the half of the disk away from the sun", () => {
    //: The eye over the equator at noon's meridian, the sun 90 degrees east:
    //: the terminator is the frame's vertical, the night the western half.
    const eye = { lat: 0, lon: 0 };
    const path = nightPath(eye, R, { lat: 0, lon: 90 });
    expect(path).not.toBe(null);
    const xs = path!
      .slice(1, -1)
      .split("L")
      .map((pair) => Number(pair.split(",")[0]));
    expect(Math.max(...xs)).toBeLessThan(1e-6);
    expect(Math.min(...xs)).toBeCloseTo(-R, 3);
  });

  it("is the whole disk with the sun behind the planet, and nothing with the sun overhead", () => {
    const eye = { lat: 0, lon: 0 };
    expect(nightPath(eye, R, { lat: 0, lon: 180 })).toContain("A");
    expect(nightPath(eye, R, { lat: 0, lon: 0 })).toBe(null);
  });
});

describe("the land", () => {
  it("takes its tone from the row's warmth", () => {
    const bands = { cold: 0, cool: 15 };
    expect(toneOf(-5, bands)).toBe("cold");
    expect(toneOf(5, bands)).toBe("cool");
    expect(toneOf(20, bands)).toBe("warm");
  });

  it("draws land, mountains and lakes over the sea, each as its own path", () => {
    const eye = { lat: 45, lon: -60 };
    const paths = cellPaths(world, eye, R, { cold: 0, cool: 15 });
    const cells = (d: string) => (d.match(/M/g) ?? []).length;
    //: The sea rows draw nothing; the land rows draw what faces the eye.
    expect(cells(paths.land.cold)).toBe(0);
    expect(cells(paths.land.warm)).toBeGreaterThan(0);
    expect(cells(paths.land.cool)).toBeGreaterThan(0);
    expect(cells(paths.high)).toBe(1);
    expect(cells(paths.water)).toBe(1);
    //: The far side is not drawn: fewer cells than the land has.
    expect(cells(paths.land.warm) + cells(paths.high)).toBeLessThan(8);
  });

  it("reads the grid every so many cells on the approach, fewer cells drawn", () => {
    const eye = { lat: 45, lon: -60 };
    const fine = cellPaths(world, eye, R, { cold: 0, cool: 15 });
    const coarse = cellPaths(world, eye, R, { cold: 0, cool: 15 }, 2);
    const cells = (p: { land: Record<string, string>; high: string; water: string }) =>
      [p.land.cold, p.land.cool, p.land.warm, p.high, p.water].join("").split("M").length - 1;
    expect(cells(coarse)).toBeLessThan(cells(fine));
    expect(cells(coarse)).toBeGreaterThan(0);
  });

  it("runs a river as one polyline where it faces the eye and cuts it at the horizon", () => {
    expect(riverRuns(world, { lat: 20, lon: 5 }, R)).toHaveLength(1);
    expect(riverRuns(world, { lat: 20, lon: 5 }, R)[0]).toHaveLength(3);
    expect(riverRuns(world, { lat: -20, lon: -175 }, R)).toHaveLength(0);
  });

  it("lays a cell where the projection lays its corners", () => {
    const eye = { lat: 45, lon: -60 };
    const paths = cellPaths(world, eye, R, { cold: 0, cool: 15 });
    const corner = project(eye, R, { lat: 45, lon: -90 });
    const laid = paths.land.warm
      .split(/[ML]/)
      .filter(Boolean)
      .map((pair) => pair.replace("Z", "").split(",").map(Number));
    expect(
      laid.some(([x, y]) => Math.abs(x - corner.x) < 1e-3 && Math.abs(y - corner.y) < 1e-3),
    ).toBe(true);
  });
});
