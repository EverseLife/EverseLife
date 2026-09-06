// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Where a city ends: its outline (D-323 addendum, owner 2026-09-06).
 *
 * A city's land is the land of its nodes joined -- each node the disc of
 * its area -- and the outline is drawn as a **contour**, not a fill and not
 * a circle. Between nodes that stand apart the land is bridged, so that a
 * city grown as a spread of nodes reads as one blot with no holes in it
 * and no corner: the blot has the shape the city grew into.
 *
 * The blot is a field over the city's own flat plane -- metres about its
 * middle -- summing every node's disc as a metaball, `(r / d)^4`; where the
 * field is one lies the edge. The fourth power, not the square: it falls
 * off fast, so the edge lies close to the discs' own -- the land of the
 * nodes, not a swell round it -- and two neighbours still join. The field is rastered, its contour traced
 * (marching squares), the trace smoothed (Chaikin) and given back in
 * degrees, so the globe projects it like any way. Pure arithmetic, once
 * per map.
 */

import type { MapNode } from "../../api";
import type { Geo } from "./globe";

const RAD = Math.PI / 180;
/** A node's land when the wire says nothing: the smallest node's (D-321). */
const FALLBACK_AREA_M2 = 60;
/**
 * The least a node's land reaches, as a share of the city's typical spacing
 * between nodes: two discs of this radius at that spacing just merge in
 * the field (they do at 0.42 of the spacing; half of it leaves room for the raster), so the gaps of one spread
 * close and an outlying field stays its own. A node whose own land reaches
 * farther keeps its own reach: in a city whose nodes stand metres apart
 * the land itself does the joining, and the outline hugs the nodes
 * (owner, 2026-09-06) instead of standing a bridge's width off them.
 */
const REACH_SHARE = 0.5;
/** The least a lone node's land reaches, metres. */
const LONE_REACH_M = 6;
/** The raster: cells across the city's spacing, and the cap on cells a side. */
const CELLS_PER_STEP = 8;
const MAX_CELLS = 160;
/** How many times the traced edge is rounded. */
const SMOOTHING = 2;

type Disc = { x: number; y: number; r: number };

/** The outlines of the cities among these nodes, by the city's key: each a
 *  list of closed loops of lat/lon. A city with no placed member has none. */
export function cityOutlines(nodes: readonly MapNode[], radiusM: number): Map<string, Geo[][]> {
  const members = new Map<string, MapNode[]>();
  for (const node of nodes) {
    if (!node.parent || !node.place || !("lat" in node.place)) continue;
    members.set(node.parent, [...(members.get(node.parent) ?? []), node]);
  }
  const out = new Map<string, Geo[][]>();
  for (const [city, own] of members) {
    //: A city is a node others hang under: a planet's node with no parent
    //: among the nodes given is the sphere, not a city.
    if (!nodes.some((node) => node.key === city && node.layer !== "space")) continue;
    const loops = outlineOf(own, radiusM);
    if (loops.length) out.set(city, loops);
  }
  return out;
}

/** One city's outline, loops of lat/lon. */
export function outlineOf(members: readonly MapNode[], radiusM: number): Geo[][] {
  const places = members
    .map((node) => node.place)
    .filter((p): p is { lat: number; lon: number } => Boolean(p && "lat" in p));
  if (!places.length) return [];
  const lat0 = places.reduce((s, p) => s + p.lat, 0) / places.length;
  const lon0 = places.reduce((s, p) => s + p.lon, 0) / places.length;
  const stretch = Math.max(1e-6, Math.cos(lat0 * RAD));
  const perDeg = radiusM * RAD;
  const toLocal = (p: { lat: number; lon: number }) => ({
    x: (p.lon - lon0) * stretch * perDeg,
    y: (p.lat - lat0) * perDeg,
  });
  const toGeo = (q: { x: number; y: number }): Geo => ({
    lat: lat0 + q.y / perDeg,
    lon: lon0 + q.x / (stretch * perDeg),
  });
  const centres = places.map(toLocal);
  const spacing = typicalSpacing(centres);
  const least = centres.length > 1 ? spacing * REACH_SHARE : LONE_REACH_M;
  const discs: Disc[] = members.map((node, i) => ({
    ...centres[i],
    r: Math.max(least, Math.sqrt(Math.max(1, node.area ?? FALLBACK_AREA_M2) / Math.PI)),
  }));
  return traceField(discs, spacing).map((loop) => loop.map(toGeo));
}

