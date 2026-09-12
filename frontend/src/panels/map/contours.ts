// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The lines of the relief, cut from the picture's rasters (landscape plan
 * wave 6): the contours, and the boundaries of the provinces. The hachures
 * of the cliffs went 2026-09-12 with the shader's cut of shade on a cliff:
 * the owner read the pair as a dark smear with black lines, and a wall
 * reads by its own cast shadow now; the trees went the same day into
 * `figures.ts`, one figure a biome. The rivers are the shader's (the
 * stream raster), and the coast and the lakes' rims are not cut at all
 * since 2026-09-11 -- the water's own edge is the shore (owner: what is
 * the coastline for).
 *
 * Everything is cut **for the frame it is drawn on** and nothing for the
 * planet. The near frames are cut on a mesh of ground about the eye, a cell
 * of the grid to the step (`localSamples`); the planet's own disk, which has
 * no bounded frame, is read whole and coarse (`provinceWhole`). Cutting for
 * the planet is what it was: one walk over three million cells kept
 * in bins, three seconds of it before the first line appeared, for a shore
 * that is drawn from eleven kilometres in.
 *
 * A quantity of the rasters is read **between** the cells and never as the
 * cell's own value (`Samples.between`). A field of steps has its level line
 * along the edges of the steps, and on this grid that is a chain of straight
 * runs at forty-five degrees -- the shape of a cell, not of a shore. It is
 * also the surface the shader draws by, so the line and the colour agree.
 */

import type { RasterPassport } from "../../api";
import { latticeOf, type Lattice } from "./healpix";
import { UNITS_PER_METRE, type Eye, type Geo } from "./globe";
import type { Rasters } from "./rasters";
import { FIGURE_ACROSS, FIGURE_SPACING_M, figures, type Figure, type Growth } from "./figures";

const RAD = Math.PI / 180;

/** How many samples a window may hold: marching squares over more would
 *  not keep up with a drag. A wider window is read at a stride. */
export const SAMPLE_BUDGET = 40_000;
/** A window reaches this far past the frame, as a share of the frame's
 *  angle, so that a small drag does not re-read it. */
export const WINDOW_MARGIN = 0.25;
/** The contour interval by the frame's width in metres, coarsest first:
 *  a frame wider than the first width shows no contours -- the planet
 *  frame is the coast's and the biomes', the region frame reads its ridges
 *  and canyons by the shade (plan §9.7) -- and each narrower frame steps
 *  down to the next interval. The bounded frames of the map are the
 *  ground's units (`bands.groundReach`), which are the planet's radius
 *  times a halving: on Terra about 21, 10, 5.2, 2.6 and 1.3 km across, so
 *  the ladder starts under the region's 21 km.
 *
 *  Both columns were divided by four when the planets were shrunk
 *  sixteenfold by area (2026-09-10), and again by two when they were shrunk
 *  fourfold more (D-329) -- each time by whatever the radius was divided by,
 *  and each time for two different reasons.
 *  The **widths** because the frames themselves are the radius: at the old
 *  ladder every bounded frame would have fallen under 45 km and the region
 *  frame would have gained lines it was never meant to have. The
 *  **intervals** because the relief was divided by the same four
 *  (`terrain.relief_m` 3000 to 750) to keep the slopes: a frame narrower
 *  by that much crosses that much less height, and an interval left alone
 *  would have left three lines where there were eight.
 *
 *  Measured on the running client at the first ladder (2026-09-10): 11 lines
 *  on an 8.1 km frame, 10 on 3.65 km, 9 on 1.63 km, 5 from 0.93 km in, and
 *  none at all past the first rung. The second ladder is the same numbers
 *  halved, so the same frames -- half as wide now -- draw the same count.
 *
 *  Picture, not balance: D-065 leaves the sizes and colours of the window
 *  out of the registry, and the plan's §9.2 lets what the shader and the
 *  lines draw be merely beautiful, judging nothing. */
