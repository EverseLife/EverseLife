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
 *   node's centre than that node's radius plus a find's least own;
 * - **the way** -- the straight line from here to the point crosses no way
 *   already laid.
 *
 * All in the frame's own plane, map units: the reach is a hundred metres
 * at most, and at that size the globe is flat. The water on the way is not
 * drawn -- the server refuses that one; the shore is.
 */

import { UNITS_PER_METRE, type Geo } from "./globe";
import type { Point } from "./model";

/** A find's least land (D-321: `explore.node_area` from 60 m2): its radius. */
const FIND_LEAST_R_M = Math.sqrt(60 / Math.PI);

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

/** The field about the node one stands in, from the map's rows. */
export function fieldOf(
  origin: Point,
  reach: { min: number; max: number },
  nodes: readonly { key: string; at: Point; area?: number | null }[],
  ways: readonly Way[],
  here: string,
): Field {
  return {
    origin,
    near: reach.min * UNITS_PER_METRE,
    far: reach.max * UNITS_PER_METRE,
    blocks: nodes
      .filter((node) => node.key !== here)
      .map((node) => ({
        at: node.at,
        r: (Math.sqrt(Math.max(1, node.area ?? 60) / Math.PI) + FIND_LEAST_R_M) * UNITS_PER_METRE,
      })),
    ways: [...ways],
  };
}

/** Whether segments a-b and c-d cross (proper crossing, ends excluded). */
export function segmentsCross(a: Point, b: Point, c: Point, d: Point): boolean {
  const side = (p: Point, q: Point, r: Point) => (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
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
export type Placed = { key: string; at: Point; area?: number | null; place?: Geo };
