// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** The ground of the globe (D-319, wave 4): land, water, tone and the night. */

import { describe, expect, it } from "vitest";

import type { Terrain, Tile } from "../api";
import { project, radiusUnits } from "../panels/map/globe";
import {
  above,
  cellPaths,
  cut,
  heightAt,
  kindAt,
  localAt,
  tileKey,
  tilesAbout,
  nightPath,
  subsolar,
  toneOf,
} from "../panels/map/relief";

const R = radiusUnits(6371);
const EPOCH = "2026-01-01T00:00:00Z";
const HOURS = 38;
const MS = 3_600_000;

/** A small world: 4 rows by 8 columns, the northern half land, a massif
 *  two cells square (a peak of one cell is below the tree line at its own
 *  corners, where the height is read between the cells), a lake. */
const world: Terrain = {
  rows: 4,
  cols: 8,
  sea_level: 0.5,
  mountain_level: 0.9,
  grid: [
    [0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
    [0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2, 0.2],
    [0.6, 0.6, 0.95, 0.95, 0.6, 0.6, 0.6, 0.6],
    [0.7, 0.7, 0.95, 0.95, 0.7, 0.7, 0.7, 0.7],
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
  tile: { deg: 45, n: 2 },
  peak_level: 0.5,
  basin_level: -0.5,
  wet: true,
};

/** A tile of the north-western land: flat at the grid's height, with a
 *  basin at its south-west corner and a peak in its middle. */
const tile: Tile = {
  row: 3,
  col: 1,
  lat0: 45,
  lon0: -135,
  step: 22.5,
  n: 2,
  local: [
    [-0.9, 0, 0],
    [0, 0.9, 0],
    [0, 0, 0],
  ],
};

/** How many cells a set of ground paths holds. */
const cells = (p: { land: Record<string, string>; high: string }) =>
  [p.land.cold, p.land.cool, p.land.warm, p.high].join("").split("M").length - 1;

/** Every point of a set of ground paths. */
const pointsOf = (p: { land: Record<string, string>; high: string }) =>
  [p.land.cold, p.land.cool, p.land.warm, p.high]
    .join("")
    .split(/[MLZ]/)
    .filter(Boolean)
    .map((pair) => pair.split(",").map(Number));

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

  it("is cut fine enough that a near frame sees a curve, not a chord", () => {
    //: The path is cut once in map units and the camera then scales it: a
    //: chord that is a fine line on the whole disk is a ruler-straight edge
    //: across a frame a hundred times nearer, and the night came over the
    //: near frames as a dark rectangle with a corner in the middle of the
    //: map (owner's world, 2026-09-11). The sagitta is the measure.
    const eye = { lat: 0, lon: 0 };
    const points = nightPath(eye, R, { lat: 0, lon: 91 })!
      .slice(1, -1)
      .split("L")
      .map((pair) => pair.split(",").map(Number));
    //: The greatest gap between neighbouring points, as a share of the
    //: radius: the sagitta is about an eighth of its square.
    let widest = 0;
    for (let i = 1; i < points.length; i++) {
      const [ax, ay] = points[i - 1];
      const [bx, by] = points[i];
      widest = Math.max(widest, Math.hypot(bx - ax, by - ay) / R);
    }
    const sagitta = (widest * widest) / 8;
    //: Under a thousandth of the planet's radius -- below a pixel on the
    //: nearest frame the map draws.
    expect(sagitta).toBeLessThan(1e-3);
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

  it("draws land, mountains and lakes over the sea, the lake over a hole in the land", () => {
    const eye = { lat: 45, lon: -60 };
    const paths = cellPaths(world, eye, R, { cold: 0, cool: 15 });
    const cells = (d: string) => (d.match(/M/g) ?? []).length;
    //: The sea rows draw nothing; the land rows draw what faces the eye.
    expect(cells(paths.land.cold)).toBe(0);
    expect(cells(paths.land.warm)).toBeGreaterThan(0);
    expect(cells(paths.land.cool)).toBeGreaterThan(0);
    //: The massif: the cell between its four centres whole, and a piece of
    //: each of the eight cells round it, cut at the tree line.
    expect(cells(paths.high)).toBe(9);
    //: The lake is water of its own over the land: the land round it is
    //: cut by the sea's level, and the lake drawn over the hole.
    const unflooded = cellPaths({ ...world, lakes: [] }, eye, R, { cold: 0, cool: 15 });
    expect(paths.land.cool).not.toBe(unflooded.land.cool);
    expect(paths.land.cool.length).toBeGreaterThan(unflooded.land.cool.length);
    expect((paths.water.match(/M/g) ?? []).length).toBeGreaterThan(0);
    expect(unflooded.water).toBe("");
    //: The far side is not drawn: from over the equator the cells on the
    //: other side of the sphere have no corner facing the eye.
    const half = cellPaths(world, { lat: 0, lon: -60 }, R, { cold: 0, cool: 15 });
    const above = cellPaths(world, { lat: 85, lon: -60 }, R, { cold: 0, cool: 15 });
    expect(cells(half.land.warm)).toBeLessThan(cells(above.land.warm));
  });

  it("reads the grid every so many cells on the approach, fewer cells drawn", () => {
    const eye = { lat: 45, lon: -60 };
    const fine = cellPaths(world, eye, R, { cold: 0, cool: 15 });
    const coarse = cellPaths(world, eye, R, { cold: 0, cool: 15 }, 2);
    expect(cells(coarse)).toBeLessThan(cells(fine));
    expect(cells(coarse)).toBeGreaterThan(0);
  });

  it("keeps a lone cell of land: a diamond about its centre, not nothing", () => {
    //: One island in the cold sea row, seen from right over it.
    const grid = world.grid.map((row) => row.slice());
    grid[0][4] = 0.6;
    const isle = cellPaths({ ...world, grid }, { lat: -67.5, lon: 22.5 }, R, { cold: 0, cool: 15 });
    //: Four cells share the island's centre as a corner, each with a
    //: triangle of it -- over what the far land rows show at the limb.
    const none = cellPaths(world, { lat: -67.5, lon: 22.5 }, R, { cold: 0, cool: 15 });
    expect(cells(isle) - cells(none)).toBe(4);
  });

  it("lays only the frame's part of the sphere when told how wide the frame is", () => {
    const eye = { lat: 45, lon: -60 };
    const whole = cellPaths(world, eye, R, { cold: 0, cool: 15 }, 0.25);
    const framed = cellPaths(world, eye, R, { cold: 0, cool: 15 }, 0.25, R / 8);
    expect(cells(framed)).toBeGreaterThan(0);
    expect(cells(framed)).toBeLessThan(cells(whole) / 4);
    //: Every point laid lies within the frame or one cell past it -- a
    //: cell of this small world is a quarter of 45 degrees across.
    const cell = R * ((45 * 0.25 * Math.PI) / 180) * Math.SQRT2;
    const far = Math.max(...pointsOf(framed).map(([x, y]) => Math.max(Math.abs(x), Math.abs(y))));
    expect(far).toBeLessThan(R / 8 + cell);
  });

  it("draws the land up to the horizon, its far corners pushed to the limb", () => {
    //: A world of land alone: seen from over the pole, the disk is land to
    //: its very edge -- some corner of a cell cut by the horizon reaches R.
    const dry: Terrain = { ...world, grid: world.grid.map((row) => row.map(() => 0.7)), lakes: [] };
    const paths = cellPaths(dry, { lat: 85, lon: 0 }, R, { cold: 0, cool: 15 });
    const farthest = Math.max(...pointsOf(paths).map(([x, y]) => Math.hypot(x, y)));
    expect(farthest).toBeCloseTo(R, 0);
    expect(farthest).toBeLessThanOrEqual(R + 1e-6);
  });

  it("reads the height between the cells, and draws more cells when read finer", () => {
    //: Between a sea cell of 0.2 and a land cell of 0.6 the field is 0.4.
    expect(heightAt(world, 0, -180 + 22.5)).toBeCloseTo(0.4, 6);
    //: On a cell's centre the field is the cell's own.
    expect(heightAt(world, 22.5, -180 + 22.5)).toBeCloseTo(0.6, 6);
    const eye = { lat: 45, lon: -60 };
    const plain = cellPaths(world, eye, R, { cold: 0, cool: 15 });
    const fine = cellPaths(world, eye, R, { cold: 0, cool: 15 }, 0.5);
    expect(cells(fine)).toBeGreaterThan(cells(plain) * 2);
  });

  it("reads the local relief off its tile: a basin is a hole, a peak a mountain", () => {
    const eye = { lat: 67.5, lon: -112.5 };
    const tiles = new Map([[tileKey(3, 1), tile]]);
    //: Between the lattice points the tile reads bilinearly.
    expect(localAt(world, tiles, 67.5, -112.5)).toBeCloseTo(0.9, 9);
    expect(localAt(world, tiles, 56.25, -112.5)).toBeCloseTo(0.45, 9);
    expect(localAt(world, tiles, 10, 10)).toBe(null);
    //: Drawn at a sixteenth of a cell: without the tile the land is whole
    //: and there is no mountain; with it the peak is high ground and the
    //: basin a hole in the land.
    const plain = cellPaths(world, eye, R, { cold: 0, cool: 15 }, 1 / 16, R / 2);
    const fine = cellPaths(world, eye, R, { cold: 0, cool: 15 }, 1 / 16, R / 2, tiles);
    const highs = (d: string) => (d.match(/M/g) ?? []).length;
    expect(highs(fine.high)).toBeGreaterThan(highs(plain.high));
    expect(fine.land.cool).not.toBe(plain.land.cool);
    //: The basin is water of its own over the hole, told from the sea.
    expect(highs(fine.water)).toBeGreaterThan(highs(plain.water));
    //: Under a frame inside one cell the flat colour reads the tile too.
    expect(kindAt(world, { lat: 67.5, lon: -112.5 }, { cold: 0, cool: 15 }, tiles)).toBe("high");
    expect(kindAt(world, { lat: 45.5, lon: -134.5 }, { cold: 0, cool: 15 }, tiles)).toBe("water");
    expect(kindAt(world, { lat: 67.5, lon: -112.5 }, { cold: 0, cool: 15 })).toBe("cool");
    //: The tiles a frame needs: the eye's own under a small frame, none
    //: for a frame that would need the whole planet.
    expect(tilesAbout(world, eye, R, R / 20)).toEqual([[3, 1]]);
    expect(tilesAbout(world, eye, R, R)).toEqual([]);
  });

  it("cuts a coast cell by the height and then by the noise: a basin at the shore is a hole", () => {
    //: Left half sea, right half land; the land's far corner is a basin.
    const quad = [
      { x: 0, y: 0 },
      { x: 1, y: 0 },
      { x: 1, y: 1 },
      { x: 0, y: 1 },
    ];
    const hs = [0.25, 0.75, 0.75, 0.25];
    const ls = [0, 0, -0.9, 0];
    const shore = cut(quad, hs, 0.5, ls);
    expect(shore?.points).toEqual([
      { x: 0.5, y: 0 },
      { x: 1, y: 0 },
      { x: 1, y: 1 },
      { x: 0.5, y: 1 },
    ]);
    //: The noise is carried to the shore's own vertices by the same cut.
    expect(shore?.carry).toEqual([0, 0, -0.9, -0.45]);
    const land = cut(shore!.points, shore!.carry, -0.5);
    //: The basin corner (1, 1) is out of the land; the shore corner stays.
    expect(land?.points.some((p) => p.x === 1 && p.y === 1)).toBe(false);
    expect(land?.points.some((p) => p.x === 1 && p.y === 0)).toBe(true);
  });

  it("cuts a cell along the level, the cut where the heights cross it", () => {
    const quad = [
      { x: 0, y: 0 },
      { x: 1, y: 0 },
      { x: 1, y: 1 },
      { x: 0, y: 1 },
    ];
    //: The top two corners above, the bottom two below: the cut runs
    //: three quarters of the way down the sides.
    expect(above(quad, [1, 1, 0, 0], 0.25)).toEqual([
      { x: 0, y: 0 },
      { x: 1, y: 0 },
      { x: 1, y: 0.75 },
      { x: 0, y: 0.75 },
    ]);
    //: All below: nothing; all above: the cell itself.
    expect(above(quad, [0, 0, 0, 0], 0.25)).toBe(null);
    expect(above(quad, [1, 1, 1, 1], 0.25)).toEqual(quad);
    //: A saddle -- two opposite corners above -- is one loop of six points.
    const saddle = above(quad, [1, 0, 1, 0], 0.5);
    expect(saddle?.length).toBe(6);
    expect(saddle?.[0]).toEqual({ x: 0, y: 0 });
    expect(saddle?.[1]).toEqual({ x: 0.5, y: 0 });
  });

  it("lays a cell where the projection lays its corners", () => {
    const eye = { lat: 45, lon: -60 };
    const paths = cellPaths(world, eye, R, { cold: 0, cool: 15 });
    //: A corner stands on a cell's centre; this one is land, and land
    //: corners are kept as they are by the cut.
    const corner = project(eye, R, { lat: 22.5, lon: -67.5 });
    const laid = paths.land.warm
      .split(/[ML]/)
      .filter(Boolean)
      .map((pair) => pair.replace("Z", "").split(",").map(Number));
    expect(
      laid.some(([x, y]) => Math.abs(x - corner.x) < 1e-3 && Math.abs(y - corner.y) < 1e-3),
    ).toBe(true);
  });
});
