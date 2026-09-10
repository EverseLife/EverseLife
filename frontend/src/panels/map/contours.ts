// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The lines of the relief (landscape plan wave 6, §9.5-9.6): contours,
 * the coast, the hachures of a cliff and the rivers -- geometry read off
 * the field's rasters, drawn by the vector layer over the shaded ground.
 * Lines are vector on purpose: thin and sharp at any zoom (§9.5), and the
 * SVG's business, not the shader's.
 *
 * Lines are a near frame's business (`closeFrame`): wider than the city
 * frame the ground is the shader's alone, and a line drawn from cells of
 * five hundred metres would be a web over the region rather than a line.
 *
 * Two kinds of line, two costs. The coast, the lakes' shores and the rivers
 * do not depend on the frame: they are read once per planet, cell by cell,
 * and kept in bins of a few degrees, so a frame takes the bins under it.
 * The contours and the hachures depend on the frame -- the interval on its
 * width, the cells on its window -- and are read for the window, at a
 * stride that keeps the samples within a budget.
 *
 * Nothing here judges: a coast drawn from the height's zero is a picture of
 * the sea, and whether a point is water stays the server's and the tiles'
 * (§9.1, rule two).
 *
 * Pure arithmetic over typed arrays, so it is tested without a DOM. Every
 * result is in degrees; the component projects them by the eye.
 */

import type { RasterPassport } from "../../api";
import { latticeOf, type Lattice } from "./healpix";
import { UNITS_PER_METRE, type Eye, type Geo } from "./globe";
import type { Rasters } from "./rasters";

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
 *  ground's units (`bands.groundReach`): on Terra about 83, 42, 21, 10 and
 *  5 km across, so the ladder starts under the region's 83 km.
 *
 *  Picture, not balance: D-065 leaves the sizes and colours of the window
 *  out of the registry, and the plan's §9.2 lets what the shader and the
 *  lines draw be merely beautiful, judging nothing. */
export const CONTOUR_LADDER: readonly (readonly [frameM: number, intervalM: number])[] = [
  [45_000, Infinity],
  [30_000, 250],
  [7_000, 100],
  [0, 50],
];
/** Every so many contours one is drawn heavier, as on a topographic sheet. */
export const INDEX_EVERY = 5;
/** Below this frame width the vector lines are drawn at all -- contours,
 *  the coast, the lakes' shores, the hachures, the rivers: the city frame
 *  and nearer (plan §9.7), where the window is read cell by cell. Wider
 *  than this every one of them is a thread of 500-metre cells laid over a
 *  region: a web rather than a line (owner, 2026-09-09), and the far
 *  frames are the shaded ground's alone -- the coast is seen there as the
 *  edge of the water's colour, which the shader cuts by the same zero. */
export const CLOSE_FRAME_M = 45_000;

/** Whether a frame of this width in metres is near enough to draw lines on. */
export function closeFrame(frameM: number): boolean {
  return frameM <= CLOSE_FRAME_M;
}
/** A hachure's length as a share of a cell's side. */
export const HACHURE_SHARE = 0.45;
/** The landforms that get hachures: the cliffs, and the canyon, whose
 *  walls the pipeline classes as canyon rather than cliff (plan §4.3). */