export const CONTOUR_LADDER: readonly (readonly [frameM: number, intervalM: number])[] = [
  [5_625, Infinity],
  [3_750, 30],
  [875, 12],
  [0, 6],
];
/** Every so many contours one is drawn heavier, as on a topographic sheet. */
export const INDEX_EVERY = 5;
/** Below this frame width the vector lines are drawn at all -- contours,
 *  the trees: the city frame
 *  and nearer (plan §9.7), where the window is read cell by cell. Wider
 *  than this every one of them is a thread of whole cells laid over a
 *  region: a web rather than a line (owner, 2026-09-09), and the far
 *  frames are the shaded ground's alone -- the coast is seen there as the
 *  edge of the water's colour, which the shader cuts by the same zero.
 *
 *  It is the first rung of `CONTOUR_LADDER` and moved with it: the two
 *  answer one question -- whether this frame is near enough to be read as
 *  ground -- and a gap between them would be a frame with a shore and no
 *  contours. */
export const CLOSE_FRAME_M = CONTOUR_LADDER[0][0];

/** Whether a frame of this width in metres is near enough to draw lines on. */
export function closeFrame(frameM: number): boolean {
  return frameM <= CLOSE_FRAME_M;
}
/** The frames the figures are drawn on: this wide and nearer, metres. A
 *  figure is a little under a node's circle (`figures.FIGURE_OF_NODE`), a
 *  couple of metres of ground, and at four hundred metres a frame that is
 *  a few pixels -- a texture, not a tree; wider than this it was a dust
 *  (owner, 2026-09-12: the figures are to come in closer). */
export const TREE_FRAME_M = 400;
/** How long a joined run of a province's boundary may grow, degrees.
 *
 *  A run is drawn as one straight line between its two ends, so a run that
 *  followed a boundary across many degrees would be drawn through the
 *  ground beside it, and one reaching over the date line would be drawn
 *  round the back of the planet. `provinceEdges` cuts a run where its cells
 *  would leave a square of this side, which bounds both. It was the side of
 *  the bins the planet's lines were once kept in; the bins are gone with
 *  the planet-wide cut, and the bound is not. */
export const BIN_DEG = 5;

/** The cells a frame looks at, read every `stride`-th: rows `r0..r1`,
 *  columns `c0` on for `count`, wrapping round the planet. */
export type Window = {
  r0: number;
  r1: number;
  c0: number;
  count: number;
  stride: number;
};

/** The contour interval for a frame's width in metres; none from the
 *  planet frame, which has no width. */
export function contourInterval(frameM: number): number {
  if (!Number.isFinite(frameM)) return Infinity;
  for (const [width, interval] of CONTOUR_LADDER) if (frameM > width) return interval;
  return CONTOUR_LADDER[CONTOUR_LADDER.length - 1][1];
}

/** The angle a frame subtends about the eye, degrees, with the window's
 *  margin: a quarter of the sphere for the planet frame. */
function frameAngle(radius: number, within: number | undefined): number {
  const reach = within === undefined ? radius : within * Math.SQRT2;
  return (reach >= radius ? 90 : Math.asin(reach / radius) / RAD) * (1 + WINDOW_MARGIN);
}

/** The rows and columns under an angle about the eye: every column when a
 *  pole is in or the angle spans the planet. */
function spanOf(
  eye: Eye,
  ang: number,
): { latLo: number; latHi: number; lonLo: number; lonHi: number; whole: boolean } {
  const latLo = Math.max(-90, eye.lat - ang);
  const latHi = Math.min(90, eye.lat + ang);
  const nearestPole = Math.max(Math.abs(latLo), Math.abs(latHi));
  const spread = ang / Math.max(1e-9, Math.cos(nearestPole * RAD));
  const whole = latLo <= -90 || latHi >= 90 || spread >= 180;
  return { latLo, latHi, lonLo: eye.lon - spread, lonHi: eye.lon + spread, whole };
}

/**
 * The window a frame about the eye needs: the rows under the arc the
 * frame's corner subtends, every column when a pole is in, and a stride
 * that keeps the samples within the budget. `within` is half the frame's
 * width in map units; undefined means the whole hemisphere.
 */
export function windowAbout(
  lattice: Lattice,
  eye: Eye,
  radius: number,
  within: number | undefined,
): Window {
  const { rows, cols } = lattice;
  const span = spanOf(eye, frameAngle(radius, within));
  const r0 = Math.min(rows - 1, Math.max(0, Math.floor(((span.latLo + 90) * rows) / 180)));
  const r1 = Math.min(rows - 1, Math.max(0, Math.ceil(((span.latHi + 90) * rows) / 180)));
  let c0 = 0;
  let count = cols;
  if (!span.whole) {
    c0 = Math.floor(((span.lonLo + 180) * cols) / 360);
    count = Math.min(cols, Math.ceil(((span.lonHi + 180) * cols) / 360) - c0 + 1);
  }
  const cells = (r1 - r0 + 1) * count;
  const stride = Math.max(1, Math.ceil(Math.sqrt(cells / SAMPLE_BUDGET)));
  return { r0, r1, c0: ((c0 % cols) + cols) % cols, count, stride };
}

