// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The ground under the surface (D-319, wave 4): land and water, the climate's
 * tone, the night.
 *
 * The planet's relief is a grid of heights from `/public/terrain` -- one
 * answer per planet, everybody's from the world's first day. The globe draws
 * it as flat colour and nothing else (plan, "Climate field": muted bands of
 * tone, no textures): the sea is the disk of the sphere, the land its cells
 * laid over it, tinted by the row's warmth, the mountains a shade apart, the
 * lakes holes in it. The rivers of the field are not drawn (`Ground`).
 *
 * The **terminator** is the night half's shadow, computed from the clock at
 * the moment of drawing: not a timer and not a request (D-226) -- what is
 * already known, redrawn with any render, and between renders it stands
 * still (Terra turns a quarter of a degree a minute). The sun stands over
 * the equator: no axial tilt is modelled, so the shadow's edge passes
 * through the poles.
 *
 * Pure arithmetic, so a test can draw a night without a DOM.
 */

import type { Terrain, Tile } from "../../api";
import { project, type Eye, type Geo } from "./globe";
import type { Point } from "./model";

/** How many pieces the terminator and the limb are drawn in. */
export const NIGHT_STEPS = 48;
/** The land's tone by the row's warmth: the two bounds of the climate. */
export type Warmth = { cold: number; cool: number };
export type Tone = "cold" | "cool" | "warm";

const RAD = Math.PI / 180;
const MS_PER_HOUR = 3_600_000;

/** Where the sun stands over the planet now: over the equator, at the
 *  longitude whose day is at noon. The planet's day is `dayHours` long and
 *  counted from the world's epoch (D-029), noon being half of it -- and a
 *  place east of the meridian reaches noon first, by its longitude's share
 *  of the turn (D-319). Nothing without an epoch: a world not yet born has
 *  no hour. */
export function subsolar(epoch: string | null, dayHours: number, nowMs: number): Geo | null {
  if (!epoch || !(dayHours > 0)) return null;
  const dayMs = dayHours * MS_PER_HOUR;
  const turned = (((nowMs - new Date(epoch).getTime()) % dayMs) + dayMs) % dayMs / dayMs;
  const lon = (0.5 - turned) * 360;
  return { lat: 0, lon: ((lon + 180) % 360 + 360) % 360 - 180 };
}

type Vec = [number, number, number];

const toVec = (p: Geo): Vec => {
  const lat = p.lat * RAD;
  const lon = p.lon * RAD;
  return [Math.cos(lat) * Math.cos(lon), Math.cos(lat) * Math.sin(lon), Math.sin(lat)];
};
const dot = (a: Vec, b: Vec) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: Vec, b: Vec): Vec => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
];
const unit = (a: Vec): Vec => {
  const n = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / n, a[1] / n, a[2] / n];
};

/** The frame's axes at the eye: what is east, what is north, what faces it. */
function axes(eye: Eye): { east: Vec; north: Vec; out: Vec } {
  const lat = eye.lat * RAD;
  const lon = eye.lon * RAD;
  return {
    east: [-Math.sin(lon), Math.cos(lon), 0],
    north: [-Math.sin(lat) * Math.cos(lon), -Math.sin(lat) * Math.sin(lon), Math.cos(lat)],
    out: toVec(eye),
  };
}

/**
 * The night as a closed path over the visible disk, or null when the whole
 * disk is lit. The terminator is the great circle a quarter turn from the
 * sun; the half of it that faces the eye runs from limb to limb, and the
 * path closes along the limb on the side the sun does not reach. When the
 * sun stands behind the eye's own point the whole disk is night.
 */