export const HACHURED_FORMS = ["cliff", "coast_cliff", "canyon"] as const;
/** The bins the planet's lines are kept in, degrees a side. */
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
  lattice: { rows: number; cols: number },
  eye: Eye,
  ang: number,
): { latLo: number; latHi: number; lonLo: number; lonHi: number; whole: boolean } {
  const latLo = Math.max(-90, eye.lat - ang);
  const latHi = Math.min(90, eye.lat + ang);
  const nearestPole = Math.max(Math.abs(latLo), Math.abs(latHi));
  const spread = ang / Math.max(1e-9, Math.cos(nearestPole * RAD));
  const whole = latLo <= -90 || latHi >= 90 || spread >= 180;
  void lattice;
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
  const span = spanOf(lattice, eye, frameAngle(radius, within));
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

/** The samples of the whole planet, made once and kept.
 *
 *  Two readers want them -- the coast with its rivers and the boundaries of
 *  the provinces -- and laying out a million samples is most of a second
 *  each time. They are the same million for both. */
const WHOLE = new Map<Lattice, Samples>();
export function wholeSamples(lattice: Lattice): Samples {
  let held = WHOLE.get(lattice);
  if (!held) WHOLE.set(lattice, (held = samplesOf(lattice, wholeWindow(lattice))));
  return held;
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
   *  draws by, so the coast's line and the water's colour agree.
   *
   *  Kept by raster: a window is walked once for the coast and again for
   *  every contour of the ladder. */
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
 *  km2 -- which is how wide it is drawn (`riverWidthM`). */
export type River = { at: Segment; flow: number };

/** A byte of a scaled raster at its full: 255 stands for one. */
const BYTE = 255;

/** How wide a river carrying this much land runs, metres.
 *
 *  `a·A^b` with the exponent at a half: the hydraulic geometry every river
 *  on Earth obeys. On Terra a brook of twenty square kilometres comes out
 *  thirteen metres across and the greatest river, draining four and a half
 *  thousand, two hundred -- which is what a river of that catchment looks
 *  like. Picture, not balance (D-065): what a river is worth to the game is
 *  its crossing and its water, and how wide it is drawn changes no rule --
 *  the same reason the contour ladder's numbers live in code (wave 6). */
export const RIVER_WIDTH_A = 3;
export const RIVER_WIDTH_B = 0.5;
export function riverWidthM(catchmentKm2: number): number {
  return catchmentKm2 > 0 ? RIVER_WIDTH_A * Math.pow(catchmentKm2, RIVER_WIDTH_B) : 0;
}

/**
 * Marching squares: the segments of the level line `level` of a value read
 * at every sample. The saddle is split by the mean of the four corners.
 * `each` is told the quad (i, j) of every segment as it is made, so a
 * caller can style by the quad without walking the quads again.
 */
export function isolines(
  samples: Samples,
  value: (i: number, j: number) => number,
  level: number,
  each?: (i: number, j: number) => void,
  fine = 1,
): Segment[] {
  const out: Segment[] = [];
  const { nr, nc, geo } = samples;
  for (let i = 0; i < nr - 1; i++) {
    for (let j = 0; j < nc - 1; j++) {
      const v00 = value(i, j);
      const v01 = value(i, j + 1);
      const v11 = value(i + 1, j + 1);
      const v10 = value(i + 1, j);
      const lo = Math.min(v00, v01, v11, v10);
      const hi = Math.max(v00, v01, v11, v10);
      if (lo >= level || hi < level) continue;
      //: A quad the line crosses may be read more finely than the raster
      //: is: the four corners are all the shader has of it too, and inside
      //: them it reads the same bilinear surface. One chord across the
      //: whole cell is that surface's rope bridge -- on a cell five hundred
      //: metres wide and a frame three metres to the pixel, the rope hangs
      //: tens of metres away from the ground it stands for, and the coast's
      //: line ran over the water the shader had drawn. Cut only here, where
      //: the line actually is: the rest of the planet costs nothing.
      for (let a = 0; a < fine; a++) {
        for (let b = 0; b < fine; b++) {
          const corner = (di: number, dj: number) =>
            mix(v00, v01, v11, v10, (a + di) / fine, (b + dj) / fine);
          const at = (di: number, dj: number): Geo =>
            geo(i + (a + di) / fine, j + (b + dj) / fine);
          for (const segment of quadLines(
            corner(0, 0), corner(0, 1), corner(1, 1), corner(1, 0), level, at,
          )) {
            out.push(segment);
            each?.(i, j);
          }
        }
      }
    }
  }
  return out;
}

/** The value inside a quad, between its four corners: the very surface the
 *  shader samples when it reads the height texture with linear filtering. */
function mix(
  v00: number, v01: number, v11: number, v10: number, di: number, dj: number,
): number {
  const top = v00 + (v01 - v00) * dj;
  const bottom = v10 + (v11 - v10) * dj;
  return top + (bottom - top) * di;
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
 *  marked whether it is an index contour.
 *
 *  The whole ladder is drawn in **one** walk of the window. Level by level
 *  it was one walk each -- the same forty thousand quads read eight or ten
 *  times over, and nine of every ten of those readings only to learn that
 *  the quad is nowhere near the level. A quad knows its own lowest and
 *  highest corner, and that says which rungs of the ladder can possibly
 *  cross it: usually none, sometimes one. The map moves under the hand
 *  because of this, so it is worth the extra dozen lines. */
export function contours(
  rasters: Rasters,
  samples: Samples,
  interval: number,
): { level: number; index: boolean; segments: Segment[] }[] {
  if (!Number.isFinite(interval) || interval <= 0) return [];
  const read = samples.between(rasters.height);
  const { nr, nc, geo } = samples;
  let top = 0;
  for (let k = 0; k < read.length; k++) if (read[k] > top) top = read[k];
  const rungs = Math.max(0, Math.ceil(top / interval) - 1);
  if (!rungs) return [];
  const held: Segment[][] = Array.from({ length: rungs }, () => []);
  for (let i = 0; i < nr - 1; i++) {
    for (let j = 0; j < nc - 1; j++) {
      const v00 = read[i * nc + j];
      const v01 = read[i * nc + j + 1];
      const v11 = read[(i + 1) * nc + j + 1];
      const v10 = read[(i + 1) * nc + j];
      const lo = Math.min(v00, v01, v11, v10);
      const hi = Math.max(v00, v01, v11, v10);
      //: The rungs this quad can possibly cross, and no others: rung `r`
      //: stands at `(r + 1) * interval`. A rung wide of the mark on either
      //: side, because the exact word belongs to the test just below and a
      //: float must not be trusted to sit on a whole multiple.
      const first = Math.max(0, Math.floor(lo / interval) - 1);
      const last = Math.min(rungs - 1, Math.floor(hi / interval));
      for (let rung = first; rung <= last; rung++) {
        const level = (rung + 1) * interval;
        if (lo >= level || hi < level) continue;
        const at = (di: number, dj: number): Geo => geo(i + di, j + dj);
        for (const segment of quadLines(v00, v01, v11, v10, level, at)) {
          held[rung].push(segment);
        }
      }
    }
  }
  const out: { level: number; index: boolean; segments: Segment[] }[] = [];
  for (let rung = 0; rung < rungs; rung++) {
    if (!held[rung].length) continue;
    const level = (rung + 1) * interval;
    out.push({ level, index: (rung + 1) % INDEX_EVERY === 0, segments: held[rung] });
  }
  return out;
}


/** How a stretch of coast is drawn: a rock wall, a beach, or plain shore. */
export type Shore = "rock" | "beach" | "shore";

/** Into how many pieces a cell the coast crosses is cut. The shader draws
 *  the water by the same zero of the same bilinear surface, and one chord a
 *  cell was visibly not that surface: on a frame of two kilometres the line
 *  ran tens of metres from the colour's edge, sometimes over the water
 *  (owner, 2026-09-09). Four is where the two stop disagreeing by more than
 *  a pixel on the frames that draw them; only the cells the line crosses
 *  are cut, so the walk over the planet costs what it did. */
export const COAST_FINE = 4;

/** The coast: the height's zero, each stretch styled by the land it
 *  touches -- a sea cliff or a cliff is rock, a beach a beach. The lake
 *  shores come separately: the form raster says where a lake is. */
export function coast(
  rasters: Rasters,
  samples: Samples,
  forms: readonly string[],
): { shores: Record<Shore, Segment[]>; lakes: Segment[] } {
  const code = (name: string) => forms.indexOf(name);
  const rock = new Set([code("coast_cliff"), code("cliff")]);
  const beach = code("beach");
  const lake = code("lake");
  const read = samples.between(rasters.height);
  const height = (i: number, j: number) => read[i * samples.nc + j];
  const shores: Record<Shore, Segment[]> = { rock: [], beach: [], shore: [] };
  //: The style of a quad is read off its land corners as its segment is made.
  const styleOf = (i: number, j: number): Shore => {
    let style: Shore = "shore";
    for (const [ci, cj] of [[i, j], [i, j + 1], [i + 1, j + 1], [i + 1, j]] as const) {
      if (height(ci, cj) < 0) continue;
      const form = rasters.form[samples.index(ci, cj)];
      if (rock.has(form)) return "rock";
      if (form === beach) style = "beach";
    }
    return style;
  };
  const styles: Shore[] = [];
  const segments = isolines(samples, height, 0, (i, j) => styles.push(styleOf(i, j)), COAST_FINE);
  segments.forEach((segment, k) => shores[styles[k]].push(segment));
  //: The lake is cut where its own share passes a half, read between the
  //: cells -- the very number and the very threshold the shader cuts it by
  //: (`shade.ts`, u_wet). As a class it was whole cells, and a lake with the
  //: corners of a cell is not a lake.
  const wet = samples.between(rasters.lake);
  const lakes = isolines(samples, (i, j) => wet[i * samples.nc + j], BYTE / 2, undefined, COAST_FINE);
  void lake;
  return { shores, lakes };
}

/**
 * The hachures of the cliffs: from the centre of every cliff cell a tick
 * down the slope, `HACHURE_SHARE` of a cell long -- the classic mark of a
 * wall on a topographic sheet (§9.6). Read at the stride the window is.
 */
export function hachures(
  rasters: Rasters,
  samples: Samples,
  passport: RasterPassport,
  win: Window,
  radius: number,
): Segment[] {
  const cliffs = new Set(
    HACHURED_FORMS.map((name) => passport.forms.indexOf(name)).filter((c) => c >= 0),
  );
  if (!cliffs.size) return [];
  const out: Segment[] = [];
  const { nr, nc } = samples;
  const read = samples.between(rasters.height);
  const step = passport.step_m * win.stride;
  const radiusM = radius / UNITS_PER_METRE;
  const length = HACHURE_SHARE * step;
  for (let i = 1; i < nr - 1; i++) {
    for (let j = 1; j < nc - 1; j++) {
      if (!cliffs.has(rasters.form[samples.index(i, j)])) continue;
      const here = samples.geo(i, j);
      const cos = Math.max(Math.cos(here.lat * RAD), 0.05);
      const east = (read[i * nc + j + 1] - read[i * nc + j - 1]) / (2 * step * cos);
      const north = (read[(i + 1) * nc + j] - read[(i - 1) * nc + j]) / (2 * step);
      const norm = Math.hypot(east, north);
      if (norm === 0) continue;
      //: Down the slope: against the gradient, in metres, then in degrees.
      const dx = (-east / norm) * length;
      const dy = (-north / norm) * length;
      out.push([
        here,
        { lat: here.lat + dy / radiusM / RAD, lon: here.lon + dx / (radiusM * cos) / RAD },
      ]);
    }
  }
  return out;
}

/** The rivers: every river cell of the water raster joined to its river
 *  neighbours to the east and the south (each pair once), so the cells
 *  read as threads. */
export function rivers(
  rasters: Rasters,
  samples: Samples,
  water: readonly string[],
  topKm2 = 0,
): River[] {
  const river = water.indexOf("river");
  if (river < 0) return [];
  const out: River[] = [];
  const { nr, nc } = samples;
  const is = (i: number, j: number) => rasters.water[samples.index(i, j)] === river;
  //: How much land drains through the cell, off the flow raster: a byte on
  //: a log scale to the greatest catchment of the planet (`flow_max_km2`).
  const drains = (i: number, j: number) =>
    topKm2 > 0
      ? Math.expm1((rasters.flow[samples.index(i, j)] / BYTE) * Math.log1p(topKm2))
      : 0;
  for (let i = 0; i < nr; i++) {
    for (let j = 0; j < nc - 1; j++) {
      if (!is(i, j)) continue;
      const here = samples.geo(i, j);
      const flow = drains(i, j);
      const joins: [number, number][] = [[i, j + 1], [i + 1, j], [i + 1, j + 1], [i + 1, j - 1]];
      for (const [ni, nj] of joins) {
        if (ni >= nr || nj < 0 || nj >= nc - 1) continue;
        //: A reach carries what the smaller of its two ends does: a river
        //: does not widen because it happens to run beside a bigger one.
        if (is(ni, nj)) out.push({ at: [here, samples.geo(ni, nj)], flow: Math.min(flow, drains(ni, nj)) });
      }
    }
  }
  return out;
}

/** The frame's width in metres from half its width in map units. */
export function frameMetres(within: number | undefined): number {
  return within === undefined ? Infinity : (2 * within) / UNITS_PER_METRE;
}

/** Segments in bins of `BIN_DEG` a side, so a frame takes the bins under it
 *  and no more.
 *
 *  Keyed by a segment's first end, which for a segment one cell long -- all
 *  the coast and the rivers are -- is the segment. `middle` keys by the
 *  point halfway instead, for the joined runs of `provinceEdges`: those are
 *  cut so that a run lies inside one bin, and its middle is the bin it lies
 *  in, while its first end may stand half a cell out of it. Safe for those
 *  and not in general: a segment across the date line has no halfway point
 *  worth the name, and a run is cut before it reaches one. */
export type Bins = Map<string, Segment[]>;

export function binKey(lat: number, lon: number): string {
  const b = Math.floor((lat + 90) / BIN_DEG);
  const l = ((Math.floor((lon + 180) / BIN_DEG) % (360 / BIN_DEG)) + 360 / BIN_DEG) % (360 / BIN_DEG);
  return `${b}:${l}`;
}

export function binned(segments: readonly Segment[], middle = false): Bins {
  const bins: Bins = new Map();
  for (const segment of segments) {
    const [a, b] = segment;
    const key = middle ? binKey((a.lat + b.lat) / 2, (a.lon + b.lon) / 2) : binKey(a.lat, a.lon);
    let bin = bins.get(key);
    if (!bin) bins.set(key, (bin = []));
    bin.push(segment);
  }
  return bins;
}

/**
 * The segments of the bins under a frame about the eye.
 *
 * The far side of the globe is dropped bin by bin, before any of it is
 * projected. On the planet's frame the window spans the whole sphere and
 * the bins of the hemisphere behind the eye are half of everything there
 * is; every segment of them projects only to be thrown away for facing
 * away. A bin is kept when its middle stands within a right angle of the
 * eye and a bin's own width -- past that its nearest corner is behind the
 * limb too.
 */
export function underFrame(bins: Bins, eye: Eye, radius: number, within: number | undefined): Segment[] {
  const span = spanOf({ rows: 0, cols: 0 }, eye, frameAngle(radius, within));
  const out: Segment[] = [];
  const bands = 360 / BIN_DEG;
  const b0 = Math.max(0, Math.floor((span.latLo + 90) / BIN_DEG));
  const b1 = Math.min(180 / BIN_DEG - 1, Math.floor((span.latHi + 90) / BIN_DEG));
  const l0 = span.whole ? 0 : Math.floor((span.lonLo + 180) / BIN_DEG);
  const l1 = span.whole ? bands - 1 : Math.floor((span.lonHi + 180) / BIN_DEG);
  const seen = Math.cos((90 + BIN_DEG) * RAD);
  const eyeLat = eye.lat * RAD;
  for (let b = b0; b <= b1; b++) {
    const lat = (b * BIN_DEG - 90 + BIN_DEG / 2) * RAD;
    for (let l = l0; l <= l1; l++) {
      const lane = ((l % bands) + bands) % bands;
      const lon = (lane * BIN_DEG - 180 + BIN_DEG / 2 - eye.lon) * RAD;
      const near =
        Math.sin(eyeLat) * Math.sin(lat) + Math.cos(eyeLat) * Math.cos(lat) * Math.cos(lon);
      if (near < seen) continue;
      const bin = bins.get(`${b}:${lane}`);
      if (bin) for (const segment of bin) out.push(segment);
    }
  }
  return out;
}

/** Where a province's name is written: the mean of its ground, which for a
 *  patch of land is inside it. */
export type ProvinceMark = { code: number; at: Geo };

/** The lines of a planet that do not depend on the frame: the coast by
 *  the form of its land, the lakes' shores and the rivers, read once cell
 *  by cell and binned. The rivers are kept in bands of width rather than as
 *  one heap: a stroke has one thickness, and a river that widens downstream
 *  is drawn as a few strokes, each of the width its reaches share. */
export type PlanetLines = {
  shores: Record<Shore, Bins>;
  lakes: Bins;
  rivers: { widthM: number; bins: Bins }[];
};

/** Into how many widths the rivers of a planet are sorted. Five: fewer and
 *  a brook is drawn as a river, more and the map pays for strokes no eye
 *  can tell apart. */
export const RIVER_BANDS = 5;

/** The reaches sorted into bands by width, widest last so the great rivers
 *  are drawn over the brooks that feed them. */
export function riverBands(reaches: readonly River[]): { widthM: number; bins: Bins }[] {
  const widest = reaches.reduce((top, one) => Math.max(top, riverWidthM(one.flow)), 0);
  if (!(widest > 0)) return [];
  const bands: Segment[][] = Array.from({ length: RIVER_BANDS }, () => []);
  for (const reach of reaches) {
    //: By the square root of the width, so the narrow bands -- where most
    //: of a river system's length lies -- are not all one.
    const share = Math.sqrt(riverWidthM(reach.flow) / widest);
    bands[Math.min(RIVER_BANDS - 1, Math.floor(share * RIVER_BANDS))].push(reach.at);
  }
  return bands
    .map((segments, band) => ({
      widthM: widest * ((band + 1) / RIVER_BANDS) ** 2,
      bins: binned(segments),
    }))
    .filter((band) => band.bins.size > 0);
}

/** A planet's provinces as the map draws them: the boundaries binned, and
 *  a place for every name. Read once per planet, like the coast -- and
 *  apart from it, because the two are drawn on opposite frames and the
 *  coast's walk must not be paid for on the planet's disk.
 *
 *  The lattice is an argument, not a fact of the passport: what this walks
 *  is a mesh of latitude and longitude, and where each of its points
 *  reaches into the rasters is the grid's business (D-328), not the
 *  drawing's. Left out, it is the planet's own. */
export type ProvinceLines = { edges: Bins; marks: ProvinceMark[] };

export function provinceLines(
  rasters: Rasters,
  passport: RasterPassport,
  lattice: Lattice = latticeOf(passport),
): ProvinceLines {
  const samples = wholeSamples(lattice);
  return {
    edges: binned(provinceEdges(rasters, samples), true),
    marks: provinceMarks(rasters, samples),
  };
}

/**
 * The boundary between provinces: the edge two cells of different code
 * share. Not an isoline -- a province is a name, not a level, and the two
 * neighbours are equals: the line runs between them, along the cells.
 *
 * The sea has no province (code 0), and its edge with the land is the
 * coast's business, drawn there in the colour of the water: a boundary
 * drawn over it would double the shore.
 *
 * A run of edges along one meridian or one parallel comes out as a single
 * segment rather than one a cell: a boundary of a province is thousands of
 * cells long, and the path is rebuilt for every turn of the eye. A run ends
 * where the two provinces it parts change -- one segment is one boundary --
 * and where it would leave the bin it is filed under.
 */
export function provinceEdges(rasters: Rasters, samples: Samples): Segment[] {
  const out: Segment[] = [];
  const { nr, nc } = samples;
  const code = (i: number, j: number) => rasters.province[samples.index(i, j)];
  //: Two cells are on opposite sides of a boundary when both are in a
  //: province and the provinces differ; the pair, in one number, says which
  //: boundary it is, so a run can end where the boundary does.
  const seam = (a: number, b: number) => (a > 0 && b > 0 && a !== b ? a * 256 + b : 0);
  //: Which bin a cell falls in, down and along. A run is cut where its
  //: cells would leave one bin, so that the whole of a run lies in the bin
  //: its middle is in and `binned(.., true)` can file it there exactly --
  //: a run filed in a bin it does not lie in would go missing from every
  //: frame that it crosses and that bin does not. A run reaching the date
  //: line is cut by the same rule, which is also what keeps a run's middle
  //: an honest average of its two ends.
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

/** Half a cell: the boundary runs between the centres, not through them. */
const MARK_HALF = 0.5;

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

export function planetLines(
  rasters: Rasters,
  passport: RasterPassport,
  lattice: Lattice = latticeOf(passport),
): PlanetLines {
  const samples = wholeSamples(lattice);
  const { shores, lakes } = coast(rasters, samples, passport.forms);
  return {
    shores: { rock: binned(shores.rock), beach: binned(shores.beach), shore: binned(shores.shore) },
    lakes: binned(lakes),
    rivers: riverBands(rivers(rasters, samples, passport.water, passport.flow_max_km2 ?? 0)),
  };
}

/** The lines of a frame that depend on it: the contours at the frame's
 *  interval and, from the city frame in, the hachures. */
export type FrameLines = {
  contours: { level: number; index: boolean; segments: Segment[] }[];
  hachures: Segment[];
};

export function frameLines(
  rasters: Rasters,
  passport: RasterPassport,
  eye: Eye,
  radius: number,
  within: number | undefined,
  lattice: Lattice = latticeOf(passport),
): FrameLines {
  const frame = frameMetres(within);
  const interval = contourInterval(frame);
  const close = closeFrame(frame);
  if (!Number.isFinite(interval) && !close) return { contours: [], hachures: [] };
  const win = windowAbout(lattice, eye, radius, within);
  const samples = samplesOf(lattice, win);
  return {
    contours: contours(rasters, samples, interval),
    hachures: close ? hachures(rasters, samples, passport, win, radius) : [],
  };
}

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
