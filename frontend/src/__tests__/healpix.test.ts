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

import { ang2pix, atlasIndex, latticeOf, Rings, BANDS_PER_NSIDE } from "../panels/map/healpix";
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
    biomes: [],
    forms: [],
    water: ["land", "sea", "lake", "river"],
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

  it("lays a ring out by asking the projection, and the rings hold the planet", () => {
    //: A ring is laid out when it is first read, by asking the projection
    //: for the middle of every place along it -- not by walking every cell
    //: of the planet and putting each in its ring. The second is the shorter
    //: arithmetic, but it is four hundred milliseconds of it before a line
    //: can be drawn, and a frame touches a few dozen rings of four thousand.
    //: What holds the two together is this: the rings, taken all at once,
    //: are the planet's cells and each of them exactly once.
    for (const nside of [1, 2, 3, 8, 13]) {
      const rings = new Rings(nside);
      const seen = new Set<number>();
      for (let jr = 1; jr <= rings.count; jr++) {
        for (const cell of rings.ring(jr)) seen.add(cell);
      }
      expect(seen.size).toBe(12 * nside * nside);
      expect(Math.min(...seen)).toBe(0);
      expect(Math.max(...seen)).toBe(12 * nside * nside - 1);
    }
  });

  it("reads between the rings, and a constant field comes back constant", () => {
    //: What the reading is for: a value taken as the cell's own is a field
    //: of steps, and the level line of steps runs along their edges -- on
    //: this grid a chain of straight runs at forty-five degrees, which is
    //: the shape of a cell and not the shape of a shore.
    const nside = 16;
    const rings = new Rings(nside);
    const cells = 12 * nside * nside;
    //: The weights of a reading sum to one, so a flat field stays flat.
    for (let i = 0; i < 40; i++) {
      const lat = -89 + (i * 178) / 40;
      const lon = -179 + (i * 358) / 40;
      expect(rings.between(() => 7, lat, lon)).toBeCloseTo(7, 9);
    }
    //: A field that counts the rings from the north reads as a number that
    //: only grows southward -- and grows **between** the rings, not in
    //: steps at them: that is the whole of the difference.
    const band = new Float64Array(cells);
    for (let jr = 1; jr <= rings.count; jr++) {
      for (const cell of rings.ring(jr)) band[cell] = jr;
    }
    let last = -Infinity;
    const seen = new Set<number>();
    for (let i = 0; i <= 200; i++) {
      const lat = 89 - (i * 178) / 200;
      const read = rings.between((cell) => band[cell], lat, 33);
      expect(read).toBeGreaterThanOrEqual(last - 1e-9);
      last = read;
      seen.add(Math.round(read * 1000));
    }
    //: Two hundred readings down a meridian give two hundred different
    //: numbers, not the sixty-odd rings they fall in.
    expect(seen.size).toBeGreaterThan(150);
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
