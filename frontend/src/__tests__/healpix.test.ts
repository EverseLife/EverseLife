// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The picture finds its cell where the server finds it (D-328).
 *
 * The equal-area grid lives in three places now -- the vault cuts the field
 * by it, the server reads the field by it, and the picture draws by it --
 * and none of the three can import either of the others. Two of them are
 * held together by a probe the vault writes into the field's passport
 * (`field._check_probe`); this is the third, and the numbers below come out
 * of the server's own `src/healpix.py`.
 *
 * Drift here would not throw: a map drawn a cell off is still a map, and
 * the ground would simply be somewhere else than where one walks.
 */

import { describe, expect, it } from "vitest";

import { ang2pix, atlasBetween, atlasIndex, cellCentre, faceUV, latticeOf, BANDS_PER_NSIDE } from "../panels/map/healpix";
import type { RasterPassport } from "../api";

/** Points and the cells the server puts them in, by fineness. */
const CELLS: Record<number, [number, number, number][]> = {
  1: [
    [-64.1457, 47.3145, 8],
    [0.7245, -112.8628, 7],
    [2.2046, 123.0819, 5],
    [-28.0079, -28.3489, 11],
    [-47.8642, -169.7737, 10],
    [-73.4432, 162.8065, 9],
    [-12.2598, 159.7801, 6],
    [-13.8596, -64.1631, 7],
    [-72.3821, -2.364, 11],
    [-31.5725, -81.8015, 7],
    [35.1846, 93.8772, 5],
    [13.6029, 169.7869, 6],
    [74.9251, 39.1332, 0],
    [46.2384, 40.9299, 0],
    [90.0, 0.0, 0],
    [-90.0, 0.0, 8],
    [0.0, -180.0, 6],
    [0.0, 179.9999, 6],
    [41.8103, 45.0, 0],
    [-41.8103, -45.0, 11],
    [66.4435, 135.0, 1],
  ],
  3: [
    [-64.1457, 47.3145, 72],
    [0.7245, -112.8628, 69],
    [2.2046, 123.0819, 47],
    [-28.0079, -28.3489, 104],
    [-47.8642, -169.7737, 96],
    [-73.4432, 162.8065, 81],
    [-12.2598, 159.7801, 57],
    [-13.8596, -64.1631, 64],
    [-72.3821, -2.364, 100],
    [-31.5725, -81.8015, 63],
    [35.1846, 93.8772, 53],
    [13.6029, 169.7869, 61],
    [74.9251, 39.1332, 8],
    [46.2384, 40.9299, 4],
    [90.0, 0.0, 8],
    [-90.0, 0.0, 72],
    [0.0, -180.0, 58],
    [0.0, 179.9999, 58],
    [41.8103, 45.0, 4],
    [-41.8103, -45.0, 103],
    [66.4435, 135.0, 17],
  ],
  8: [
    [-64.1457, 47.3145, 530],
    [0.7245, -112.8628, 498],
    [2.2046, 123.0819, 335],
    [-28.0079, -28.3489, 734],
    [-47.8642, -169.7737, 688],
    [-73.4432, 162.8065, 578],
    [-12.2598, 159.7801, 416],
    [-13.8596, -64.1631, 452],
    [-72.3821, -2.364, 706],
    [-31.5725, -81.8015, 449],
    [35.1846, 93.8772, 383],
    [13.6029, 169.7869, 436],
    [74.9251, 39.1332, 54],
    [46.2384, 40.9299, 36],
    [90.0, 0.0, 63],
    [-90.0, 0.0, 512],
    [0.0, -180.0, 412],
    [0.0, 179.9999, 419],
    [41.8103, 45.0, 27],
    [-41.8103, -45.0, 740],
    [66.4435, 135.0, 109],
  ],
  13: [
    [-64.1457, 47.3145, 1394],
    [0.7245, -112.8628, 1303],
    [2.2046, 123.0819, 882],
    [-28.0079, -28.3489, 1947],
    [-47.8642, -169.7737, 1821],
    [-73.4432, 162.8065, 1524],
    [-12.2598, 159.7801, 1106],
    [-13.8596, -64.1631, 1190],
    [-72.3821, -2.364, 1863],
    [-31.5725, -81.8015, 1185],
    [35.1846, 93.8772, 1000],
    [13.6029, 169.7869, 1151],
    [74.9251, 39.1332, 153],
    [46.2384, 40.9299, 97],
    [90.0, 0.0, 168],
    [-90.0, 0.0, 1352],
    [0.0, -180.0, 1098],
    [0.0, 179.9999, 1098],
    [41.8103, 45.0, 84],
    [-41.8103, -45.0, 1943],
    [66.4435, 135.0, 295],
  ],
  64: [
    [-64.1457, 47.3145, 33810],
    [0.7245, -112.8628, 31760],
    [2.2046, 123.0819, 21177],
    [-28.0079, -28.3489, 46965],
    [-47.8642, -169.7737, 44102],
    [-73.4432, 162.8065, 37138],
    [-12.2598, 159.7801, 26887],
    [-13.8596, -64.1631, 28838],
    [-72.3821, -2.364, 45079],
    [-31.5725, -81.8015, 28748],
    [35.1846, 93.8772, 24126],
    [13.6029, 169.7869, 27812],
    [74.9251, 39.1332, 3572],
    [46.2384, 40.9299, 2400],
    [90.0, 0.0, 4095],
    [-90.0, 0.0, 32768],
    [0.0, -180.0, 26592],
    [0.0, 179.9999, 26655],
    [41.8103, 45.0, 2015],
    [-41.8103, -45.0, 47136],
    [66.4435, 135.0, 7151],
  ],
};