/** The whole planet, cell by cell. */
export function wholeWindow(lattice: Lattice): Window {
  return { r0: 0, r1: lattice.rows - 1, c0: 0, count: lattice.cols, stride: 1 };
}

/**
 * The samples of a near frame: a square of **ground** about the eye, a cell
 * of the grid to the step.
 *
 * Not a box of latitude and longitude, which is what the wider frames use.
 * A box has to reach as far east as the frame's corner does, and near the
 * pole that is every meridian there is: at sixty-five degrees a frame of
 * forty kilometres wanted the whole polar cap, and the budget answered by
 * striding over cells -- the coast came out cut at eight hundred metres
 * where the shader cuts water at four hundred, and the line left the edge
 * of the colour. Away from the equator that was most of the planet.
 *
 * A mesh laid on the ground has no pole in it. Its rows are not lines of
 * latitude, so a reading cannot be settled by the row; it is settled per
 * sample, which costs a few milliseconds on the widest near frame and
 * nothing on the ones a walk is made at.
 *
 * The chart is gnomonic -- a point is the eye's own plane pushed out onto
 * the ball -- so the mesh stretches by a twentieth at the corner of the
 * widest frame it is used for. That moves no ground: the same point places
 * a sample and finds its cell.
 */
export function localSamples(
  lattice: Lattice,
  eye: Eye,
  radius: number,
  reachM: number,
  stepM: number,
): { samples: Samples; stepM: number } {
  const across = Math.max(2, Math.ceil((2 * reachM) / stepM) + 1);
  const side = Math.min(across, Math.floor(Math.sqrt(SAMPLE_BUDGET)));
  const step = (2 * reachM) / (side - 1);
  const middle = (side - 1) / 2;
  const metres = radius / UNITS_PER_METRE;
  //: The eye's own frame: up, and the two ways along the ground from it.
  const lat = eye.lat * RAD;
  const lon = eye.lon * RAD;
  const up = [Math.cos(lat) * Math.cos(lon), Math.cos(lat) * Math.sin(lon), Math.sin(lat)];
  const east = [-Math.sin(lon), Math.cos(lon), 0];
  const north = [
    -Math.sin(lat) * Math.cos(lon),
    -Math.sin(lat) * Math.sin(lon),
    Math.cos(lat),
  ];
  const geo = (i: number, j: number): Geo => {
    const away = ((j - middle) * step) / metres;
    const along = ((middle - i) * step) / metres;
    const x = up[0] + east[0] * away + north[0] * along;
    const y = up[1] + east[1] * away + north[1] * along;
    const z = up[2] + east[2] * away + north[2] * along;
    const size = Math.hypot(x, y, z);
    return { lat: Math.asin(z / size) / RAD, lon: Math.atan2(y, x) / RAD };
  };
  const table = new Int32Array(side * side);
  const places = new Float64Array(side * side * 2);
  for (let i = 0; i < side; i++) {
    for (let j = 0; j < side; j++) {
      const at = geo(i, j);
      places[(i * side + j) * 2] = at.lat;
      places[(i * side + j) * 2 + 1] = at.lon;
      table[i * side + j] = lattice.at(at.lat, at.lon);
    }
  }
  const read = new Map<ArrayLike<number>, Float32Array>();
  return {
    stepM: step,
    samples: {
      nr: side,
      nc: side,
      cell: (i, j) => [i, j],
      geo,
      index: (i, j) => table[i * side + j],
      between: (raster) => {
        let held = read.get(raster);
        if (held) return held;
        held = new Float32Array(side * side);
        for (let k = 0; k < side * side; k++) {
          held[k] = lattice.between(raster, places[k * 2], places[k * 2 + 1]);
        }
        read.set(raster, held);
        return held;
      },
    },
  };
}

/** The whole planet within the budget: every n-th cell, for what is a mean
 *  over the ground rather than a line along it. */
