// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Where a city ends: its outline (D-323 addendum, owner 2026-09-06).
 *
 * A city's land is the land of its nodes joined -- each node the disc of
 * its area -- and the outline is drawn as a **contour**, not a fill and not
 * a circle. It is **one** blot, always: a city is one place, and a mine an
 * hour's walk out is a part of it, not a second city with a circle of its
 * own. What the discs do not join, an isthmus does.
 *
 * The blot is a field over the city's own flat plane -- metres about its
 * middle -- summing every node's disc as a metaball, `(r / d)^4`; where the
 * field is one lies the edge. The fourth power, not the square: it falls
 * off fast, so the edge lies close to the discs' own -- the land of the
 * nodes, not a swell round it -- and two neighbours still join.
 *
 * The isthmuses are laid along the **shortest tree that joins every node**
 * (Prim): that is the least a city can be bridged by and still be one, and
 * it is the same tree whatever order the nodes arrive in. Each is a capsule
 * in the same field, so a bridge inside a dense city's own land shows
 * nothing and a bridge across a kilometre of taiga shows as a neck. Widening
 * the discs instead would have done it too, and would have blown the land of
 * every node of a spread-out city up to half its farthest gap.
 *
 * The field is rastered, its contour traced (marching squares), the trace
 * smoothed (Chaikin) and given back in degrees, so the globe projects it
 * like any way. Pure arithmetic, once per map.
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
/**
 * How wide an isthmus is, in cells of the raster -- never less, whatever the
 * nodes' own land says. A neck thinner than the grid falls between two
 * samples: the field is over one along it and nowhere measured, so the trace
 * steps over the bridge and the far node drops off the blot again, which is
 * the whole thing this is for. At one and a half the band of "inside" is
 * three cells across, and the nearest sample to the axis is never farther
 * than 0.71 of one, so no slope of a bridge can slip between two columns.
 *
 * The cost of that floor: where the grid is coarse -- a city spread over
 * kilometres, where the cell is the span over `MAX_CELLS` -- the neck is as
 * wide as the grid says rather than as wide as the nodes' own land. So
 * `MAX_CELLS` is not only how long this takes: it is also the shape of what
 * the player sees, and it is not to be turned as a performance knob alone.
 */
const BRIDGE_CELLS = 1.5;
/** What the swell of the isthmuses takes off the raster, in cells: the
 *  bridge's own width at both edges of the picture. */
const MARGIN_CELLS = 4 * BRIDGE_CELLS;
/** How many times the traced edge is rounded. */
const SMOOTHING = 2;

type Disc = { x: number; y: number; r: number };
/** An isthmus: a capsule of the same field, laid between two nodes. */
type Bridge = { ax: number; ay: number; bx: number; by: number };

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
  //: Members and places are picked in one pass, not two: a member with no
  //: place used to shift every disc after it onto its neighbour's centre,
  //: and the last of them onto nothing at all -- a disc at `undefined`, a
  //: field of `Infinity` everywhere, and the city silently without an
  //: outline. `cityOutlines` never hands one over, but this is exported.
  const placed = members.filter(
    (node): node is MapNode & { place: { lat: number; lon: number } } =>
      Boolean(node.place && "lat" in node.place),
  );
  const places = placed.map((node) => node.place);
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
  const discs: Disc[] = placed.map((node, i) => ({
    ...centres[i],
    r: Math.max(least, Math.sqrt(Math.max(1, node.area ?? FALLBACK_AREA_M2) / Math.PI)),
  }));
  return traceField(discs, spacing, least).map((loop) => loop.map(toGeo));
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

/** How far a point lies off a segment, squared. */
function offSegment(bridge: Bridge, x: number, y: number): number {
  const vx = bridge.bx - bridge.ax;
  const vy = bridge.by - bridge.ay;
  const run = vx * vx + vy * vy;
  //: A bridge between two nodes standing on one spot is a point.
  const along = run > 0 ? Math.min(1, Math.max(0, ((x - bridge.ax) * vx + (y - bridge.ay) * vy) / run)) : 0;
  return (x - bridge.ax - along * vx) ** 2 + (y - bridge.ay - along * vy) ** 2;
}

/** The metaball field: one on a disc's own edge alone, more where discs meet.
 *  An isthmus counts the same way, measured off its axis rather than off a
 *  centre -- a capsule where a node is a disc. */
function fieldAt(
  discs: readonly Disc[],
  bridges: readonly Bridge[],
  bridgeR: number,
  x: number,
  y: number,
): number {
  let sum = 0;
  for (const d of discs) {
    const dd = (x - d.x) ** 2 + (y - d.y) ** 2;
    sum += dd > 0 ? (d.r * d.r * d.r * d.r) / (dd * dd) : Infinity;
  }
  const wide = bridgeR ** 4;
  for (const bridge of bridges) {
    const dd = offSegment(bridge, x, y);
    sum += dd > 0 ? wide / (dd * dd) : Infinity;
  }
  return sum;
}

/**
 * The shortest tree that joins every node (Prim), as isthmuses.
 *
 * Every node ends up on it, so the blot is one however the city is spread;
 * and it is the *shortest* such tree, so nothing is bridged that a nearer
 * pair has already joined. Order does not enter it: the tree is a property
 * of the points.
 */