function passportOf(nside: number, border = 1): RasterPassport {
  const side = nside + 2 * border;
  return {
    grid: "healpix",
    nside,
    cells: 12 * nside * nside,
    rows: 3 * side,
    cols: 4 * side,
    across: 4,
    down: 3,
    border,
    step_m: 400,
    relief_m: 3000,
    height_unit_m: 0.1,
    temperature_c: { min: -64, step: 0.5, cold: -15, hot: 35 },
    biomes: [],
    forms: [],
    water: ["land", "sea", "lake", "river"],
    fluid: "water",
  };
}

describe("the equal-area grid", () => {
  it("puts a point in the same cell the server does", () => {
    for (const [nside, points] of Object.entries(CELLS)) {
      for (const [lat, lon, cell] of points) {
        expect(ang2pix(Number(nside), lat, lon)).toBe(cell);
      }
    }
  });

  it("holds at a fineness that is not a power of two", () => {
    //: The classic formulas are written in bit shifts and take nside a
    //: power of two; ours takes any whole number, and Aurora's picture is
    //: 181 cells a side, which is prime (D-328).
    for (const nside of [3, 13]) {
      const seen = new Set<number>();
      for (let i = 0; i < 240; i++) {
        for (let j = 0; j < 240; j++) {
          const lat = (Math.asin(-1 + (2 * (i + 0.5)) / 240) * 180) / Math.PI;
          const lon = -180 + ((j + 0.5) * 360) / 240;
          const cell = ang2pix(nside, lat, lon);
          expect(cell).toBeGreaterThanOrEqual(0);
          expect(cell).toBeLessThan(12 * nside * nside);
          seen.add(cell);
        }
      }
      //: And no cell of the sphere is unreachable.
      expect(seen.size).toBe(12 * nside * nside);
    }
  });

  it("lays every cell inside its own face of the atlas, borders kept clear", () => {
    const nside = 8;
    const passport = passportOf(nside);
    const side = nside + 2 * passport.border;
    const taken = new Set<number>();
    for (let cell = 0; cell < 12 * nside * nside; cell++) {
      const at = atlasIndex(passport, cell);
      expect(taken.has(at)).toBe(false);
      taken.add(at);
      const row = Math.floor(at / passport.cols);
      const col = at % passport.cols;
      //: Never in the border: that belongs to the face over the edge.
      expect(row % side).toBeGreaterThanOrEqual(passport.border);
      expect(row % side).toBeLessThan(side - passport.border);
      expect(col % side).toBeGreaterThanOrEqual(passport.border);
      expect(col % side).toBeLessThan(side - passport.border);
      //: And on the face it belongs to.
      const face = Math.floor(cell / (nside * nside));
      expect(Math.floor(col / side)).toBe(face % passport.across);
      expect(Math.floor(row / side)).toBe(Math.floor(face / passport.across));
    }
  });

  it("walks a mesh as fine as the cells are", () => {
    const passport = passportOf(64);
    const lattice = latticeOf(passport);
    expect(lattice.rows).toBe(BANDS_PER_NSIDE * 64);
    expect(lattice.cols).toBe(2 * lattice.rows);
    //: A point of the mesh reaches the byte of the cell it stands in.
    expect(lattice.at(0, 0)).toBe(atlasIndex(passport, ang2pix(64, 0, 0)));
    //: Neighbouring points of the mesh are the same cell or the next one,
    //: never a jump: the mesh is no coarser than the grid.
    let jumps = 0;
    let last = lattice.at(-89.9, 0);
    for (let i = 1; i < lattice.rows; i++) {
      const lat = -90 + ((i + 0.5) * 180) / lattice.rows;
      const now = lattice.at(lat, 0);
      if (now !== last) jumps++;
      last = now;
    }
    //: Down a whole meridian the mesh changes cell most of the way: it is
    //: not so fine that it wastes work, nor so coarse that it skips cells.
    expect(jumps).toBeGreaterThan(lattice.rows / 3);
    expect(jumps).toBeLessThanOrEqual(lattice.rows);
  });
});