export function nightPath(eye: Eye, radius: number, sun: Geo): string | null {
  const { east, north, out } = axes(eye);
  const s = toVec(sun);
  const screen = (p: Vec) => `${radius * dot(p, east)},${-radius * dot(p, north)}`;
  const facing = dot(s, out);
  if (Math.abs(facing) > 1 - 1e-9) {
    return facing < 0 ? `M${radius},0A${radius},${radius} 0 1 1 ${-radius},0A${radius},${radius} 0 1 1 ${radius},0Z` : null;
  }
  //: `u` is where the terminator crosses the limb; `w` completes the circle,
  //: and the half that faces the eye is the one that rises towards it.
  const u = unit(cross(s, out));
  let w = cross(s, u);
  if (dot(w, out) < 0) w = [-w[0], -w[1], -w[2]];
  const run: string[] = [];
  for (let i = 0; i <= NIGHT_STEPS; i++) {
    const theta = (i / NIGHT_STEPS) * Math.PI;
    const p: Vec = [
      Math.cos(theta) * u[0] + Math.sin(theta) * w[0],
      Math.cos(theta) * u[1] + Math.sin(theta) * w[1],
      Math.cos(theta) * u[2] + Math.sin(theta) * w[2],
    ];
    run.push(screen(p));
  }
  //: Back along the limb from -u to u, through `n` -- the point of the limb
  //: a quarter turn from both, on the side the sun does not reach.
  let n = unit(cross(out, u));
  if (dot(n, s) > 0) n = [-n[0], -n[1], -n[2]];
  for (let i = 1; i < NIGHT_STEPS; i++) {
    const phi = (i / NIGHT_STEPS) * Math.PI;
    const p: Vec = [
      -Math.cos(phi) * u[0] + Math.sin(phi) * n[0],
      -Math.cos(phi) * u[1] + Math.sin(phi) * n[1],
      -Math.cos(phi) * u[2] + Math.sin(phi) * n[2],
    ];
    run.push(screen(p));
  }
  return `M${run.join("L")}Z`;
}

/** The tone of a row of land by its warmth. */
export function toneOf(warmth: number, bands: Warmth): Tone {
  if (warmth < bands.cold) return "cold";
  if (warmth < bands.cool) return "cool";
  return "warm";
}

/** What kind of ground a cell is: the sea, a lake, a mountain, or land of
 *  a tone. Sea is what is under the sea level and not a lake. */
export type Kind = Tone | "high" | "water" | "sea";

/** The kind of the cell under a point of the sphere. */
export function kindAt(terrain: Terrain, at: Geo, bands: Warmth, tiles?: Tiles): Kind {
  const { rows, cols } = terrain;
  const r = Math.min(rows - 1, Math.max(0, Math.floor(((at.lat + 90) * rows) / 180)));
  const c = (((Math.floor(((at.lon + 180) * cols) / 360) % cols) + cols) % cols);
  const height = terrain.grid[r][c];
  if (terrain.lakes.some(([lr, lc]) => lr === r && lc === c)) return "water";
  if (height < terrain.sea_level) return "sea";
  if (height >= terrain.mountain_level) return "high";
  //: On land the local relief has the last word, where its tile is known.
  const local = tiles ? localAt(terrain, tiles, at.lat, at.lon) : null;
  if (local !== null) {
    if (terrain.wet && local <= terrain.basin_level) return "water";
    if (local >= terrain.peak_level) return "high";
  }
  return toneOf(terrain.warmth[r], bands);
}

/** The tiles of a planet's local relief the page holds, by `tileKey`. */
export type Tiles = ReadonlyMap<string, Tile>;

export const tileKey = (row: number, col: number): string => `${row}:${col}`;

/** Which tile a point lies on. */
export function tileOf(terrain: Terrain, lat: number, lon: number): [number, number] {
  const { deg } = terrain.tile;
  const rows = Math.round(180 / deg);
  const cols = Math.round(360 / deg);
  const row = Math.min(rows - 1, Math.max(0, Math.floor((lat + 90) / deg)));
  const col = ((Math.floor((lon + 180) / deg) % cols) + cols) % cols;
  return [row, col];
}

/**
 * The tiles a frame needs: those under the arc the frame's corner subtends
 * about the eye (as `lattice` cuts the rows), every column when a pole is
 * in. None when the frame would need more than `TILE_LIMIT`: that frame is
 * far enough out for the grid to do -- and a frame at a pole, which would
 * need every column, gets none: no city stands past `map.city_lat_max`.
 */
