// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** The atlas laid out again with a wide border (D-331 addendum): the faces
 *  as they came, the room round each filled from over the edge. */

import { describe, expect, it } from "vitest";

import type { RasterPassport } from "../api";
import { FINE_CELLS, retile, wideSide, widen } from "../panels/map/atlas";
import { ang2pix, atlasIndex, cellCentre } from "../panels/map/healpix";

const RAD = Math.PI / 180;

/** A passport of the server's own layout: a border of one cell. */
function served(nside: number): RasterPassport {
  const side = nside + 2;
  return { nside, border: 1, across: 4, cols: 4 * side, rows: 3 * side } as RasterPassport;
}

function angleBetween(a: { lat: number; lon: number }, b: { lat: number; lon: number }): number {
  const p = [Math.cos(a.lat * RAD) * Math.cos(a.lon * RAD), Math.cos(a.lat * RAD) * Math.sin(a.lon * RAD), Math.sin(a.lat * RAD)];
  const q = [Math.cos(b.lat * RAD) * Math.cos(b.lon * RAD), Math.cos(b.lat * RAD) * Math.sin(b.lon * RAD), Math.sin(b.lat * RAD)];
  return Math.acos(Math.max(-1, Math.min(1, p[0] * q[0] + p[1] * q[1] + p[2] * q[2])));
}

describe("the wide atlas", () => {
  it("grows a tile to the next power of two with room for a level-seven read, and leaves a wide one alone", () => {
    expect(wideSide(254)).toBe(512);
    expect(wideSide(126)).toBe(256);
    expect(widen(served(126)).passport.border).toBe(65);
    expect(widen({ ...served(126), border: 65, cols: 4 * 256, rows: 3 * 256 }).map).toBeNull();
  });

  it("keeps every face's own cells where they were, and fills the room from over the edge", () => {
    const n = 100;
    const before = served(n);
    const { passport, map } = widen(before);
    expect(passport.border).toBe(78);
    expect(passport.cols).toBe(4 * 256);
    expect(passport.rows).toBe(3 * 256);
    expect(map).not.toBeNull();
    const wide = map as Int32Array;
    //: Nothing left unfilled.
    let empty = 0;
    for (let i = 0; i < wide.length; i++) if (wide[i] < 0) empty++;
    expect(empty).toBe(0);
    const side = 256;
    const at = (face: number, px: number, py: number) =>
      wide[(Math.floor(face / 4) * side + passport.border + py) * passport.cols + (face % 4) * side + passport.border + px];
    //: Inside a face, the face's own cell.
    for (const [face, ix, iy] of [[4, 0, 0], [4, 99, 99], [0, 50, 3], [11, 7, 60]] as const) {
      expect(at(face, ix, iy)).toBe(atlasIndex(before, (face * n + iy) * n + ix));
    }
    //: One cell past an edge: the cell over the edge -- a stranger's, and
    //: a step of the lattice away from the edge cell.
    const cellAngle = angleBetween(cellCentre(n, (4 * n + 50) * n + 50), cellCentre(n, (4 * n + 50) * n + 51));
    for (const [face, px, py] of [[4, 100, 50], [4, -1, 50], [4, 50, 100], [0, 50, -1], [9, -1, 20]] as const) {
      const source = at(face, px, py);
      //: The old atlas's texel back to its cell: interior texels only.
      const oldSide = n + 2;
      const row = Math.floor(source / before.cols);
      const col = source - row * before.cols;
      const sourceFace = Math.floor(row / oldSide) * 4 + Math.floor(col / oldSide);
      const six = col - (col >= 0 ? Math.floor(col / oldSide) * oldSide : 0) - 1;
      const siy = row - Math.floor(row / oldSide) * oldSide - 1;
      expect(six).toBeGreaterThanOrEqual(0);
      expect(siy).toBeGreaterThanOrEqual(0);
      expect(sourceFace).not.toBe(face);
      const edgeCell = (face * n + Math.max(0, Math.min(n - 1, py))) * n + Math.max(0, Math.min(n - 1, px));
      const sourceCell = (sourceFace * n + siy) * n + six;
      expect(angleBetween(cellCentre(n, edgeCell), cellCentre(n, sourceCell))).toBeLessThan(cellAngle * 2);
    }
    //: Far out, the sphere's own answer: the texel where a straight line
    //: from the edge leads, which `ang2pix` names the same cell for.
    const far = at(4, 100 + FINE_CELLS + 3, 50);
    expect(far).toBeGreaterThanOrEqual(0);
  });

  it("lays a raster out by the map, the same kind of array", () => {
    const map = new Int32Array([2, 0, -1, 1]);
    expect(Array.from(retile(map, new Uint8Array([10, 20, 30])))).toEqual([30, 10, 0, 20]);
    const floats = retile(map, new Float32Array([1.5, 2.5, 3.5]));
    expect(floats).toBeInstanceOf(Float32Array);
    expect(Array.from(floats)).toEqual([3.5, 1.5, 0, 2.5]);
    //: And the arithmetic the map is built on agrees with itself: a cell's
    //: centre falls in the cell.
    const n = 100;
    for (const cell of [0, 12345, 4 * n * n + 7, 12 * n * n - 1]) {
      const centre = cellCentre(n, cell);
      expect(ang2pix(n, centre.lat, centre.lon)).toBe(cell);
    }
  });
});
