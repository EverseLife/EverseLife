// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The lines of the relief (landscape plan wave 6, §9.5-9.6): contours,
 * the coast, the hachures of a cliff and the rivers -- geometry read off
 * the field's rasters, drawn by the vector layer over the shaded ground.
 * Lines are vector on purpose: thin and sharp at any zoom (§9.5), and the
 * SVG's business, not the shader's.
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
/** Below this frame width the hachures of the cliffs are drawn: the city
 *  frame and nearer (plan §9.7), where the window is read cell by cell. */
export const CLOSE_FRAME_M = 45_000;
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
  passport: { rows: number; cols: number },
  eye: Eye,
  ang: number,
): { latLo: number; latHi: number; lonLo: number; lonHi: number; whole: boolean } {
  const latLo = Math.max(-90, eye.lat - ang);
  const latHi = Math.min(90, eye.lat + ang);
  const nearestPole = Math.max(Math.abs(latLo), Math.abs(latHi));
  const spread = ang / Math.max(1e-9, Math.cos(nearestPole * RAD));
  const whole = latLo <= -90 || latHi >= 90 || spread >= 180;
  void passport;
  return { latLo, latHi, lonLo: eye.lon - spread, lonHi: eye.lon + spread, whole };
}

/**
 * The window a frame about the eye needs: the rows under the arc the
 * frame's corner subtends, every column when a pole is in, and a stride
 * that keeps the samples within the budget. `within` is half the frame's
 * width in map units; undefined means the whole hemisphere.
 */
export function windowAbout(
  passport: { rows: number; cols: number },
  eye: Eye,
  radius: number,
  within: number | undefined,
): Window {
  const { rows, cols } = passport;
  const span = spanOf(passport, eye, frameAngle(radius, within));
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
export function wholeWindow(passport: { rows: number; cols: number }): Window {
  return { r0: 0, r1: passport.rows - 1, c0: 0, count: passport.cols, stride: 1 };
}

/** The samples of a window: a grid `nr` by `nc` of cell readings, and the
 *  place of a sample by its fractional indices. */
export type Samples = {
  nr: number;
  nc: number;
  /** The raster's row and column of sample (i, j). */
  cell: (i: number, j: number) => [number, number];
  /** Degrees of sample (i, j), fractional indices allowed. */
  geo: (i: number, j: number) => Geo;
  index: (i: number, j: number) => number;
};

export function samplesOf(passport: { rows: number; cols: number }, win: Window): Samples {
  const { rows, cols } = passport;
  const { r0, r1, c0, count, stride } = win;
  const nr = Math.floor((r1 - r0) / stride) + 1;
  //: A window round the whole planet closes on itself: one sample more, so
  //: the last quad joins the first column and no seam opens on `c0`.
  const nc = Math.max(1, Math.ceil(count / stride)) + (count >= cols ? 1 : 0);
  const cell = (i: number, j: number): [number, number] => [
    Math.min(rows - 1, r0 + i * stride),
    (((c0 + j * stride) % cols) + cols) % cols,
  ];
  return {
    nr,
    nc,
    cell,
    geo: (i, j) => ({
      lat: -90 + (r0 + i * stride + 0.5) * (180 / rows),
      lon: ((((c0 + j * stride + 0.5) * (360 / cols)) % 360) + 360) % 360 - 180,
    }),
    index: (i, j) => {
      const [r, c] = cell(i, j);
      return r * cols + c;
    },
  };
}

/** A line as two ends, degrees. */
export type Segment = [Geo, Geo];

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
): Segment[] {
  const out: Segment[] = [];
  const { nr, nc, geo } = samples;
  const cut = (a: number, b: number) => {
    const span = b - a;
    return span === 0 ? 0.5 : Math.max(0, Math.min(1, (level - a) / span));
  };
  const put = (i: number, j: number, ...segments: Segment[]) => {
    for (const segment of segments) {
      out.push(segment);
      each?.(i, j);
    }
  };
  for (let i = 0; i < nr - 1; i++) {
    for (let j = 0; j < nc - 1; j++) {
      const v00 = value(i, j);
      const v01 = value(i, j + 1);
      const v11 = value(i + 1, j + 1);
      const v10 = value(i + 1, j);
      const bits = (v00 >= level ? 1 : 0) | (v01 >= level ? 2 : 0) | (v11 >= level ? 4 : 0) | (v10 >= level ? 8 : 0);
      if (bits === 0 || bits === 15) continue;
      //: The four edge crossings: top (i, j..j+1), right (i..i+1, j+1),
      //: bottom (i+1, j..j+1), left (i..i+1, j).
      const top = () => geo(i, j + cut(v00, v01));
      const right = () => geo(i + cut(v01, v11), j + 1);
      const bottom = () => geo(i + 1, j + cut(v10, v11));
      const left = () => geo(i + cut(v00, v10), j);
      switch (bits) {
        case 1:
        case 14:
          put(i, j, [left(), top()]);
          break;
        case 2:
        case 13:
          put(i, j, [top(), right()]);
          break;
        case 3:
        case 12:
          put(i, j, [left(), right()]);
          break;
        case 4:
        case 11:
          put(i, j, [right(), bottom()]);
          break;
        case 6:
        case 9:
          put(i, j, [top(), bottom()]);
          break;
        case 7:
        case 8:
          put(i, j, [left(), bottom()]);
          break;
        case 5:
        case 10: {
          //: Two high corners on a diagonal (5: top-left and bottom-right).
          //: With the middle high too, the high corners join through it and
          //: the line cuts off the two low corners; with the middle low, the
          //: high corners are cut off each on its own.
          const middle = (v00 + v01 + v11 + v10) / 4 >= level;
          if ((bits === 5) === middle) {
            put(i, j, [top(), right()], [left(), bottom()]);
          } else {
            put(i, j, [left(), top()], [right(), bottom()]);
          }
          break;
        }
      }
    }
  }
  return out;
}