export const TILE_LIMIT = 16;
export function tilesAbout(
  terrain: Terrain,
  eye: Eye,
  radius: number,
  within: number,
): [number, number][] {
  const { deg } = terrain.tile;
  const rows = Math.round(180 / deg);
  const cols = Math.round(360 / deg);
  const reach = within * Math.SQRT2;
  const ang = reach >= radius ? 90 : Math.asin(reach / radius) / RAD;
  const latLo = Math.max(-90, eye.lat - ang);
  const latHi = Math.min(90, eye.lat + ang);
  const nearestPole = Math.max(Math.abs(latLo), Math.abs(latHi));
  const spread = ang / Math.max(1e-9, Math.cos(nearestPole * RAD));
  const r0 = Math.min(rows - 1, Math.max(0, Math.floor((latLo + 90) / deg)));
  const r1 = Math.min(rows - 1, Math.max(0, Math.floor((latHi + 90) / deg)));
  let c0 = 0;
  let count = cols;
  if (latLo > -90 && latHi < 90 && spread < 180) {
    c0 = Math.floor((eye.lon - spread + 180) / deg);
    count = Math.floor((eye.lon + spread + 180) / deg) - c0 + 1;
  }
  const out: [number, number][] = [];
  for (let r = r0; r <= r1; r++) {
    for (let k = 0; k < count; k++) out.push([r, (((c0 + k) % cols) + cols) % cols]);
  }
  return out.length > TILE_LIMIT ? [] : out;
}

/** The local noise at a point, read bilinearly off its tile -- or nothing,
 *  when the page does not hold that tile. */
export function localAt(terrain: Terrain, tiles: Tiles, lat: number, lon: number): number | null {
  const [row, col] = tileOf(terrain, lat, lon);
  const tile = tiles.get(tileKey(row, col));
  if (!tile) return null;
  const { n, step } = tile;
  const fi = Math.min(n, Math.max(0, (lat - tile.lat0) / step));
  const fj = Math.min(n, Math.max(0, ((((lon - tile.lon0) % 360) + 360) % 360) / step));
  const i0 = Math.min(n - 1, Math.floor(fi));
  const j0 = Math.min(n - 1, Math.floor(fj));
  const ti = fi - i0;
  const tj = fj - j0;
  const g = tile.local;
  return (
    (g[i0][j0] * (1 - tj) + g[i0][j0 + 1] * tj) * (1 - ti) +
    (g[i0 + 1][j0] * (1 - tj) + g[i0 + 1][j0 + 1] * tj) * ti
  );
}

export type GroundPaths = {
  land: Record<Tone, string>;
  high: string;
  /** The lakes: a river's end, a basin of the local relief -- drawn over
   *  the land in water's own tone, so a lake is told from the sea. */
  water: string;
};

/** How many cells of the grid make one drawn cell while the disk is
 *  smaller than the frame: a cell is then a pixel or two, and the descent
 *  redraws the ground at every step. */
export const COARSE_STRIDE = 3;
/** How fine the grid is read close up: half a cell, four times the cells
 *  of the grid, and the coast's line through them twice as supple. */
export const FINE_UNIT = 0.5;

/** The height of the field at a point, read between the four nearest
 *  cells: the grid is two degrees, and a coast drawn cell by cell is a
 *  staircase; read between the cells it bends. */
export function heightAt(terrain: Terrain, lat: number, lon: number): number {
  const { rows, cols, grid } = terrain;
  const fr = Math.min(rows - 1, Math.max(0, ((lat + 90) * rows) / 180 - 0.5));
  const fc = ((((lon + 180) * cols) / 360 - 0.5) % cols + cols) % cols;
  const r0 = Math.floor(fr);
  const r1 = Math.min(rows - 1, r0 + 1);
  const c0 = Math.floor(fc);
  const c1 = (c0 + 1) % cols;
  const t = fr - r0;
  const u = fc - c0;
  return (
    grid[r0][c0] * (1 - t) * (1 - u) +
    grid[r0][c1] * (1 - t) * u +
    grid[r1][c0] * t * (1 - u) +
    grid[r1][c1] * t * u
  );
}