export function coarseWindow(lattice: Lattice): Window {
  const whole = wholeWindow(lattice);
  const stride = Math.max(1, Math.ceil(Math.sqrt((lattice.rows * lattice.cols) / SAMPLE_BUDGET)));
  return { ...whole, stride };
}

/** The samples of a window: a grid `nr` by `nc` of readings, and the place
 *  of a sample by its fractional indices. */
export type Samples = {
  nr: number;
  nc: number;
  /** The lattice's row and column of sample (i, j). */
  cell: (i: number, j: number) => [number, number];
  /** Degrees of sample (i, j), fractional indices allowed. */
  geo: (i: number, j: number) => Geo;
  /** Where sample (i, j) reaches into the rasters: the cell it stands in.
   *  For what the rasters keep as a class. */
  index: (i: number, j: number) => number;
  /** A quantity of the rasters read **between** the cells around every
   *  sample, as one array over the window.
   *
   *  A level line is drawn from this and never from the cell's own value.
   *  The cells are a field of steps, and the level line of a field of steps
   *  runs along the edges of cells -- on the equal-area grid that is a
   *  chain of straight runs at forty-five degrees, which is the shape of a
   *  cell and not the shape of a shore. It is also the surface the shader
   *  draws by, so a line and the colour under it agree.
   *
   *  Kept by raster: a window is walked once for every contour of the
   *  ladder. */
  between: (raster: ArrayLike<number>) => Float32Array;
};

export function samplesOf(lattice: Lattice, win: Window): Samples {
  const { rows, cols } = lattice;
  const { r0, r1, c0, count, stride } = win;
  const nr = Math.floor((r1 - r0) / stride) + 1;
  //: A window round the whole planet closes on itself: one sample more, so
  //: the last quad joins the first column and no seam opens on `c0`.
  const nc = Math.max(1, Math.ceil(count / stride)) + (count >= cols ? 1 : 0);
  const cell = (i: number, j: number): [number, number] => [
    Math.min(rows - 1, r0 + i * stride),
    (((c0 + j * stride) % cols) + cols) % cols,
  ];
  const geo = (i: number, j: number): Geo => ({
    lat: -90 + (r0 + i * stride + 0.5) * (180 / rows),
    lon: ((((c0 + j * stride + 0.5) * (360 / cols)) % 360) + 360) % 360 - 180,
  });
  //: The lattice is latitude and longitude; the rasters are the equal-area
  //: cells of the field (D-328). A sample reaches its bytes by asking the
  //: projection which cell stands under its point -- not by a row and a
  //: column, which the rasters no longer have.
  //
  //: Asked once per sample and kept, not asked where it is read: marching
  //: squares reads the four corners of every quad, so a lazy `index` did the
  //: projection four times over for each sample, and the whole planet's
  //: lines are a million of them. The table is the same size as the window.
  const table = new Int32Array(nr * nc);
  for (let i = 0; i < nr; i++) {
    const lat = -90 + (r0 + i * stride + 0.5) * (180 / rows);
    for (let j = 0; j < nc; j++) {
      const lon = ((((c0 + j * stride + 0.5) * (360 / cols)) % 360) + 360) % 360 - 180;
      table[i * nc + j] = lattice.at(lat, lon);
    }
  }
  const read = new Map<ArrayLike<number>, Float32Array>();
  const between = (raster: ArrayLike<number>): Float32Array => {
    let held = read.get(raster);
    if (held) return held;
    held = new Float32Array(nr * nc);
    const step = (stride * 360) / cols;
    const lon0 = ((c0 + 0.5) * 360) / cols - 180;
    for (let i = 0; i < nr; i++) {
      const lat = -90 + (r0 + i * stride + 0.5) * (180 / rows);
      lattice.row(raster, lat, lon0, step, nc, held, i * nc);
    }
    read.set(raster, held);
    return held;
  };
  return { nr, nc, cell, geo, index: (i, j) => table[i * nc + j], between };
}

/** A line as two ends, degrees. */
export type Segment = [Geo, Geo];

/** A reach of a river: where it runs and how much land drains through it,
 *  km2. */