/** The contours of a window: the level lines of the height at `interval`
 *  metres from the first above the sea to the highest sampled, each
 *  marked whether it is an index contour. */
export function contours(
  rasters: Rasters,
  samples: Samples,
  interval: number,
): { level: number; index: boolean; segments: Segment[] }[] {
  if (!Number.isFinite(interval) || interval <= 0) return [];
  const value = (i: number, j: number) => rasters.height[samples.index(i, j)];
  let top = 0;
  for (let i = 0; i < samples.nr; i++) {
    for (let j = 0; j < samples.nc; j++) top = Math.max(top, value(i, j));
  }
  const out: { level: number; index: boolean; segments: Segment[] }[] = [];
  //: Strictly under the summit: a contour at the summit's own height is a point.
  for (let level = interval; level < top; level += interval) {
    const segments = isolines(samples, value, level);
    if (segments.length) {
      out.push({ level, index: Math.round(level / interval) % INDEX_EVERY === 0, segments });
    }
  }
  return out;
}

/** How a stretch of coast is drawn: a rock wall, a beach, or plain shore. */
export type Shore = "rock" | "beach" | "shore";

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
  const height = (i: number, j: number) => rasters.height[samples.index(i, j)];
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
  const segments = isolines(samples, height, 0, (i, j) => styles.push(styleOf(i, j)));
  segments.forEach((segment, k) => shores[styles[k]].push(segment));
  const lakes =
    lake < 0
      ? []
      : isolines(samples, (i, j) => (rasters.form[samples.index(i, j)] === lake ? 1 : 0), 0.5);
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
  const step = passport.step_m * win.stride;
  const radiusM = radius / UNITS_PER_METRE;
  const length = HACHURE_SHARE * step;
  for (let i = 1; i < nr - 1; i++) {
    for (let j = 1; j < nc - 1; j++) {
      if (!cliffs.has(rasters.form[samples.index(i, j)])) continue;
      const here = samples.geo(i, j);
      const cos = Math.max(Math.cos(here.lat * RAD), 0.05);
      const east = (rasters.height[samples.index(i, j + 1)] - rasters.height[samples.index(i, j - 1)]) / (2 * step * cos);
      const north = (rasters.height[samples.index(i + 1, j)] - rasters.height[samples.index(i - 1, j)]) / (2 * step);
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
export function rivers(rasters: Rasters, samples: Samples, water: readonly string[]): Segment[] {
  const river = water.indexOf("river");
  if (river < 0) return [];
  const out: Segment[] = [];
  const { nr, nc } = samples;
  const is = (i: number, j: number) => rasters.water[samples.index(i, j)] === river;
  for (let i = 0; i < nr; i++) {
    for (let j = 0; j < nc - 1; j++) {
      if (!is(i, j)) continue;
      const here = samples.geo(i, j);
      const joins: [number, number][] = [[i, j + 1], [i + 1, j], [i + 1, j + 1], [i + 1, j - 1]];
      for (const [ni, nj] of joins) {
        if (ni >= nr || nj < 0 || nj >= nc - 1) continue;
        if (is(ni, nj)) out.push([here, samples.geo(ni, nj)]);
      }
    }
  }
  return out;
}

/** The frame's width in metres from half its width in map units. */
export function frameMetres(within: number | undefined): number {
  return within === undefined ? Infinity : (2 * within) / UNITS_PER_METRE;
}

/** Segments in bins of `BIN_DEG` a side, keyed by the bin of a segment's
 *  first end, so a frame takes the bins under it and no more. */
export type Bins = Map<string, Segment[]>;

export function binKey(lat: number, lon: number): string {
  const b = Math.floor((lat + 90) / BIN_DEG);
  const l = ((Math.floor((lon + 180) / BIN_DEG) % (360 / BIN_DEG)) + 360 / BIN_DEG) % (360 / BIN_DEG);
  return `${b}:${l}`;
}

export function binned(segments: readonly Segment[]): Bins {
  const bins: Bins = new Map();
  for (const segment of segments) {
    const key = binKey(segment[0].lat, segment[0].lon);
    let bin = bins.get(key);
    if (!bin) bins.set(key, (bin = []));
    bin.push(segment);
  }
  return bins;
}

/** The segments of the bins under a frame about the eye. */
export function underFrame(bins: Bins, eye: Eye, radius: number, within: number | undefined): Segment[] {
  const span = spanOf({ rows: 0, cols: 0 }, eye, frameAngle(radius, within));
  const out: Segment[] = [];
  const bands = 360 / BIN_DEG;
  const b0 = Math.max(0, Math.floor((span.latLo + 90) / BIN_DEG));
  const b1 = Math.min(180 / BIN_DEG - 1, Math.floor((span.latHi + 90) / BIN_DEG));
  const l0 = span.whole ? 0 : Math.floor((span.lonLo + 180) / BIN_DEG);
  const l1 = span.whole ? bands - 1 : Math.floor((span.lonHi + 180) / BIN_DEG);
  for (let b = b0; b <= b1; b++) {
    for (let l = l0; l <= l1; l++) {
      const bin = bins.get(`${b}:${((l % bands) + bands) % bands}`);
      if (bin) for (const segment of bin) out.push(segment);
    }
  }
  return out;
}

/** The lines of a planet that do not depend on the frame: the coast by
 *  the form of its land, the lakes' shores and the rivers, read once cell
 *  by cell and binned. */
export type PlanetLines = {
  shores: Record<Shore, Bins>;
  lakes: Bins;
  rivers: Bins;
};

export function planetLines(rasters: Rasters, passport: RasterPassport): PlanetLines {
  const samples = samplesOf(passport, wholeWindow(passport));
  const { shores, lakes } = coast(rasters, samples, passport.forms);
  return {
    shores: { rock: binned(shores.rock), beach: binned(shores.beach), shore: binned(shores.shore) },
    lakes: binned(lakes),
    rivers: binned(rivers(rasters, samples, passport.water)),
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
): FrameLines {
  const frame = frameMetres(within);
  const interval = contourInterval(frame);
  const close = frame <= CLOSE_FRAME_M;
  if (!Number.isFinite(interval) && !close) return { contours: [], hachures: [] };
  const win = windowAbout(passport, eye, radius, within);
  const samples = samplesOf(passport, win);
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
