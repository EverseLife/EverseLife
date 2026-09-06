// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The bands of scale (D-319, wave 4): what a height shows, and where one
 * band hands over to the next.
 *
 * The layers of the old map were tabs; the bands are heights. Far out the
 * map is the **sky** -- the system in its own units, planets as markers
 * (D-320). Close in it is the **surface** -- the globe of one planet, and
 * on it a city is a point until the eye comes near enough to see its
 * streets, when it opens into its nodes with its border drawn round them.
 * The **inside** -- floors, the rooms of a hull -- is not a height at all:
 * it has no north, and it is a window one opens from a node (plan §2, item 4).
 *
 * The two coordinate systems do not meet (plan §2, item 2): the sky draws
 * bodies eighty-six times their size, the surface the true sphere. So the
 * hand-over is a cut at a threshold, and the choreography that would join
 * them -- the tilt to the equator, the marker shrinking to the sphere -- is
 * the stitch of wave 5. Pure numbers and functions, so a test can zoom.
 */

import { UNITS_PER_METRE } from "./globe";
import { H, type Point } from "./model";

export type Band = "sky" | "surface" | "inside";

/** How far in and out the hand may zoom in a band. */
export type Bounds = { nearest: number; furthest: number };
export const SKY_BOUNDS: Bounds = { nearest: 4, furthest: 0.4 };
export const INSIDE_BOUNDS: Bounds = { nearest: 4, furthest: 0.4 };
/** How near the surface may be zoomed: the city's own scale times this. */
export const SURFACE_NEAREST = 4;
/** The scale a city's streets are drawn at: the flat map's own. */
export const STREET_SCALE = 1;
/** At this scale and farther the frame holds several cells of the relief,
 *  and the ground is drawn cell by cell; nearer, one cell fills it. */
export const GROUND_SCALE = 5e-4;
/** How much of the frame's height the globe takes when the sky opens a
 *  surface: a little more than all of it, so the marker becomes the disk. */
export const GLOBE_FILL = 1.2;

/** The scale the surface opens at from the sky, from the planet's radius in
 *  map units: the disk `GLOBE_FILL` of the frame high. Without a radius the
 *  scene is flat, and it opens at the streets' scale as it always did. */
export function globeScale(radius: number | null): number {
  if (!radius) return STREET_SCALE;
  return (H * GLOBE_FILL) / (2 * radius);
}

/**
 * The floor of the surface band from the vault's height (`map.approach_km`,
 * D-319): the camera has no height of its own yet -- it has a scale -- so
 * the height is read as what a right-angled view from it takes in, twice
 * the height across the frame. Below the floor, the sky. Until the book has
 * come there is no height to read, and the floor is set under any planet's
 * disk so that a surface opened from the sky does not at once fall back.
 */
export function surfaceFloor(approachKm: number): number {
  if (!(approachKm > 0)) return UNBOOKED_FLOOR;
  return H / (2 * approachKm * 1000 * UNITS_PER_METRE);
}
/** The floor before the book has come: under any planet's disk. */
const UNBOOKED_FLOOR = 1e-6;

export function surfaceBounds(approachKm: number): Bounds {
  return { nearest: SURFACE_NEAREST, furthest: surfaceFloor(approachKm) };
}

/** At this scale and nearer a city opens into its nodes; farther, it is a
 *  point with a name -- the printer's (plan §2). */
export const CITY_SCALE = 0.35;
/** In the sky, zoomed all the way in on a planet, the surface opens: the
 *  marker within this many map units of the frame's middle. */
export const OPEN_REACH = 60;
/** Room left round a city's nodes by its border. */
export const BORDER_MARGIN = 28;

export function boundsOf(band: Band, surface: Bounds): Bounds {
  if (band === "sky") return SKY_BOUNDS;
  if (band === "inside") return INSIDE_BOUNDS;
  return surface;
}

/** Whether cities are open at this scale. */
export function cityOpen(scale: number): boolean {
  return scale >= CITY_SCALE;
}

/** Whether the surface, zoomed out this far, hands over to the sky. */
export function leavesSurface(scale: number, floor: number): boolean {
  return scale <= floor;
}

/** Whether the sky, zoomed in this close, is asking to open a planet. */
export function reachesSurface(scale: number): boolean {
  return scale >= SKY_BOUNDS.nearest;
}

/** The planet under the middle of the frame, if one is close enough to open. */
export function planetUnder(
  middle: Point,
  spheres: readonly { key: string; planet: string; at: Point }[],
): { key: string; planet: string } | null {
  let best: { key: string; planet: string } | null = null;
  let nearest = OPEN_REACH;
  for (const sphere of spheres) {
    const away = Math.hypot(sphere.at.x - middle.x, sphere.at.y - middle.y);
    if (away <= nearest) {
      nearest = away;
      best = { key: sphere.key, planet: sphere.planet };
    }
  }
  return best;
}

/** The border of an open city: a circle round its nodes, with room to spare. */
export function ring(points: readonly Point[]): { cx: number; cy: number; r: number } | null {
  if (points.length === 0) return null;
  const cx = points.reduce((sum, p) => sum + p.x, 0) / points.length;
  const cy = points.reduce((sum, p) => sum + p.y, 0) / points.length;
  const reach = points.reduce((far, p) => Math.max(far, Math.hypot(p.x - cx, p.y - cy)), 0);
  return { cx, cy, r: reach + BORDER_MARGIN };
}