/**
 * Marching squares: the segments of the level lines `levels` (ascending)
 * of a value read at every sample, one list per level. The saddle is
 * split by the mean of the four corners.
 *
 * The whole ladder is drawn in **one** walk of the window. Level by level
 * it was one walk each -- the same forty thousand quads read eight or ten
 * times over, and nine of every ten of those readings only to learn that
 * the quad is nowhere near the level. A quad knows its own lowest and
 * highest corner, and that says which levels can possibly cross it:
 * usually none, sometimes one. The map moves under the hand because of
 * this. One chord to the crossed quad: the coast used to cut a quad finer,
 * to follow the shader's bilinear water inside a cell; with the coast gone
 * (2026-09-11) a contour's chord is enough.
 */
export function isolines(
  samples: Samples,
  value: (i: number, j: number) => number,
  levels: readonly number[],
): Segment[][] {
  const out: Segment[][] = levels.map(() => []);
  if (!levels.length) return out;
  const { nr, nc, geo } = samples;
  for (let i = 0; i < nr - 1; i++) {
    for (let j = 0; j < nc - 1; j++) {
      const v00 = value(i, j);
      const v01 = value(i, j + 1);
      const v11 = value(i + 1, j + 1);
      const v10 = value(i + 1, j);
      const lo = Math.min(v00, v01, v11, v10);
      const hi = Math.max(v00, v01, v11, v10);
      //: The levels the quad crosses are those in (lo, hi]: the first over
      //: the lowest corner, up to the last not over the highest -- found by
      //: bisection, so a ladder of forty rungs costs a quad a dozen
      //: comparisons and not forty.
      let a = 0;
      let b = levels.length;
      while (a < b) {
        const m = (a + b) >> 1;
        if (levels[m] > lo) b = m;
        else a = m + 1;
      }
      const first = a;
      b = levels.length;
      while (a < b) {
        const m = (a + b) >> 1;
        if (levels[m] <= hi) a = m + 1;
        else b = m;
      }
      if (first >= a) continue;
      const at = (di: number, dj: number): Geo => geo(i + di, j + dj);
      for (let k = first; k < a; k++) {
        for (const segment of quadLines(v00, v01, v11, v10, levels[k], at)) out[k].push(segment);
      }
    }
  }
  return out;
}

/**
 * The pieces of the level line inside one quad, by the sixteen cases of
 * marching squares. `at(di, dj)` places a point of the quad, both a share
 * of the way across it.
 */
function quadLines(
  v00: number, v01: number, v11: number, v10: number,
  level: number,
  at: (di: number, dj: number) => Geo,
): Segment[] {
  const bits =
    (v00 >= level ? 1 : 0) | (v01 >= level ? 2 : 0) | (v11 >= level ? 4 : 0) | (v10 >= level ? 8 : 0);
  if (bits === 0 || bits === 15) return [];
  const cut = (a: number, b: number) => {
    const span = b - a;
    return span === 0 ? 0.5 : Math.max(0, Math.min(1, (level - a) / span));
  };
  //: The four edge crossings: top, right, bottom, left of the quad.
  const top = () => at(0, cut(v00, v01));
  const right = () => at(cut(v01, v11), 1);
  const bottom = () => at(1, cut(v10, v11));
  const left = () => at(cut(v00, v10), 0);
  switch (bits) {
    case 1:
    case 14:
      return [[left(), top()]];
    case 2:
    case 13:
      return [[top(), right()]];
    case 3:
    case 12:
      return [[left(), right()]];
    case 4:
    case 11:
      return [[right(), bottom()]];
    case 6:
    case 9:
      return [[top(), bottom()]];
    case 7:
    case 8:
      return [[left(), bottom()]];
    default: {
      //: Two high corners on a diagonal (5: top-left and bottom-right).
      //: With the middle high too, the high corners join through it and
      //: the line cuts off the two low corners; with the middle low, the
      //: high corners are cut off each on its own.
      const middle = (v00 + v01 + v11 + v10) / 4 >= level;
      return (bits === 5) === middle
        ? [[top(), right()], [left(), bottom()]]
        : [[left(), top()], [right(), bottom()]];
    }
  }
}

/** The contours of a window: the level lines of the height at `interval`
 *  metres from the first above the sea to the highest sampled, each
 *  marked whether it is an index contour -- the whole ladder in one walk
 *  (`isolines`). */