function spanOf(discs: readonly Disc[]): Bridge[] {
  if (discs.length < 2) return [];
  //: Prim over the points, each carrying the nearest one already on the tree
  //: and how far that is -- so every node is looked at once per step rather
  //: than once per pair, and a city of hundreds costs its square, not its cube.
  const onTree = discs.map(() => false);
  const run = discs.map(() => Infinity);
  const from = discs.map(() => 0);
  const bridges: Bridge[] = [];
  onTree[0] = true;
  for (let i = 1; i < discs.length; i++) {
    run[i] = (discs[0].x - discs[i].x) ** 2 + (discs[0].y - discs[i].y) ** 2;
  }
  for (let step = 1; step < discs.length; step++) {
    let next = -1;
    for (let i = 0; i < discs.length; i++) {
      if (!onTree[i] && (next < 0 || run[i] < run[next])) next = i;
    }
    const a = discs[from[next]];
    const b = discs[next];
    bridges.push({ ax: a.x, ay: a.y, bx: b.x, by: b.y });
    onTree[next] = true;
    for (let i = 0; i < discs.length; i++) {
      if (onTree[i]) continue;
      const far = (b.x - discs[i].x) ** 2 + (b.y - discs[i].y) ** 2;
      if (far < run[i]) {
        run[i] = far;
        from[i] = next;
      }
    }
  }
  return bridges;
}

/** The contour of the field at one, rastered and traced: closed loops. */
function traceField(
  discs: readonly Disc[],
  spacing: number,
  least: number,
): { x: number; y: number }[][] {
  //: The blot cannot reach past twice a disc's radius from its centre: the
  //: raster covers that and a cell more.
  const reach = Math.max(...discs.map((d) => d.r * 2));
  const spanX = Math.max(...discs.map((d) => d.x)) - Math.min(...discs.map((d) => d.x));
  const spanY = Math.max(...discs.map((d) => d.y)) - Math.min(...discs.map((d) => d.y));
  //: The margin the blot may need round the nodes is either the discs' own
  //: reach or an isthmus's swell, and the second is measured in cells -- so
  //: the cell that keeps the raster within `MAX_CELLS` a side is the one
  //: that leaves room for that swell as well. `MARGIN_CELLS` is what the
  //: bridges take: `BRIDGE_CELLS` each side of the axis, doubled for the two
  //: sides of the picture.
  const cell = Math.max(
    spacing / CELLS_PER_STEP,
    (spanX + 2 * reach) / MAX_CELLS,
    (spanY + 2 * reach) / MAX_CELLS,
    spanX / (MAX_CELLS - MARGIN_CELLS),
    spanY / (MAX_CELLS - MARGIN_CELLS),
  );
  const bridges = spanOf(discs);
  //: As wide as the smallest node's land, and never narrower than the grid
  //: can see (`BRIDGE_CELLS`). The margin grows with it: an isthmus swells a
  //: little past its own axis, as a disc does past its centre.
  const bridgeR = bridges.length ? Math.max(least, cell * BRIDGE_CELLS) : 0;
  const edge = Math.max(reach, bridgeR * 2);
  const x0 = Math.min(...discs.map((d) => d.x)) - edge;
  const x1 = x0 + spanX + 2 * edge;
  const y0 = Math.min(...discs.map((d) => d.y)) - edge;
  const y1 = y0 + spanY + 2 * edge;
  const nx = Math.ceil((x1 - x0) / cell) + 1;
  const ny = Math.ceil((y1 - y0) / cell) + 1;
  const values = new Float64Array(nx * ny);
  for (let j = 0; j < ny; j++) {
    for (let i = 0; i < nx; i++) {
      values[j * nx + i] = fieldAt(discs, bridges, bridgeR, x0 + i * cell, y0 + j * cell);
    }
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

/**
 * The segments joined end to end into closed loops.
 *
 * Every segment is written down under both of its ends once, and the walk
 * then asks a point for what meets it instead of searching the contour for
 * it. It used to search: a scan of every segment per step, which is the
 * contour's length squared, and each scan spelling out the coordinates
 * again. A city that is now one long bridged contour rather than a handful
 * of small circles made that cost visible -- a fifth of a second, on the
 * main thread, once per city of every planet on every step of a walk.
 */
function joinLoops(segments: Seg[]): { x: number; y: number }[][] {
  const spell = (p: { x: number; y: number }) => `${p.x.toFixed(4)},${p.y.toFixed(4)}`;
  //: Both ends of every segment, spelled once: `toFixed` is the dear part.
  const ends: [string, string][] = segments.map((seg) => [spell(seg[0]), spell(seg[1])]);
  const meeting = new Map<string, number[]>();
  const met = (at: string) => {
    const held = meeting.get(at);
    if (held) return held;
    const made: number[] = [];
    meeting.set(at, made);
    return made;
  };
  ends.forEach(([a, b], i) => {
    met(a).push(i);
    met(b).push(i);
  });

  const used = new Array<boolean>(segments.length).fill(false);
  const loops: { x: number; y: number }[][] = [];
  for (let start = 0; start < segments.length; start++) {
    if (used[start]) continue;
    used[start] = true;
    const loop = [segments[start][0], segments[start][1]];
    const first = ends[start][0];
    let tail = ends[start][1];
    for (;;) {
      const next = (meeting.get(tail) ?? []).find((i) => !used[i]);
      if (next === undefined) break;
      used[next] = true;
      //: The far end of what was found, whichever way round it was laid.
      const far = ends[next][0] === tail ? 1 : 0;
      tail = ends[next][far];
      //: Back where the loop began: it is closed, and the first point is
      //: not written twice.
      if (tail === first) break;
      loop.push(segments[next][far]);
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