describe("the centre of a cell", () => {
  //: The inverse of `ang2pix` for the face's own (ix, iy): what a thing that
  //: belongs to a cell stands on -- a tree of the woods -- so that it stands
  //: still while the eye moves (owner, 2026-09-11).
  it("comes back to the same cell it was asked about", () => {
    for (const [nside, points] of Object.entries(CELLS)) {
      const n = Number(nside);
      for (const [, , cell] of points) {
        const at = cellCentre(n, cell);
        expect(ang2pix(n, at.lat, at.lon)).toBe(cell);
      }
    }
  });

  it("is where the face's own fraction says a whole cell is", () => {
    //: Whole cells sit at halves of the face's (u, v) (`faceUV`): the
    //: centre of a cell is a half in both.
    for (const [nside, points] of Object.entries(CELLS)) {
      const n = Number(nside);
      for (const [, , cell] of points) {
        const at = cellCentre(n, cell);
        const { u, v } = faceUV(n, at.lat, at.lon);
        expect(u - Math.floor(u)).toBeCloseTo(0.5, 6);
        expect(v - Math.floor(v)).toBeCloseTo(0.5, 6);
      }
    }
  });
});

describe("a quantity read the way the shader reads it", () => {
  //: The coast is cut from this reading and the water is painted by the
  //: shader's: they must be one surface, or the line runs beside the
  //: colour (owner, 2026-09-11: the coast line did not match the texture).
  //: A raster where every cell's texel holds the cell's own number, and the
  //: border ring of each face holds its nearest cell of the same face --
  //: enough for a reading inside the face, which is what these pin.
  const nside = 8;
  const passport = passportOf(nside);
  const side = nside + 2 * passport.border;
  const raster = new Float32Array(passport.rows * passport.cols);
  for (let row = 0; row < passport.rows; row++) {
    for (let col = 0; col < passport.cols; col++) {
      const fr = Math.floor(row / side);
      const fc = Math.floor(col / side);
      const face = fr * passport.across + fc;
      const iy = Math.min(nside - 1, Math.max(0, row - fr * side - passport.border));
      const ix = Math.min(nside - 1, Math.max(0, col - fc * side - passport.border));
      raster[row * passport.cols + col] = (face * nside + iy) * nside + ix;
    }
  }

  it("gives a cell its own value at its centre", () => {
    for (const [, , cell] of CELLS[nside]) {
      const at = cellCentre(nside, cell);
      expect(atlasBetween(passport, raster, at.lat, at.lon)).toBeCloseTo(cell, 6);
    }
  });

  it("blends between two cells halfway between their centres", () => {
    //: Two cells side by side on one face: halfway between their centres
    //: the reading is the mean of the two, as the hardware's blend is.
    const a = (4 * nside + 5) * nside + 5;
    const b = a + 1;
    const pa = cellCentre(nside, a);
    const pb = cellCentre(nside, b);
    const mid = { lat: (pa.lat + pb.lat) / 2, lon: (pa.lon + pb.lon) / 2 };
    expect(atlasBetween(passport, raster, mid.lat, mid.lon)).toBeCloseTo((a + b) / 2, 1);
  });
});