export function contours(
  rasters: Rasters,
  samples: Samples,
  interval: number,
): { level: number; index: boolean; segments: Segment[] }[] {
  if (!Number.isFinite(interval) || interval <= 0) return [];
  const read = samples.between(rasters.height);
  const { nc } = samples;
  let top = 0;
  for (let k = 0; k < read.length; k++) if (read[k] > top) top = read[k];
  const rungs = Math.max(0, Math.ceil(top / interval) - 1);
  if (!rungs) return [];
  const levels = Array.from({ length: rungs }, (_, rung) => (rung + 1) * interval);
  const held = isolines(samples, (i, j) => read[i * nc + j], levels);
  const out: { level: number; index: boolean; segments: Segment[] }[] = [];
  for (let rung = 0; rung < rungs; rung++) {
    if (!held[rung].length) continue;
    out.push({ level: levels[rung], index: (rung + 1) % INDEX_EVERY === 0, segments: held[rung] });
  }
  return out;
}

/** The frame's width in metres from half its width in map units. */
export function frameMetres(within: number | undefined): number {
  return within === undefined ? Infinity : (2 * within) / UNITS_PER_METRE;
}

/** Where a province's name is written: the mean of its ground, which for a
 *  patch of land is inside it. */
export type ProvinceMark = { code: number; at: Geo };

/** The eye a window is read for: the eye itself, quantised to a share of
 *  the frame's angle, so that a drag shorter than the window's margin does
 *  not read the window again. */
export function quantisedEye(eye: Eye, radius: number, within: number | undefined): Eye {
  const reach = within === undefined ? radius : within * Math.SQRT2;
  const ang = reach >= radius ? 90 : Math.asin(reach / radius) / RAD;
  const grain = Math.max(0.01, (ang * WINDOW_MARGIN) / 2);
  return {
    lat: Math.round(eye.lat / grain) * grain,
    lon: Math.round(eye.lon / grain) * grain,
  };
}

/** Half a cell: the boundary runs between the centres, not through them. */
const MARK_HALF = 0.5;

/**
 * The boundary between provinces: the edge two cells of different code
 * share. Not an isoline -- a province is a name, not a level, and the two
 * neighbours are equals: the line runs between them, along the cells.
 *
 * The sea has no province (code 0), and its edge with the land is the
 * water's own, drawn by the shader in the colour of the water: a boundary
 * drawn over it would double the shore.
 *
 * A run of edges along one meridian or one parallel comes out as a single
 * segment rather than one a cell: a boundary of a province is thousands of
 * cells long, and the path is rebuilt for every turn of the eye. A run ends
 * where the two provinces it parts change -- one segment is one boundary --
 * and where it would leave its own square.
 */
export function provinceEdges(rasters: Rasters, samples: Samples): Segment[] {
  const out: Segment[] = [];
  const { nr, nc } = samples;
  const code = (i: number, j: number) => rasters.province[samples.index(i, j)];
  //: Two cells are on opposite sides of a boundary when both are in a
  //: province and the provinces differ; the pair, in one number, says which
  //: boundary it is, so a run can end where the boundary does.
  const seam = (a: number, b: number) => (a > 0 && b > 0 && a !== b ? a * 256 + b : 0);
  //: Which square of `BIN_DEG` a cell falls in, down and along. A run is
  //: cut where its cells would leave one square, so that a run is short enough to be
  //: drawn as one straight line between its ends: a run that followed a
  //: boundary across many degrees would be drawn through the ground beside
  //: it. A run reaching the date line is cut by the same rule, which is
  //: what keeps it from being drawn round the back of the planet.
  const lanes = 360 / BIN_DEG;
  const band = (i: number) => Math.floor((samples.geo(i, 0).lat + 90) / BIN_DEG);
  const lane = (j: number) =>
    ((Math.floor((samples.geo(0, j).lon + 180) / BIN_DEG) % lanes) + lanes) % lanes;
  //: Down a meridian: the shared edge of a cell and its eastern neighbour,
  //: joined while the run holds. The last row has an eastern neighbour like
  //: any other; a window round the whole planet closes on its first column.
  for (let j = 0; j < nc - 1; j++) {
    let from = -1;
    let held = 0;
    for (let i = 0; i <= nr; i++) {
      const on = i < nr ? seam(code(i, j), code(i, j + 1)) : 0;
      const goes = on !== 0 && on === held && band(i) === band(from);
      if (from >= 0 && !goes) {
        out.push([
          samples.geo(from - MARK_HALF, j + MARK_HALF),
          samples.geo(i - 1 + MARK_HALF, j + MARK_HALF),
        ]);
        from = -1;
      }
      if (on !== 0 && from < 0) from = i;
      held = on;
    }
  }
  //: And along a parallel: the shared edge of a cell and its southern one.
  for (let i = 0; i + 1 < nr; i++) {
    let from = -1;
    let held = 0;
    for (let j = 0; j <= nc - 1; j++) {
      const on = j < nc - 1 ? seam(code(i, j), code(i + 1, j)) : 0;
      const goes = on !== 0 && on === held && lane(j) === lane(from);
      if (from >= 0 && !goes) {
        out.push([
          samples.geo(i + MARK_HALF, from - MARK_HALF),
          samples.geo(i + MARK_HALF, j - 1 + MARK_HALF),
        ]);
        from = -1;
      }
      if (on !== 0 && from < 0) from = j;
      held = on;
    }
  }
  return out;
}