/** The median distance from a node to its nearest neighbour. */
function typicalSpacing(points: readonly { x: number; y: number }[]): number {
  if (points.length < 2) return LONE_REACH_M;
  const nearest = points.map((p, i) => {
    let best = Infinity;
    points.forEach((q, j) => {
      if (i !== j) best = Math.min(best, Math.hypot(p.x - q.x, p.y - q.y));
    });
    return best;
  });
  nearest.sort((a, b) => a - b);
  return Math.max(1, nearest[Math.floor(nearest.length / 2)]);
}

/** The metaball field: one on a disc's own edge alone, more where discs meet. */
function fieldAt(discs: readonly Disc[], x: number, y: number): number {
  let sum = 0;
  for (const d of discs) {
    const dd = (x - d.x) ** 2 + (y - d.y) ** 2;
    sum += dd > 0 ? (d.r * d.r * d.r * d.r) / (dd * dd) : Infinity;
  }
  return sum;
}

/** The contour of the field at one, rastered and traced: closed loops. */
function traceField(discs: readonly Disc[], spacing: number): { x: number; y: number }[][] {
  //: The blot cannot reach past twice a disc's radius from its centre: the
  //: raster covers that and a cell more.
  const reach = Math.max(...discs.map((d) => d.r * 2));
  const x0 = Math.min(...discs.map((d) => d.x)) - reach;
  const x1 = Math.max(...discs.map((d) => d.x)) + reach;
  const y0 = Math.min(...discs.map((d) => d.y)) - reach;
  const y1 = Math.max(...discs.map((d) => d.y)) + reach;
  let cell = spacing / CELLS_PER_STEP;
  cell = Math.max(cell, (x1 - x0) / MAX_CELLS, (y1 - y0) / MAX_CELLS);
  const nx = Math.ceil((x1 - x0) / cell) + 1;
  const ny = Math.ceil((y1 - y0) / cell) + 1;
  const values = new Float64Array(nx * ny);
  for (let j = 0; j < ny; j++) {
    for (let i = 0; i < nx; i++) values[j * nx + i] = fieldAt(discs, x0 + i * cell, y0 + j * cell);
  }
  const segments = marchingSquares(values, nx, ny, 1, (i, j) => ({ x: x0 + i * cell, y: y0 + j * cell }));
  return outerLoops(joinLoops(segments), cell).map((loop) => smooth(loop, SMOOTHING));
}

/** The area a loop encloses. */
function areaOf(loop: readonly { x: number; y: number }[]): number {
  let sum = 0;
  for (let i = 0; i < loop.length; i++) {
    const a = loop[i];
    const b = loop[(i + 1) % loop.length];
    sum += a.x * b.y - b.x * a.y;
  }
  return Math.abs(sum) / 2;
}

/** Whether a point lies within a loop (even-odd). */
function within(loop: readonly { x: number; y: number }[], p: { x: number; y: number }): boolean {
  let hit = false;
  for (let i = 0, j = loop.length - 1; i < loop.length; j = i++) {
    const a = loop[i];
    const b = loop[j];
    if (a.y > p.y !== b.y > p.y && p.x < ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x) hit = !hit;
  }
  return hit;
}

/**
 * The outer edges alone: a hole inside the blot is a loop lying within a
 * larger one, and the city has no holes (owner, 2026-09-06); a speck
 * smaller than a raster cell is the field grazing the level, not land.
 * By containment, not by winding: the trace joins segments in whichever
 * direction it finds them first.
 */