/**
 * The part of a cell above `level`, as a polygon: the corners above it,
 * and where an edge crosses the level, the crossing, found by the heights
 * at the edge's ends. A coast is then a line through the cells, not a
 * staircase of them. A saddle -- two opposite corners above, two below --
 * comes out as one polygon joining both; the coast is rarely that finicky.
 */
export function above(
  corners: readonly Point[],
  heights: readonly number[],
  level: number,
): Point[] | null {
  return cut(corners, heights, level)?.points ?? null;
}

/**
 * `above`, carrying a second reading through the cut: the value of
 * `carry` at every vertex of the part kept, the crossings' by the same
 * interpolation -- so the part can be cut again along the other reading.
 * A land polygon cut from the height is cut once more along the noise for
 * its basins; a peak cut from the noise once more along the height for
 * the sea (D-323).
 */
export function cut(
  corners: readonly Point[],
  values: readonly number[],
  level: number,
  carry?: readonly number[],
): { points: Point[]; carry: number[] } | null {
  const n = corners.length;
  const points: Point[] = [];
  const carried: number[] = [];
  let any = false;
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    const a = corners[i];
    const b = corners[j];
    const va = values[i];
    const vb = values[j];
    if (va >= level) {
      points.push(a);
      carried.push(carry ? carry[i] : 0);
      any = true;
    }
    if ((va >= level) !== (vb >= level)) {
      const t = (level - va) / (vb - va);
      points.push({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t });
      carried.push(carry ? carry[i] + (carry[j] - carry[i]) * t : 0);
    }
  }
  return any && points.length >= 3 ? { points, carry: carried } : null;
}

const ring = (points: readonly Point[]): string =>
  `M${points.map((p) => `${p.x},${p.y}`).join("L")}Z`;

/** The grid with its lakes read as just under the sea: a lake is then a
 *  hole the sea's level cuts in the land, and the disk shows through it --
 *  the lake's tone and the sea's are one. */
function withLakesSunk(terrain: Terrain): Terrain {
  if (!terrain.lakes.length) return terrain;
  const grid = terrain.grid.map((row) => row.slice());
  for (const [r, c] of terrain.lakes) grid[r][c] = Math.min(grid[r][c], terrain.sea_level - 1e-6);
  return { ...terrain, grid };
}

/**
 * Where the drawn corners stand, in cells of the grid: a corner at a whole
 * `p` is on the centre of row `p` (and `q`, of column `q`), the lattice a
 * `unit` apart, the poles closing the rows and the ring of columns closing
 * on its first corner. With `within` only what the frame can hold is laid:
 * the rows within the arc the frame's corner subtends about the eye, and
 * the columns that arc spans at the latitude nearest a pole -- every column
 * when a pole is in the frame. The frame, not the planet, is what a
 * redraw costs.
 */