/**
 * Where each province's name is written: the mean of the ground it holds.
 *
 * The ground, not the cells: a cell of a parallel near the pole is a
 * fraction of one at the equator, and counting them alike would drag the
 * name of a province that reaches north away from the land it names. The
 * longitude is averaged round the circle, or a province astride the date
 * line would be named on the other side of the planet.
 */
export function provinceMarks(rasters: Rasters, samples: Samples): ProvinceMark[] {
  const sums = new Map<number, { lat: number; x: number; y: number; weight: number }>();
  //: A window round the whole planet carries one column twice -- the last
  //: closes on the first (`samplesOf`) -- and counting it would weigh the
  //: provinces that touch it down towards the date line.
  const closes = samples.nc > 1 && samples.cell(0, samples.nc - 1)[1] === samples.cell(0, 0)[1];
  const columns = samples.nc - (closes ? 1 : 0);
  for (let i = 0; i < samples.nr; i++) {
    for (let j = 0; j < columns; j++) {
      const code = rasters.province[samples.index(i, j)];
      if (!code) continue;
      const at = samples.geo(i, j);
      const weight = Math.max(0, Math.cos(at.lat * RAD));
      let sum = sums.get(code);
      if (!sum) sums.set(code, (sum = { lat: 0, x: 0, y: 0, weight: 0 }));
      sum.lat += at.lat * weight;
      sum.x += Math.cos(at.lon * RAD) * weight;
      sum.y += Math.sin(at.lon * RAD) * weight;
      sum.weight += weight;
    }
  }
  return [...sums.entries()]
    .filter(([, sum]) => sum.weight > 0)
    .map(([code, sum]) => ({
      code,
      at: { lat: sum.lat / sum.weight, lon: Math.atan2(sum.y, sum.x) / RAD },
    }));
}

/** The provinces of a planet as the planet's own disk draws them: where
 *  each name is written, and the boundaries between them.
 *
 *  One coarse walk for both. The mean of a province does not move a pixel
 *  for the tenth sample of a cell, and a boundary on the disk is a few
 *  pixels long; what matters more is that this walk has a **fixed** phase.
 *  A boundary cut on a mesh that moves with the eye is redrawn a sample to
 *  the side after every step, and on the disk, where a sample is kilometres,
 *  the line crawls under the hand. */
export function provinceWhole(
  rasters: Rasters,
  passport: RasterPassport,
  lattice: Lattice = latticeOf(passport),
): { marks: ProvinceMark[]; edges: Segment[] } {
  const samples = samplesOf(lattice, coarseWindow(lattice));
  return { marks: provinceMarks(rasters, samples), edges: provinceEdges(rasters, samples) };
}

/** The lines of a frame: everything the near frame draws, cut for the
 *  window the frame stands in.
 *
 *  Cut for the frame and not for the planet. It was the planet once -- one
 *  walk over every cell of it, kept in bins, and the frame picked the bins
 *  it could see -- and that walk was **three seconds** before the first
 *  line appeared, all of it on the loop, for a shore that is drawn from
 *  eleven kilometres in and nearer. A frame's window is forty thousand
 *  samples against a planet's million and a half; cutting it again when the
 *  eye leaves the window is milliseconds, and there is nothing to wait for
 *  at the start. What it costs is that the same shore is cut afresh when
 *  the eye comes back to it, which is the trade the contours already made.
 */