function outerLoops(
  loops: { x: number; y: number }[][],
  cell: number,
): { x: number; y: number }[][] {
  const kept: { x: number; y: number }[][] = [];
  for (const loop of [...loops].sort((a, b) => areaOf(b) - areaOf(a))) {
    if (areaOf(loop) <= cell * cell) continue;
    if (kept.some((outer) => within(outer, loop[0]))) continue;
    kept.push(loop);
  }
  return kept;
}

type Seg = [{ x: number; y: number }, { x: number; y: number }];

/** Marching squares over a raster: the segments of the level line. */
function marchingSquares(
  values: Float64Array,
  nx: number,
  ny: number,
  level: number,
  at: (i: number, j: number) => { x: number; y: number },
): Seg[] {
  const out: Seg[] = [];
  const cross = (a: { x: number; y: number }, va: number, b: { x: number; y: number }, vb: number) => {
    const t = (level - va) / (vb - va);
    return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
  };
  for (let j = 0; j + 1 < ny; j++) {
    for (let i = 0; i + 1 < nx; i++) {
      const v = [values[j * nx + i], values[j * nx + i + 1], values[(j + 1) * nx + i + 1], values[(j + 1) * nx + i]];
      const p = [at(i, j), at(i + 1, j), at(i + 1, j + 1), at(i, j + 1)];
      const inside = v.map((value) => value >= level);
      const edges: { x: number; y: number }[] = [];
      for (let k = 0; k < 4; k++) {
        const l = (k + 1) % 4;
        if (inside[k] !== inside[l]) edges.push(cross(p[k], v[k], p[l], v[l]));
      }
      if (edges.length === 2) out.push([edges[0], edges[1]]);
      else if (edges.length === 4) {
        //: A saddle: split by the middle's own value.
        const mid = (v[0] + v[1] + v[2] + v[3]) / 4 >= level;
        if (mid === inside[0]) out.push([edges[0], edges[3]], [edges[1], edges[2]]);
        else out.push([edges[0], edges[1]], [edges[2], edges[3]]);
      }
    }
  }
  return out;
}

/** The segments joined end to end into closed loops. */
function joinLoops(segments: Seg[]): { x: number; y: number }[][] {
  const key = (p: { x: number; y: number }) => `${p.x.toFixed(4)},${p.y.toFixed(4)}`;
  const byStart = new Map<string, Seg[]>();
  for (const seg of segments) {
    for (const [a, b] of [seg, [seg[1], seg[0]] as Seg]) {
      byStart.set(key(a), [...(byStart.get(key(a)) ?? []), [a, b]]);
    }
  }
  const used = new Set<Seg>();
  const loops: { x: number; y: number }[][] = [];
  for (const seg of segments) {
    if (used.has(seg)) continue;
    const loop = [seg[0], seg[1]];
    used.add(seg);
    let guard = segments.length * 2;
    while (guard-- > 0) {
      const tail = loop[loop.length - 1];
      const next = (byStart.get(key(tail)) ?? []).find(([, b]) => {
        const original = segments.find(
          (s) => !used.has(s) && ((key(s[0]) === key(tail) && key(s[1]) === key(b)) || (key(s[1]) === key(tail) && key(s[0]) === key(b))),
        );
        if (original) used.add(original);
        return Boolean(original);
      });
      if (!next) break;
      if (key(next[1]) === key(loop[0])) break;
      loop.push(next[1]);
    }
    if (loop.length >= 3) loops.push(loop);
  }
  return loops;
}

/** Chaikin's corner cutting: each pass replaces every corner by two points
 *  a quarter of the way along its sides, so the edge rounds off. */
function smooth(loop: { x: number; y: number }[], passes: number): { x: number; y: number }[] {
  let out = loop;
  for (let n = 0; n < passes; n++) {
    const next: { x: number; y: number }[] = [];
    for (let i = 0; i < out.length; i++) {
      const a = out[i];
      const b = out[(i + 1) % out.length];
      next.push({ x: a.x * 0.75 + b.x * 0.25, y: a.y * 0.75 + b.y * 0.25 });
      next.push({ x: a.x * 0.25 + b.x * 0.75, y: a.y * 0.25 + b.y * 0.75 });
    }
    out = next;
  }
  return out;
}