function lattice(
  rows: number,
  cols: number,
  eye: Eye,
  radius: number,
  unit: number,
  within?: number,
): { ps: number[]; qs: number[] } {
  const dlat = 180 / rows;
  const dlon = 360 / cols;
  let latLo = -90;
  let latHi = 90;
  let lonLo: number | null = null;
  let lonHi = 0;
  if (within !== undefined) {
    const reach = within * Math.SQRT2;
    const ang = reach >= radius ? 90 : Math.asin(reach / radius) / RAD;
    latLo = Math.max(-90, eye.lat - ang);
    latHi = Math.min(90, eye.lat + ang);
    const nearestPole = Math.max(Math.abs(latLo), Math.abs(latHi));
    const spread = ang / Math.max(1e-9, Math.cos(nearestPole * RAD));
    if (latLo > -90 && latHi < 90 && spread < 180) {
      lonLo = eye.lon - spread;
      lonHi = eye.lon + spread;
    }
  }
  const pLo = Math.max(-0.5, (latLo + 90) / dlat - 0.5);
  const pHi = Math.min(rows - 0.5, (latHi + 90) / dlat - 0.5);
  const ps: number[] = [];
  for (let k = Math.floor(pLo / unit); k <= Math.ceil(pHi / unit); k++) {
    const p = Math.min(rows - 0.5, Math.max(-0.5, k * unit));
    if (!ps.length || p > ps[ps.length - 1]) ps.push(p);
  }
  const qs: number[] = [];
  if (lonLo === null) {
    for (let k = 0; k * unit < cols; k++) qs.push(k * unit);
    qs.push(cols);
  } else {
    const qLo = (lonLo + 180) / dlon - 0.5;
    const qHi = (lonHi + 180) / dlon - 0.5;
    for (let k = Math.floor(qLo / unit); k <= Math.ceil(qHi / unit); k++) qs.push(k * unit);
  }
  return { ps, qs };
}

/**
 * The cells of the relief as paths, as the eye sees them: land by tone and
 * mountains. The sea is not drawn -- it is the disk under everything --
 * and a lake is a hole in the land the disk shows through. A cell is drawn
 * when any of its corners faces the eye, the far corners pushed to the
 * horizon, so the land meets the edge of the disk. The drawn corners stand
 * on the grid's cell centres, `unit` cells apart (`lattice`): coarser than
 * the grid on the approach (`COARSE_STRIDE`), finer than it close up. The
 * height is read at the corners and the cell is cut along the sea's level
 * and the mountains' (`above`): the coast and the tree line run through
 * the cells as lines, and a lone cell of land or of peak is a diamond
 * about its centre, not nothing. Where the page holds the tiles of the
 * local relief (D-323) the corners read them instead: on land a peak of
 * the noise is a mountain and a basin a lake, cut along the noise's
 * levels -- the very lines a scout's feet find.
 */
