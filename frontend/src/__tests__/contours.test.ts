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
  COAST_FINE,
  closeFrame,
  CONTOUR_LADDER,
  SAMPLE_BUDGET,
  coast,
  contourInterval,
  contours,
  frameLines,
  hachures,
  isolines,
  localSamples,
  provinceEdges,
  provinceFrame,
  riverBands,
  riverWidthM,
  provinceWhole,
  provinceMarks,
  quantisedEye,
  rivers,
  samplesOf,
  wholeWindow,
  windowAbout,
} from "../panels/map/contours";
import type { Rasters } from "../panels/map/rasters";
import type { River, Segment } from "../panels/map/contours";
import type { Lattice } from "../panels/map/healpix";
import type { RasterPassport } from "../api";
import { UNITS_PER_METRE } from "../panels/map/globe";
import { frameMetres } from "../panels/map/contours";

const FORMS = ["sea", "lake", "plain", "hills", "cliff", "coast_cliff", "beach"];
const WATER = ["land", "sea", "lake", "river"];

/** A lattice of latitude and longitude whose points reach the rasters by
 *  row and column, for the tests below.
 *
 *  What the vector layer walks is a mesh of latitude and longitude, and
 *  where a point of it reaches into the rasters is the grid's business
 *  (D-328): on a planet that is HEALPix, here it is a plain grid drawn by
 *  hand. The drawing is what these tests are about -- a ring that closes, a
 *  shore styled by the land it touches, a run of boundary cut where the
 *  provinces change -- and it is the same drawing either way. That the
 *  planet's own lattice reaches the right cell is `healpix.test.ts`. */
function meshOf(rows: number, cols: number): Lattice {
  const at = (lat: number, lon: number) => {
    const r = Math.min(rows - 1, Math.max(0, Math.floor(((lat + 90) * rows) / 180)));
    const c = ((Math.floor(((lon + 180) * cols) / 360) % cols) + cols) % cols;
    return r * cols + c;
  };
  //: On a mesh drawn by hand a point reads its own cell and nothing else:
  //: the mesh *is* the cells, and there is nothing between them. On a
  //: planet the two differ, and that reading is `healpix.test.ts`.
  const between = (raster: ArrayLike<number>, lat: number, lon: number) => raster[at(lat, lon)];
  return {
    rows,
    cols,
    at,
    between,
    row: (raster, lat, lon0, step, many, out, seat) => {
      for (let j = 0; j < many; j++) out[seat + j] = between(raster, lat, lon0 + j * step);
    },
  };
}

/** A small planet: `rows` by `2 * rows` cells, the heights given by a
 *  function, and -- where a test asks for them -- the provinces by another. */
function planet(
  rows: number,
  height: (r: number, c: number) => number,
  form?: (r: number, c: number) => string,
  province?: (r: number, c: number) => number,
) {
  const cols = 2 * rows;
  const rasters: Rasters = {
    height: new Float32Array(rows * cols),
    biome: new Uint8Array(rows * cols),
    form: new Uint8Array(rows * cols),
    water: new Uint8Array(rows * cols),
    rock: new Uint8Array(rows * cols),
    province: new Uint8Array(rows * cols),
    flow: new Uint8Array(rows * cols),
    lake: new Uint8Array(rows * cols),
  };
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      rasters.height[r * cols + c] = height(r, c);
      //: A "river" form in a test planet is a river cell of the water raster on a plain.
      const named = form ? form(r, c) : height(r, c) < 0 ? "sea" : "plain";
      rasters.form[r * cols + c] = FORMS.indexOf(named === "river" ? "plain" : named);
      rasters.water[r * cols + c] = WATER.indexOf(named === "river" ? "river" : named === "sea" ? "sea" : named === "lake" ? "lake" : "land");
      //: A river cell drains something: half a byte of the log scale, so a
      //: test planet's rivers have a width to be drawn at.
      rasters.flow[r * cols + c] = named === "river" ? 128 : 0;
      //: A lake travels as a share, not as a class: the picture cuts its
      //: shore between the cells as it cuts the sea's by the height.
      rasters.lake[r * cols + c] = named === "lake" ? 255 : 0;
      rasters.province[r * cols + c] = province ? province(r, c) : 0;
    }
  }
  const passport: RasterPassport = {
    grid: "healpix", nside: rows, cells: 12 * rows * rows,
    rows, cols, across: 4, down: 3, border: 1,
    step_m: 500, relief_m: 3000, biomes: [], forms: FORMS, water: WATER,
    flow_max_km2: 4000,
  };
  return { rasters, passport, lattice: meshOf(rows, cols) };
}

