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
 * lakes water again, the rivers lines.
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

import type { Terrain } from "../../api";
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
export function kindAt(terrain: Terrain, at: Geo, bands: Warmth): Kind {
  const { rows, cols } = terrain;
  const r = Math.min(rows - 1, Math.max(0, Math.floor(((at.lat + 90) * rows) / 180)));
  const c = (((Math.floor(((at.lon + 180) * cols) / 360) % cols) + cols) % cols);
  const height = terrain.grid[r][c];
  if (terrain.lakes.some(([lr, lc]) => lr === r && lc === c)) return "water";
  if (height < terrain.sea_level) return "sea";
  if (height >= terrain.mountain_level) return "high";
  return toneOf(terrain.warmth[r], bands);
}

export type GroundPaths = {
  land: Record<Tone, string>;
  high: string;
  water: string;
};

/** How many cells of the grid make one drawn cell while the disk is
 *  smaller than the frame: a cell is then a pixel or two, and the descent
 *  redraws the ground at every step. */
export const COARSE_STRIDE = 3;
/** How fine the grid is read close up: half a cell, the height between
 *  cells read between them. Four times the cells of the grid. */
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
 * The cells of the relief as paths, as the eye sees them: land by tone,
 * mountains, and the lakes. The sea is not drawn -- it is the disk under
 * everything. A cell is drawn when all four of its corners face the eye,
 * so the coast at the limb frays by a cell at most. The drawn cell is
 * `unit` cells of the grid across: coarser than the grid on the approach
 * (`COARSE_STRIDE`), finer than it close up (`FINE_UNIT`), where the
 * height between the cells is read between them and the coast bends.
 */
export function cellPaths(
  terrain: Terrain,
  eye: Eye,
  radius: number,
  bands: Warmth,
  unit = 1,
  within?: number,
): GroundPaths {
  const { rows, cols } = terrain;
  const dlat = 180 / rows;
  const dlon = 360 / cols;
  const nr = Math.ceil(rows / unit);
  const nc = Math.ceil(cols / unit);
  //: One projection per drawn corner, shared by the four cells round it.
  const corners: (Point | null)[] = new Array((nr + 1) * (nc + 1));
  for (let i = 0; i <= nr; i++) {
    const lat = -90 + Math.min(rows, i * unit) * dlat;
    for (let j = 0; j <= nc; j++) {
      const seen = project(eye, radius, { lat, lon: -180 + Math.min(cols, j * unit) * dlon });
      corners[i * (nc + 1) + j] = seen.front ? { x: seen.x, y: seen.y } : null;
    }
  }
  const out: Record<string, string[]> = { cold: [], cool: [], warm: [], high: [], water: [] };
  const lakes = new Set(terrain.lakes.map(([r, c]) => r * cols + c));
  for (let i = 0; i < nr; i++) {
    const lat = -90 + Math.min(rows, (i + 0.5) * unit) * dlat;
    const r = Math.min(rows - 1, Math.floor((i + 0.5) * unit));
    const tone = toneOf(terrain.warmth[r], bands);
    for (let j = 0; j < nc; j++) {
      const lon = -180 + Math.min(cols, (j + 0.5) * unit) * dlon;
      const c = Math.min(cols - 1, Math.floor((j + 0.5) * unit)) % cols;
      //: Between the cells the height is read between them; on the grid or
      //: coarser it is the cell's own -- the same number either way there.
      const height = unit < 1 ? heightAt(terrain, lat, lon) : terrain.grid[r][c];
      const lake = lakes.has(r * cols + c);
      if (height < terrain.sea_level && !lake) continue;
      const a = corners[i * (nc + 1) + j];
      const b = corners[i * (nc + 1) + j + 1];
      const d = corners[(i + 1) * (nc + 1) + j + 1];
      const e = corners[(i + 1) * (nc + 1) + j];
      if (!a || !b || !d || !e) continue;
      //: Outside a frame of `within` about the eye a cell is not drawn:
      //: close up the frame holds a corner of the disk, not the disk.
      if (within !== undefined && outside(within, a, b, d, e)) continue;
      const kind = lake ? "water" : height >= terrain.mountain_level ? "high" : tone;
      out[kind].push(`M${a.x},${a.y}L${b.x},${b.y}L${d.x},${d.y}L${e.x},${e.y}Z`);
    }
  }
  return {
    land: { cold: out.cold.join(""), cool: out.cool.join(""), warm: out.warm.join("") },
    high: out.high.join(""),
    water: out.water.join(""),
  };
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

/** The rivers as the eye sees them: each a polyline, cut where it goes over
 *  the horizon -- a river that dips behind the sphere is two runs. */
export function riverRuns(terrain: Terrain, eye: Eye, radius: number): Point[][] {
  const runs: Point[][] = [];
  for (const river of terrain.rivers) {
    let run: Point[] = [];
    for (const [lat, lon] of river) {
      const seen = project(eye, radius, { lat, lon });
      if (seen.front) run.push({ x: seen.x, y: seen.y });
      else if (run.length) {
        if (run.length >= 2) runs.push(run);
        run = [];
      }
    }
    if (run.length >= 2) runs.push(run);
  }
  return runs;
}