export type FrameLines = {
  contours: { level: number; index: boolean; segments: Segment[] }[];
  /** The figures of the growth standing on the cells, by figure (`figures`). */
  figures: Partial<Record<Figure, Segment[]>>;
};

/** No lines at all: built afresh each time, because it is handed out of an
 *  exported function and a shared mutable record is a trap. */
function nothing(): FrameLines {
  return {
    contours: [],
    figures: {},
  };
}

export function frameLines(
  rasters: Rasters,
  passport: RasterPassport,
  eye: Eye,
  radius: number,
  within: number | undefined,
  lattice: Lattice = latticeOf(passport),
  wanted: { contours: boolean; figures: boolean; growth: Growth | null; frameM?: number } = {
    contours: true,
    figures: true,
    growth: null,
  },
): FrameLines {
  const frame = frameMetres(within);
  //: The ladder has a rung for every frame a line is drawn on: the interval
  //: is finite exactly where `closeFrame` is true, so one question answers
  //: both and there is no frame with contours and no shore.
  const interval = contourInterval(frame);
  if (!closeFrame(frame)) return nothing();
  //: Every line of a near frame is cut on one mesh of ground about the eye,
  //: reaching to the frame's corner with the window's own margin.
  const reach = (frame / 2) * Math.SQRT2 * (1 + WINDOW_MARGIN);
  const { samples } = localSamples(lattice, eye, radius, reach, passport.step_m);
  //: What the map has switched off is not cut at all (D-331): a hidden
  //: line that was still walked and projected on every eye was a cost for
  //: nothing.
  return {
    contours: wanted.contours ? contours(rasters, samples, interval) : [],
    //: The figures are cut to the frame itself, not to the window
    //: (`figureLines`): with the frame's width told, here; without it,
    //: none -- the window is a kilometre across at its finest, and the
    //: figures of a kilometre are thousands of marks of three pixels.
    figures:
      wanted.figures && wanted.growth && wanted.frameM !== undefined
        ? figureLines(rasters, passport, eye, radius, wanted.frameM, lattice, wanted.growth)
        : {},
  };
}

/**
 * The figures of the growth, cut to the **frame** rather than to the
 * window the contours are cut on: a figure is a couple of metres of ground
 * (`figures.FIGURE_OF_NODE`) and is drawn from TREE_FRAME_M in, where the
 * window stands at its finest -- a kilometre across -- whatever the frame;
 * cut to the window, a frame of a hundred metres paid for the figures of a
 * kilometre on every step of the eye. Its own mesh about the eye, reaching
 * to the frame's corner with the window's margin, and its lattice opens
 * with the frame so that the widest frame holds FIGURE_ACROSS figures
 * across and not a mat of them.
 */
export function figureLines(
  rasters: Rasters,
  passport: RasterPassport,
  eye: Eye,
  radius: number,
  frameM: number,
  lattice: Lattice = latticeOf(passport),
  growth: Growth,
): Partial<Record<Figure, Segment[]>> {
  if (!(frameM <= TREE_FRAME_M)) return {};
  const reach = (frameM / 2) * Math.SQRT2 * (1 + WINDOW_MARGIN);
  const { samples } = localSamples(lattice, eye, radius, reach, passport.step_m);
  const spacing = Math.max(FIGURE_SPACING_M, frameM / FIGURE_ACROSS);
  return figures(rasters, samples, passport, growth, radius, spacing);
}

/** The boundaries of the provinces about the eye, cut on a mesh of ground.
 *
 *  Drawn from the region's frame outward. A bounded frame gets its own mesh
 *  at the frame's own step, as the near frames do; the planet's disk has no
 *  bounded frame and is read whole and coarse instead (`provinceWhole`),
 *  which is also the only shape whose phase does not move under the eye. */
export function provinceFrame(
  rasters: Rasters,
  passport: RasterPassport,
  eye: Eye,
  radius: number,
  within: number,
  lattice: Lattice = latticeOf(passport),
): Segment[] {
  const reach = (frameMetres(within) / 2) * Math.SQRT2 * (1 + WINDOW_MARGIN);
  const { samples } = localSamples(lattice, eye, radius, reach, passport.step_m);
  return provinceEdges(rasters, samples);
}