describe("windowAbout", () => {
  it("reads the whole hemisphere at a stride within the budget from the planet frame", () => {
    const win = windowAbout(meshOf(600, 1200), { lat: 10, lon: 20 }, 1e6, undefined);
    expect(win.r0).toBe(0);
    expect(win.r1).toBe(599);
    expect(win.count).toBe(1200);
    const samples = samplesOf(meshOf(600, 1200), win);
    expect(samples.nr * samples.nc).toBeLessThanOrEqual(SAMPLE_BUDGET * 1.05);
    expect(win.stride).toBeGreaterThan(1);
  });
  it("closes a window round the whole planet on itself: no seam on the first column", () => {
    //: A ring of land round the equator, the sea elsewhere: the coast must
    //: cross every column, the first included.
    const { rasters, lattice } = planet(8, (r) => (r === 3 || r === 4 ? 100 : -100));
    const samples = samplesOf(lattice, wholeWindow(lattice));
    expect(samples.nc).toBe(lattice.cols + 1);
    const { shores } = coast(rasters, samples, FORMS);
    const all = [...shores.rock, ...shores.beach, ...shores.shore];
    //: Two coasts, one north and one south of the ring, each `cols` cells
    //: long -- and every crossed cell is cut into `COAST_FINE` pieces, so
    //: the line follows the same curve inside a cell that the shader's
    //: water does.
    expect(all).toHaveLength(2 * lattice.cols * COAST_FINE);
    const lons = all.map(([a]) => a.lon);
    expect(Math.min(...lons)).toBeLessThan(-160);
    expect(Math.max(...lons)).toBeGreaterThan(160);
  });
  it("reads a close frame whole, wrapping the columns round the planet", () => {
    const radius = 1e6;
    //: A frame of a few cells about a point near the date line.
    const win = windowAbout(meshOf(600, 1200), { lat: 0, lon: 179.9 }, radius, radius * 0.01);
    expect(win.stride).toBe(1);
    expect(win.count).toBeLessThan(1200);
    const samples = samplesOf(meshOf(600, 1200), win);
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
    const { rasters, lattice } = planet(rows, (r, c) => {
      const d = Math.hypot(r - 6, c - 12);
      return d > 5 ? -100 : 1000 * (1 - d / 5);
    });
    const win = windowAbout(lattice, { lat: 0, lon: 0 }, 1e5, undefined);
    const samples = samplesOf(lattice, win);
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
    const { rasters, lattice } = planet(8, (r) => r * 500);
    const win = windowAbout(lattice, { lat: 0, lon: 0 }, 1e5, undefined);
    const rings = contours(rasters, samplesOf(lattice, win), 100);
    const index = rings.filter((r) => r.index).map((r) => r.level);
    expect(index).toEqual([500, 1000, 1500, 2000, 2500, 3000]);
  });
  it("splits a saddle by the mean of its corners", () => {
    const samples = samplesOf(meshOf(2, 4), { r0: 0, r1: 1, c0: 0, count: 2, stride: 1 });
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
    const { rasters, lattice } = planet(
      rows,
      (_r, c) => (c < 6 ? -50 : 100),
      (r, c) => (c < 6 ? "sea" : c === 6 ? (r < 3 ? "coast_cliff" : "beach") : "plain"),
    );
    const win = windowAbout(lattice, { lat: 0, lon: 0 }, 1e5, undefined);
    const { shores, lakes } = coast(rasters, samplesOf(lattice, win), FORMS);
    expect(shores.rock.length).toBeGreaterThan(0);
    expect(shores.beach.length).toBeGreaterThan(0);
    //: The planet closes on itself: the land's last column meets the sea's
    //: first across the date line, plain shore there, one row's worth of
    //: stretches a row (`COAST_FINE` pieces to the cell).
    expect(shores.shore).toHaveLength((rows - 1) * COAST_FINE);
    for (const [a, b] of shores.shore) for (const p of [a, b]) expect(Math.abs(p.lon)).toBeGreaterThan(160);
    expect(lakes).toEqual([]);
    //: Every stretch stands on the zero, cut between the sea column's centre
    //: (-15, at -50 m) and the land's (15, at 100 m): a third of the way.
    for (const [a, b] of [...shores.rock, ...shores.beach]) {
      for (const p of [a, b]) expect(p.lon).toBeCloseTo(-5, 6);
    }
  });
  it("closes a lake's shore round it, cut where its own share passes a half", () => {
    //: Off the `lake` raster and not off the form: a share, so the shore is
    //: cut between the cells, as the shader cuts it (`shade.ts`, u_wet). As
    //: a class it was whole cells, and a lake with the corners of a cell is
    //: not a lake (owner, 2026-09-10).
    const { rasters, lattice } = planet(6, () => 100, (r, c) => (r === 3 && c === 6 ? "lake" : "plain"));
    const win = windowAbout(lattice, { lat: 0, lon: 0 }, 1e5, undefined);
    const { shores, lakes } = coast(rasters, samplesOf(lattice, win), FORMS);
    expect(shores.rock.length + shores.beach.length + shores.shore.length).toBe(0);
    //: A closed ring round the one wet cell, cut as finely inside a cell as
    //: the sea's own edge is (`COAST_FINE`).
    expect(lakes.length).toBeGreaterThanOrEqual(4 * COAST_FINE);
    const middle = { lat: -90 + (3 + 0.5) * (180 / 6), lon: -180 + (6 + 0.5) * (360 / 12) };
    for (const [a, b] of lakes) {
      for (const p of [a, b]) {
        expect(Math.abs(p.lat - middle.lat)).toBeLessThan(180 / 6);
        expect(Math.abs(p.lon - middle.lon)).toBeLessThan(360 / 12);
      }
    }
    //: And every end of every stretch is on the half itself.
    const wet = samplesOf(lattice, win).between(rasters.lake);
    expect(wet.some((v) => v > 200) && wet.some((v) => v < 50)).toBe(true);
  });
});

