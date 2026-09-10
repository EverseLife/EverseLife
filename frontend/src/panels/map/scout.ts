// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Where the scout may aim (D-321 item 4, owner 2026-09-06): the field drawn
 * green on the map while scouting is armed, and the test of a point under
 * the cursor. The server is the judge; this is the same judgement drawn
 * ahead, so the hand is not led to a refusal:
 *
 * - **reach** -- a ring about the node one stands in, from the biome's
 *   near to its far (`reach` on the node's row);
 * - **land** -- the point is land, not sea nor lake (the caller reads the
 *   ground; this module takes the reading);
 * - **room** -- the point is not on any node's land: farther from each
 *   node's centre than that node's radius plus the room a find needs. The
 *   node one stands in counts among them: the server measures the free
 *   radius to the **nearest** node of the surface and does not spare the
 *   origin (`aim.check`), so a wide node one stands in shuts the near half
 *   of the ring;
 * - **the way** -- the straight line from here to the point crosses no way
 *   already laid.
 *
 * All in the frame's own plane, map units: the reach is a hundred metres
 * at most, and at that size the globe is flat. The water on the way is not
 * drawn -- the server refuses that one; the shore is.
 *
 * ## The finger points, the lattice decides
 *
 * The point a finger lands on is not the point the server judges: it presses
 * the aim to the planet's lattice and reads the **cell's centre** (D-321
 * item 3, `map.lattice_m`). A tap may therefore move by up to half a cell's
 * diagonal, and until 2026-09-09 nothing here knew that: a tap well inside
 * the green came back "слишком далеко" because the cell it fell into was
 * outside the reach (owner, 2026-09-09). So the client presses the tap to the
 * lattice itself (`snapTo`) and judges **that** point -- what the panel
 * measures and the cross marks is now the very place that will open. Where
 * the cell a finger landed on is unlawful, the nearest lawful one round it is
 * taken instead (`nearestCell`): a hand pointing at green gets an answer, not
 * a shrug.
 */

import { arcDeg, UNITS_PER_METRE, type Geo } from "./globe";
import type { Point } from "./model";

/** Degrees to radians: an arc between two places is measured in degrees and
 *  spent against a radius in map units. */
const RAD = Math.PI / 180;

/** What the vault says a find needs and how far the lattice may move a tap:
 *  the two numbers the field is drawn a little smaller by. */
export type Rules = {
  /** Metres a find needs free **beyond** a node's own circle: its least
   *  radius over `explore.fill_share`, because the find takes that share of
   *  whatever radius is free (`aim.area_for`). */
  room: number;
  /** The lattice step as degrees of latitude, or null with no book: then the
   *  tap is judged where it landed, because there is no grid to press it to. */
  step: number | null;
  /** The same step in metres (`map.lattice_m`), for counting how many cells
   *  wide a reach is. Nought where the book is silent. */
  cell: number;
};

//: Until the book arrives: today's numbers, so the first frame is not drawn
//: to one rule and re-drawn to another.
const FALLBACK: Rules = {
  room: Math.sqrt(60 / Math.PI) / 0.8,
  step: null,
  cell: 0,
};

/**
 * The rules off the vault (D-065), for a planet of this radius.
 *
 * `radiusUnits` is the planet's drawn radius in map units -- the lattice is
 * metres of arc, and turning it into degrees needs the sphere it is laid on.
 */
export function rulesOf(
  book: { constants?: Record<string, unknown> | null } | null,
  radiusUnits: number | null,
): Rules {
  const said = book?.constants;
  const area = said?.["explore.node_area"] as { min?: unknown } | undefined;
  const least = Number(area?.min);
  const share = Number(said?.["explore.fill_share"]);
  const lattice = Number(said?.["map.lattice_m"]);
  const room =
    Number.isFinite(least) && least > 0 && Number.isFinite(share) && share > 0
      ? Math.sqrt(least / Math.PI) / share
      : FALLBACK.room;
  if (!Number.isFinite(lattice) || lattice <= 0) return { ...FALLBACK, room };
  const metres =
    radiusUnits && radiusUnits > 0 ? radiusUnits / UNITS_PER_METRE : null;
  return { room, step: metres ? lattice / metres / RAD : null, cell: lattice };
}

//: The floor the server keeps its own cosine at (`globe.COS_FLOOR`): without
//: it a pole would stretch one degree of latitude into infinitely many of
//: longitude, and the column of a cell would not be a number.
const COS_FLOOR = 1e-9;
//: Past this latitude the world does not lay places (`globe.LAST_LAT`).
const LAST_LAT = 89;

/** The cell a point falls into: row and column of the planet's lattice. */
export function cellOf(rules: Rules, at: Geo): [number, number] | null {
  const step = rules.step;
  if (!step) return null;
  const row = Math.round(at.lat / step);
  return [row, Math.round(at.lon / (step * stretchAt(row * step)))];
}

/** Where a cell stands: its centre, the place a find there would have. */
export function centreOf(rules: Rules, cell: [number, number]): Geo | null {
  const step = rules.step;
  if (!step) return null;
  const [row, col] = cell;
  return {
    lat: Math.max(-LAST_LAT, Math.min(LAST_LAT, row * step)),
    lon: wrapLon(col * step * stretchAt(row * step)),
  };
}

/** The place a tap opens: its point pressed to the lattice (D-321 item 3).
 *
 *  The same arithmetic the engine does (`explore._base.cell_of`/`point_of`),
 *  and it must stay the same: a cross drawn anywhere else marks a place that
 *  will not appear there. Given back unchanged where the book has not come.
 */
export function snapTo(rules: Rules, at: Geo): Geo {
  const cell = cellOf(rules, at);
  return (cell && centreOf(rules, cell)) ?? at;
}

/**
 * The lawful cell nearest the one a finger landed on, or nothing.
 *
 * A ring drawn in whole metres holds cells that are lawful and cells that are
 * not -- the lattice is coarse against a reach of tens of metres -- and a tap
 * on green must not be a shrug. So the cells around are tried outward, ring by
 * ring, and the first lawful one wins; `reach` is how many rings deep to look
 * before giving up, in cells, and the caller sets it to the width of the field
 * so that nothing lawful inside it is missed.
 *
 * Nothing at all is a lawful answer: a patch of the ring can hold no lawful
 * cell whatever, and then the tap must do nothing. Aiming at the tap itself
 * would be worse than a shrug -- the server judges the **cell**, so the run
 * would be refused a moment later with words the hand did not earn.
 *
 * `place` turns a cell's centre into the frame's own point, and `ok` is the
 * judgement -- both handed in, because neither the projection nor the ground
 * under a point belongs to this module.
 */
export function nearestCell(
  rules: Rules,
  at: Geo,
  place: (geo: Geo) => Point | null,
  ok: (point: Point) => boolean,
  reach = 3,
): { geo: Geo; at: Point } | null {
  const cell = cellOf(rules, at);
  if (!cell) return null;
  for (let ring = 0; ring <= reach; ring += 1) {
    let best: { geo: Geo; at: Point; away: number } | null = null;
    for (let dr = -ring; dr <= ring; dr += 1) {
      for (let dc = -ring; dc <= ring; dc += 1) {
        //: Only the rim of this ring: the inside was tried a round ago.
        if (Math.max(Math.abs(dr), Math.abs(dc)) !== ring) continue;
        const geo = centreOf(rules, [cell[0] + dr, cell[1] + dc]);
        const point = geo && place(geo);
        if (!geo || !point || !ok(point)) continue;
        const away = dr * dr + dc * dc;
        if (!best || away < best.away) best = { geo, at: point, away };
      }
    }
    if (best) return { geo: best.geo, at: best.at };
  }
  return null;
}

/** How many degrees of longitude one degree of latitude's metres make here.
 *  The engine's own `globe.lon_stretch`, and it must stay the same. */
function stretchAt(lat: number): number {
  return 1 / Math.max(Math.cos(lat * RAD), COS_FLOOR);
}

/** A longitude brought back into (-180, 180], as the engine keeps it. */
function wrapLon(lon: number): number {
  const turned = (((lon + 180) % 360) + 360) % 360;
  return turned - 180;
}

/** How far apart two places are, metres: the arc between them against the
 *  planet's radius. What the survey panel says before the run. */
export function metresBetween(from: Geo, to: Geo, radius: number): number {
  return (arcDeg(from, to) * RAD * radius) / UNITS_PER_METRE;
}

/** The two thresholds the relief is toned by, off the vault's `biome.zonal`
 *  (D-065, landscape plan wave 4): the warm edge of the tundra's row is the
 *  cold line, the warm edge of the taiga's the cool line -- the same rows
 *  that sort a node, so the globe's tones and the node's word agree. Null
 *  where the book has not come, or says something else: then the water is
 *  not judged at all and every point is taken as land. */
export function warmthOf(
  zonal: unknown,
): { cold: number; cool: number } | null {
  const rows = Object.values((zonal ?? {}) as Record<string, unknown>);
  //: A biome may hold several rectangles (a dry and a wet tundra, say):
  //: its line is the warmest edge among them.
  const edge = (biome: string): number =>
    rows.reduce<number>((warmest, one) => {
      const row = one as { biome?: unknown; temp?: unknown };
      const top = Array.isArray(row.temp) ? Number(row.temp[1]) : NaN;
      return row.biome === biome && Number.isFinite(top) ? Math.max(warmest, top) : warmest;
    }, -Infinity);
  const cold = edge("tundra");
  const cool = edge("taiga");
  if (Number.isFinite(cold) && Number.isFinite(cool)) return { cold, cool };
  //: The table came but names no tundra or taiga: the globe draws no land,
  //: and that must not look like a book still on its way.
  if (rows.length) console.warn("biome.zonal has no tundra or taiga row: no tone lines");
  return null;
}

export type Block = { at: Point; r: number };
export type Way = [Point, Point];

export type Field = {
  /** The node one stands in, projected. */
  origin: Point;
  /** The ring, map units. */
  near: number;
  far: number;
  /** Every node's land, projected, a find's own radius added. */
  blocks: Block[];
  /** Every way laid, projected. */
  ways: Way[];
};

/** The field about the node one stands in, from the map's rows and the vault. */
export function fieldOf(
  origin: Point,
  reach: { min: number; max: number },
  nodes: readonly { key: string; at: Point; area?: number | null }[],
  ways: readonly Way[],
  rules: Rules,
): Field {
  return {
    origin,
    near: reach.min * UNITS_PER_METRE,
    far: reach.max * UNITS_PER_METRE,
    //: Every node, the one underfoot included: the server measures the free
    //: radius to the **nearest** node of the surface and spares nobody
    //: (`aim.check`), so a wide node one stands in shuts the near half of the
    //: ring. Leaving it out drew green where the answer was "слишком тесно"
    //: (owner, 2026-09-09).
    blocks: nodes.map((node) => ({
      at: node.at,
      r:
        (Math.sqrt(Math.max(1, node.area ?? 60) / Math.PI) + rules.room) *
        UNITS_PER_METRE,
    })),
    ways: [...ways],
  };
}

/** Whether segments a-b and c-d cross (proper crossing, ends excluded). */
export function segmentsCross(a: Point, b: Point, c: Point, d: Point): boolean {
  const side = (p: Point, q: Point, r: Point) =>
    (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
  const s1 = side(a, b, c);
  const s2 = side(a, b, d);
  const s3 = side(c, d, a);
  const s4 = side(c, d, b);
  return s1 * s2 < 0 && s3 * s4 < 0;
}

/** Whether a point may be aimed at, the ground under it read as `land`. */
export function scoutable(field: Field, p: Point, land: boolean): boolean {
  const d = Math.hypot(p.x - field.origin.x, p.y - field.origin.y);
  if (d < field.near || d > field.far) return false;
  if (!land) return false;
  for (const block of field.blocks) {
    if (Math.hypot(p.x - block.at.x, p.y - block.at.y) < block.r) return false;
  }
  for (const [a, b] of field.ways) {
    if (segmentsCross(field.origin, p, a, b)) return false;
  }
  return true;
}

/** The ring as a path, even-odd: the far disc with the near one cut out. */
/**
 * The field mask's box: a square round the circle of reach, with room to spare.
 *
 * Giving it is not optional. A mask with no `x`/`y`/`width`/`height` takes
 * them as -10%..110% by default, and under `maskUnits="userSpaceOnUse"` those
 * percentages are of the **viewport**, not of the field: the box then rides
 * with the camera, the field is cut by its invisible edge on a zoom and, once
 * past it, vanishes whole (owner, 2026-09-08). The room to spare is a tenth:
 * the mask cuts only what is inside the ring, and the ring is never wider
 * than `far`.
 */
export function fieldBox(field: Field): { x: number; y: number; size: number } {
  const edge = field.far * 1.1;
  return { x: field.origin.x - edge, y: field.origin.y - edge, size: 2 * edge };
}

export function ringPath(field: Field): string {
  const disc = (r: number) =>
    `M${field.origin.x - r} ${field.origin.y}a${r} ${r} 0 1 0 ${2 * r} 0a${r} ${r} 0 1 0 ${-2 * r} 0Z`;
  return disc(field.far) + disc(field.near);
}

/**
 * The shadow a way throws from where one stands: the ground behind it as
 * seen from the origin, out past the reach -- a quadrilateral of the way's
 * ends and the same ends pushed along their rays. Cut from the field.
 */
export function wayShadow(field: Field, way: Way): string | null {
  const [a, b] = way;
  const push = (p: Point): Point | null => {
    const dx = p.x - field.origin.x;
    const dy = p.y - field.origin.y;
    const d = Math.hypot(dx, dy);
    if (d === 0) return null;
    const k = (field.far * 2) / d;
    return { x: field.origin.x + dx * k, y: field.origin.y + dy * k };
  };
  const pa = push(a);
  const pb = push(b);
  if (!pa || !pb) return null;
  //: A way wholly beyond the reach shadows nothing the field holds.
  const nearest = Math.min(
    Math.hypot(a.x - field.origin.x, a.y - field.origin.y),
    Math.hypot(b.x - field.origin.x, b.y - field.origin.y),
  );
  if (nearest > field.far * 2) return null;
  return `M${a.x} ${a.y}L${b.x} ${b.y}L${pb.x} ${pb.y}L${pa.x} ${pa.y}Z`;
}

/** The point in the plane for a place, when the caller has it in degrees. */
export type Placed = {
  key: string;
  at: Point;
  area?: number | null;
  place?: Geo;
};
