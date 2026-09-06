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
 * a planet as a marker of the glyph's size, the surface the true sphere. So
 * the hand-over is a cut between two disks of one size (`openScale`), and
 * the **approach** (wave 5) is what happens below it: the descent by scale
 * (`descentOf`) tilts the eye from over the pole down to where one stands
 * (`tilted`). Pure numbers and functions, so a test can zoom.
 */

import { LAST_LAT, UNITS_PER_METRE, type Eye } from "./globe";
import { H, SPHERE_R, W, type Point } from "./model";

export type Band = "sky" | "surface" | "inside";

/** How far in and out the hand may zoom in a band. */
export type Bounds = { nearest: number; furthest: number };
export const SKY_BOUNDS: Bounds = { nearest: 4, furthest: 0.4 };
export const INSIDE_BOUNDS: Bounds = { nearest: 4, furthest: 0.4 };
/** How near the surface may be zoomed: the city's own scale times this. */
export const SURFACE_NEAREST = 4;
/** The scale a city's streets are drawn at: the flat map's own. */
export const STREET_SCALE = 1;
/** The relief's cell, degrees: the terrain grid is two degrees. */
export const CELL_DEG = 2;
/** Below this many drawn cells across the frame the ground is one flat
 *  colour: nearer than that the frame lies inside a cell. */
export const CELLS_ACROSS = 1.5;
/** From this drawn cell and finer the ground is read off the tiles of the
 *  local relief (D-323): a sixteenth of a grid cell, the frame under some
 *  thirty kilometres on a small world. */
export const TILE_UNIT = 1 / 16;
/** The finest reading of the ground, a thirty-second of a grid cell: one
 *  halving under `TILE_UNIT`, so the tiles' features bend. */
export const FINEST_UNIT = 1 / 32;
/** The drawn cell is halved while the frame holds fewer than this many. */
const HALVE_BELOW = 24;
const RAD = Math.PI / 180;

/**
 * How the ground is drawn for a frame at `scale` over a planet of `radius`:
 * the drawn cell in cells of the grid -- one while the frame holds many,
 * halved as it holds fewer, down to `FINEST_UNIT` -- and whether it is
 * drawn at all. From the radius, not from a scale: a scale that meant
 * "several cells across" on a planet of Terra's old size meant "more than
 * the whole disk" on one a twentieth of it (D-322), and the ground went
 * flat long before the coast could be seen. Powers of two, so the fact
 * flips a few times on the way down and not at every frame.
 */
export function groundOf(scale: number, radius: number | null): { unit: number; shown: boolean } {
  if (!radius || !(scale > 0)) return { unit: 1, shown: false };
  const span = W / scale;
  const cell = radius * CELL_DEG * RAD;
  let unit = 1;
  while (unit > FINEST_UNIT && span < HALVE_BELOW * cell * unit) unit /= 2;
  return { unit, shown: span > CELLS_ACROSS * cell * unit };
}

/**
 * How far from the eye the ground is laid at this drawn cell, in map units:
 * half the widest frame the cell serves, so that the ground laid at one
 * frame still covers every frame up to the next halving. Not the frame's
 * own width -- the ground is redrawn when a fact flips, not at every notch
 * of the zoom, and a window cut to the frame of the last flip would leave
 * the land short of the edge on the way out. A whole cell has no limit:
 * the frame then holds the disk, or most of it.
 */
export function groundReach(unit: number, radius: number | null): number | undefined {
  if (!radius || unit >= 1) return undefined;
  return HALVE_BELOW * radius * CELL_DEG * RAD * unit;
}
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

/** How far out the map tab zooms, as a share of the scale the globe fills
 *  the frame at: the disk a little over half the frame's height, and no
 *  farther -- the map has no sky (owner, 2026-09-06). */
export const MAP_FURTHEST = 0.5;

/**
 * The surface's bounds on the map tab: the planet is the whole of it. The
 * sky is the ship's console's alone, for finding one's way between the
 * worlds; from the ground the map stops at the disk, so that the stitch
 * between two drawings of one planet is never seen. Without a radius the
 * scene is flat, and it keeps the old floor.
 */
export function mapBounds(globe: number, radius: number | null): Bounds {
  if (!radius) return { nearest: SURFACE_NEAREST, furthest: UNBOOKED_FLOOR };
  return { nearest: SURFACE_NEAREST, furthest: globe * MAP_FURTHEST };
}

/**
 * The approach (D-319, wave 5, plan §2 "Камера"): between the floor of the
 * surface and the scale the globe fills the frame at, the descent -- 1 at
 * the floor, 0 at the globe and nearer, by the log of the scale, so that
 * every octave of the zoom tilts as much. Told in steps, because it is a
 * fact of the frame and React draws only when a fact flips.
 */
export const DESCENT_STEPS = 24;
export function descentOf(scale: number, floor: number, globe: number): number {
  if (!(globe > floor) || scale >= globe) return 0;
  if (scale <= floor) return 1;
  const share = Math.log(globe / scale) / Math.log(globe / floor);
  return Math.round(share * DESCENT_STEPS) / DESCENT_STEPS;
}

/**
 * The eye as the descent shows it: high up, from over the pole -- the sky
 * looks at the ecliptic from its north, and the planet's axis stands
 * perpendicular to it -- and, coming down, tilting to where the eye stands,
 * north up all the way. Smooth at both ends, so the tilt neither jerks off
 * the marker nor lands with a bump.
 */
export function tilted(eye: Eye, descent: number): Eye {
  if (descent <= 0) return eye;
  const t = Math.min(1, descent);
  const ease = t * t * (3 - 2 * t);
  return { lat: eye.lat + (LAST_LAT - eye.lat) * ease, lon: eye.lon };
}

/** The scale the surface opens at from the sky: where the true disk is the
 *  size of the marker at the sky's ceiling, so that the one becomes the
 *  other -- but never on the floor itself, or it would fall straight back:
 *  a planet whose disk would be the marker's size only below the floor
 *  (the larger ones, at the vault's approach height) opens a notch above
 *  it, the disk a little larger than the marker. */
export const ABOVE_FLOOR = 1.2;
export function openScale(radius: number | null, floor: number): number {
  if (!radius) return STREET_SCALE;
  return Math.max(floor * ABOVE_FLOOR, (SPHERE_R * SKY_BOUNDS.nearest) / radius);
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