describe("hachures and rivers", () => {
  it("ticks a cliff cell down its slope", () => {
    //: The land rises to the east: the slope falls west.
    const { rasters, passport, lattice } = planet(6, (_r, c) => c * 100, (r, c) => (r === 3 && c === 6 ? "cliff" : "plain"));
    const win = windowAbout(lattice, { lat: 0, lon: 0 }, 1e5, undefined);
    const step = (Math.PI * (1e5 / UNITS_PER_METRE)) / lattice.rows;
    const ticks = hachures(rasters, samplesOf(lattice, win), passport, step, 1e5);
    expect(ticks).toHaveLength(1);
    const [from, to] = ticks[0];
    expect(to.lon).toBeLessThan(from.lon);
    expect(Math.abs(to.lat - from.lat)).toBeLessThan(1e-9);
  });
  it("joins river cells to their river neighbours once", () => {
    const { rasters, lattice } = planet(6, () => 100, (r, c) => (r === 2 && c >= 4 && c <= 6 ? "river" : r === 3 && c === 7 ? "river" : "plain"));
    const win = windowAbout(lattice, { lat: 0, lon: 0 }, 1e5, undefined);
    const threads = rivers(rasters, samplesOf(lattice, win), WATER);
    //: 4-5, 5-6 along the row, 6 to the south-east 7.
    expect(threads).toHaveLength(3);
  });
});