export function cellPaths(
  terrain: Terrain,
  eye: Eye,
  radius: number,
  bands: Warmth,
  unit = 1,
  within?: number,
  tiles?: Tiles,
): GroundPaths {
  const { rows, cols } = terrain;
  const field = withLakesSunk(terrain);
  const fine = tiles && tiles.size > 0 ? tiles : null;
  const dlat = 180 / rows;
  const dlon = 360 / cols;
  const { ps, qs } = lattice(rows, cols, eye, radius, unit, within);
  const nc = qs.length;
  //: One projection and one height per drawn corner, shared by the four
  //: cells round it. A corner facing away is pushed out to the limb along
  //: its own ray: a cell cut by the horizon is then drawn up to the horizon.
  const corners: (Point | null)[] = new Array(ps.length * nc);
  const front: boolean[] = new Array(ps.length * nc);
  const heights: number[] = new Array(ps.length * nc);
  //: The local noise at each corner, NaN where its tile is not held.
  const locals: number[] = new Array(ps.length * nc);
  for (let i = 0; i < ps.length; i++) {
    const lat = -90 + (ps[i] + 0.5) * dlat;
    for (let j = 0; j < nc; j++) {
      const lon = -180 + (qs[j] + 0.5) * dlon;
      const seen = project(eye, radius, { lat, lon });
      const k = i * nc + j;
      front[k] = seen.front;
      heights[k] = heightAt(field, lat, lon);
      const local = fine ? localAt(terrain, fine, lat, lon) : null;
      locals[k] = local ?? NaN;
      if (seen.front) corners[k] = { x: seen.x, y: seen.y };
      else {
        const away = Math.hypot(seen.x, seen.y);
        corners[k] = away > 0 ? { x: (seen.x / away) * radius, y: (seen.y / away) * radius } : null;
      }
    }
  }
  const out: Record<string, string[]> = { cold: [], cool: [], warm: [], high: [], water: [] };
  const lakes = new Set(terrain.lakes.map(([r, c]) => r * cols + c));
  for (let i = 0; i + 1 < ps.length; i++) {
    //: The row's tone is the climate's at the cell's middle.
    const r = Math.min(rows - 1, Math.max(0, Math.round((ps[i] + ps[i + 1]) / 2)));
    const tone = toneOf(terrain.warmth[r], bands);
    for (let j = 0; j + 1 < nc; j++) {
      const ka = i * nc + j;
      const kb = ka + 1;
      const ke = (i + 1) * nc + j;
      const kd = ke + 1;
      //: A cell with no corner facing the eye is behind the sphere.
      if (!front[ka] && !front[kb] && !front[kd] && !front[ke]) continue;
      const a = corners[ka];
      const b = corners[kb];
      const d = corners[kd];
      const e = corners[ke];
      if (!a || !b || !d || !e) continue;
      //: Outside the frame of `within` about the eye a cell is not laid:
      //: the lattice is cut by the arc, this by the square within it.
      if (within !== undefined && outside(within, a, b, d, e)) continue;
      const quad = [a, b, d, e];
      const hs = [heights[ka], heights[kb], heights[kd], heights[ke]];
      const ls = [locals[ka], locals[kb], locals[kd], locals[ke]];
      //: The land is cut from the height along the sea's level; where the
      //: corners read their tiles it is cut once more along the noise, a
      //: basin a hole in it -- on a coast cell too, as the server reads it.
      const known = ls.every(Number.isFinite);
      //: A river's lake is its whole cell of the grid -- the square about
      //: the grid's centre, half a cell each way -- water over the hole the
      //: sunk height leaves in the land: every drawn cell whose middle lies
      //: in that square, the square's edge included.
      if (lakes.size && onLake(lakes, rows, cols, (ps[i] + ps[i + 1]) / 2, (qs[j] + qs[j + 1]) / 2)) {
        out.water.push(ring(quad));
      }
      const shore = cut(quad, hs, terrain.sea_level, known ? ls : undefined);
      if (!shore) continue;
      const land = known && terrain.wet ? cut(shore.points, shore.carry, terrain.basin_level) : shore;
      if (land) out[tone].push(ring(land.points));
      //: The basin's part of the shore is the lake: the other side of the
      //: same cut, drawn in water's tone over the land.
      if (known && terrain.wet) {
        const sunk = cut(
          shore.points,
          shore.carry.map((v) => -v),
          -terrain.basin_level,
        );
        if (sunk) out.water.push(ring(sunk.points));
      }
      if (!land) continue;
      //: The planet's ranges from the height, the local peaks from the
      //: noise -- the peak's part that is land, the sea keeps no mountains.
      const high = above(quad, hs, terrain.mountain_level);
      if (high) out.high.push(ring(high));
      const peak = known ? cut(quad, ls, terrain.peak_level, hs) : null;
      const risen = peak && cut(peak.points, peak.carry, terrain.sea_level);
      if (risen) out.high.push(ring(risen.points));
    }
  }
  return {
    land: { cold: out.cold.join(""), cool: out.cool.join(""), warm: out.warm.join("") },
    high: out.high.join(""),
    water: out.water.join(""),
  };
}

/** Whether a drawn cell's middle, in cells of the grid, lies on a lake
 *  cell: within half a cell of its centre, the edge included. */
function onLake(lakes: ReadonlySet<number>, rows: number, cols: number, p: number, q: number): boolean {
  for (const r of [Math.floor(p + 0.5), Math.ceil(p - 0.5)]) {
    if (r < 0 || r >= rows) continue;
    for (const c of [Math.floor(q + 0.5), Math.ceil(q - 0.5)]) {
      if (lakes.has(r * cols + (((c % cols) + cols) % cols))) return true;
    }
  }
  return false;
}

/** Whether all the corners lie beyond one edge of the square frame. */
function outside(within: number, ...corners: Point[]): boolean {
  return (
    corners.every((p) => p.x < -within) ||
    corners.every((p) => p.x > within) ||
    corners.every((p) => p.y < -within) ||
    corners.every((p) => p.y > within)
  );
}