describe("the ladder and the window's eye", () => {
  it("steps the contour interval down as the frame narrows, none from the planet", () => {
    expect(contourInterval(Infinity)).toBe(Infinity);
    expect(contourInterval(CONTOUR_LADDER[0][0] + 1)).toBe(Infinity);
    //: Terra's bounded frames after both shrinks (D-329): the region
    //: (10.5 km) bare, the next (5 km) at 30 m, then 12 m twice (2.6 and
    //: 1.3 km), the nearest (650 m) at 6 m. The shape of the ladder is what
    //: the test holds -- five frames, four rungs, the widest bare -- and it
    //: survives every shrink because both columns are divided by whatever
    //: the radius is, exactly as the frames themselves are.
    expect(contourInterval(10_500)).toBe(Infinity);
    expect(contourInterval(5_000)).toBe(30);
    expect(contourInterval(2_600)).toBe(12);
    expect(contourInterval(1_300)).toBe(12);
    expect(contourInterval(650)).toBe(6);
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
  it("draws every line for the frame it stands in, and none from a wide one", () => {
    const { rasters, passport, lattice } = planet(6, (_r, c) => c * 100, (r, c) => (r === 3 && c === 6 ? "cliff" : r === 2 && c > 3 ? "river" : "plain"));
    //: A planet small enough that a near frame covers several of its cells:
    //: the mesh of a near frame is a square of ground a cell to the step,
    //: and on a planet whose cells are a hundred kilometres wide it would
    //: sit inside one of them and see no slope at all.
    const radius = 1e5;
    const far = frameLines(rasters, passport, { lat: 0, lon: 0 }, radius, CLOSE_FRAME_M * UNITS_PER_METRE, lattice);
    expect(far.hachures).toEqual([]);
    expect(far.contours).toEqual([]);
    //: The shore and the rivers belong to the near frame with the rest: on
    //: a region they would web the ground over (owner, 2026-09-09).
    expect(far.rivers).toEqual([]);
    expect(far.lakes).toEqual([]);
    const near = frameLines(rasters, passport, { lat: 0, lon: 0 }, radius, (CLOSE_FRAME_M / 4) * UNITS_PER_METRE, lattice);
    expect(near.hachures.length).toBeGreaterThan(0);
    expect(near.contours.length).toBeGreaterThan(0);
    //: The rivers come in bands of width, and every band is a river.
    expect(near.rivers.length).toBeGreaterThan(0);
    for (const band of near.rivers) {
      expect(band.widthM).toBeGreaterThan(0);
      expect(band.segments.length).toBeGreaterThan(0);
    }
  });

  it("reads a near frame a cell of the ground to the step, at every latitude", () => {
    //: The regression this test exists for. The near frame's lines used to
    //: be cut on a box of latitude and longitude, and a box has to reach as
    //: far east as the frame's corner does -- near the pole that is every
    //: meridian there is. The budget then answered by striding over cells,
    //: and the coast came out cut at twice the cell where the shader cuts
    //: water at one: the line left the edge of the colour over most of the
    //: planet, and not one test noticed.
    //
    //: A mesh of ground has no pole in it. The step is the cell, everywhere.
    const lattice = meshOf(600, 1200);
    const radius = 1e6;
    const cellM = 400;
    const reach = 20_000;
    for (const lat of [0, 30, 45, 60, 75, 89]) {
      const { samples, stepM } = localSamples(lattice, { lat, lon: 0 }, radius, reach, cellM);
      expect(stepM).toBeLessThanOrEqual(cellM);
      expect(samples.nr).toBe(samples.nc);
      //: And the mesh really covers the ground it promises: its corner
      //: stands a frame's corner away from the eye, whatever the latitude.
      const corner = samples.geo(0, 0);
      const away =
        (Math.acos(
          Math.min(
            1,
            Math.sin(lat * (Math.PI / 180)) * Math.sin(corner.lat * (Math.PI / 180)) +
              Math.cos(lat * (Math.PI / 180)) *
                Math.cos(corner.lat * (Math.PI / 180)) *
                Math.cos((corner.lon - 0) * (Math.PI / 180)),
          ),
        ) *
          radius) /
        UNITS_PER_METRE;
      expect(away).toBeGreaterThan(reach);
      expect(away).toBeLessThan(reach * 1.6);
    }
  });

  it("keeps a river's width the planet's own, not the window's", () => {
    //: The reaches are cut for the frame now, and the widest reach in a
    //: window is whatever happens to be in it. A river that changed its
    //: band as the eye moved would change its width under the hand, so the
    //: widest is the planet's, off the passport -- and the same reach comes
    //: out at the same width whatever else the window holds.
    const brook: River = { at: [{ lat: 0, lon: 0 }, { lat: 0, lon: 1 }], flow: 20 };
    const great: River = { at: [{ lat: 5, lon: 0 }, { lat: 5, lon: 1 }], flow: 4000 };
    const alone = riverBands([brook], 4000);
    const together = riverBands([brook, great], 4000);
    const widthOf = (bands: { widthM: number; segments: Segment[] }[], lat: number) =>
      bands.find((band) => band.segments.some(([a]) => a.lat === lat))?.widthM;
    expect(widthOf(alone, 0)).toBe(widthOf(together, 0));
    //: And the greatest of the planet is drawn at the width its catchment
    //: says (`riverWidthM`), not at a share of whatever else is in view.
    expect(widthOf(riverBands([great], 4000), 5)).toBeCloseTo(riverWidthM(4000), 6);
  });

});

describe("the provinces", () => {
  //: Two lands side by side with a strip of sea to the west of both: the
  //: boundary is the one seam between them, and nowhere else.
  const twoLands = (rows: number) =>
    planet(
      rows,
      () => 100,
      () => "plain",
      (_r, c) => (c < rows / 2 ? 0 : c < rows ? 1 : 2),
    );

  it("draws a line only where two provinces meet, never against the sea", () => {
    //: A hundred and eighty rows, so a cell is a degree and a run of them
    //: is worth joining: the seam is one line pole to pole, cut only where
    //: it leaves the bin it is filed under.
    const { rasters, lattice } = twoLands(180);
    const edges = provinceEdges(rasters, samplesOf(lattice, wholeWindow(lattice)));
    expect(edges.length).toBe(lattice.rows / BIN_DEG);
    for (const [a, b] of edges) {
      //: Every piece runs down one meridian, and that meridian stands
      //: between the two cells, not on either centre.
      expect(a.lon).toBeCloseTo(b.lon, 6);
      expect(a.lon).toBeCloseTo(-180 + (180 * 360) / lattice.cols, 6);
    }
    //: The edge with the sea (code 0) is the coast's business, and drawing
    //: it here would double the shore.
    expect(edges.some(([end]) => Math.abs(end.lon + 180) < 1e-6)).toBe(false);
  });

  it("cuts a joined run short enough to be drawn as a chord", () => {
    //: A run is drawn as a straight line between its two ends, so a run
    //: that followed a boundary across many degrees would be drawn through
    //: the ground beside it -- and one reaching over the date line would be
    //: drawn round the wrong side of the planet. Runs are cut at the edge
    //: of a five-degree bin, which bounds both.
    const radius = 1e6;
    for (const rows of [45, 180]) {
      const { rasters, passport, lattice } = twoLands(rows);
      //: A near frame, where the window reads every cell: there a run is
      //: bounded in degrees as well as in samples.
      const near = provinceFrame(rasters, passport, { lat: 0, lon: 0 }, radius, radius * 0.02, lattice);
      expect(near.length).toBeGreaterThan(0);
      //: A bin, and the half cell each end stands out by: the boundary is
      //: drawn between the centres, not through them (`MARK_HALF`).
      const down = BIN_DEG + 180 / lattice.rows + 1e-9;
      const along = BIN_DEG + 360 / lattice.cols + 1e-9;
      for (const [a, b] of near) {
        expect(Math.abs(a.lat - b.lat)).toBeLessThanOrEqual(down);
        expect(Math.abs(a.lon - b.lon)).toBeLessThanOrEqual(along);
      }
      //: And on the planet's disk, where the window strides over cells, a
      //: run may be as long as its own two samples -- but never round the
      //: back of the planet.
      const wide = provinceWhole(rasters, passport, lattice).edges;
      expect(wide.length).toBeGreaterThan(0);
      for (const [a, b] of wide) expect(Math.abs(a.lon - b.lon)).toBeLessThan(180);
    }
  });

  it("cuts a run where the provinces on either side of it change", () => {
    //: One land in the west, two in the east: the seam runs pole to pole,
    //: but it parts a different pair north and south, and a line joined
    //: across the equator would be one segment for two boundaries.
    const { rasters, lattice } = planet(
      180,
      () => 100,
      () => "plain",
      (r, c) => (c < 180 ? 1 : r < 90 ? 2 : 3),
    );
    const edges = provinceEdges(rasters, samplesOf(lattice, wholeWindow(lattice)));
    const meridian = edges.filter(([a, b]) => Math.abs(a.lon - b.lon) < 1e-6);
    const parallel = edges.filter(([a, b]) => Math.abs(a.lat - b.lat) < 1e-6);
    //: Two seams -- the middle and the one round the date line -- each in
    //: pieces of a bin, and none of the pieces straddling the change.
    expect(meridian.length).toBe((2 * lattice.rows) / BIN_DEG);
    for (const [a, b] of meridian) {
      expect(Math.min(a.lat, b.lat) >= 0 || Math.max(a.lat, b.lat) <= 0).toBe(true);
    }
    //: And the parallel where the two eastern lands meet, in pieces of a
    //: bin like everything else.
    expect(parallel.length).toBeGreaterThan(0);
    for (const [a, b] of parallel) expect(a.lat).toBeCloseTo(b.lat, 6);
  });

  it("writes each name inside the land it names, weighed by its ground", () => {
    const { rasters, lattice } = twoLands(8);
    const samples = samplesOf(lattice, wholeWindow(lattice));
    const marks = provinceMarks(rasters, samples);
    expect(marks.map((m) => m.code).sort()).toEqual([1, 2]);
    const first = marks.find((m) => m.code === 1)!;
    //: Columns 4..7 of sixteen: the middle of them.
    expect(first.at.lon).toBeCloseTo(-180 + (6 * 360) / lattice.cols, 6);
    //: A band symmetric about the equator is named on it, however the
    //: parallels are weighed.
    expect(first.at.lat).toBeCloseTo(0, 6);
  });

  it("names a land astride the date line on its own side of the planet", () => {
    //: The first and last columns are one province: the mean of the
    //: longitudes is nought, the middle of the wrong ocean; round the
    //: circle it is the date line, where the land actually lies. And the
    //: closing column of a whole-planet window is not counted twice, or
    //: the name would be dragged off the line towards it.
    const { rasters, lattice } = planet(
      4,
      () => 100,
      () => "plain",
      (_r, c) => (c === 0 || c === 7 ? 1 : 0),
    );
    const marks = provinceMarks(rasters, samplesOf(lattice, wholeWindow(lattice)));
    expect(marks.length).toBe(1);
    expect(Math.abs(marks[0].at.lon)).toBeCloseTo(180, 6);
  });

  it("keeps the province boundaries apart from the coast's own walk", () => {
    const { rasters, passport, lattice } = twoLands(8);
    const edges = provinceWhole(rasters, passport, lattice).edges;
    expect(edges.length).toBe(lattice.rows);
    expect(provinceWhole(rasters, passport, lattice).marks.length).toBe(2);
    //: The near frame's lines say nothing of provinces: the boundary is
    //: drawn from the region outward, the shore from the city inward.
    const near = frameLines(rasters, passport, { lat: 0, lon: 0 }, 1e6, 1e3, lattice);
    expect(Object.keys(near).sort()).toEqual([
      "contours",
      "hachures",
      "lakes",
      "rivers",
      "shores",
    ]);
  });
});
