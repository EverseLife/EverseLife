// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The globe: degrees onto the screen, and the hand turning it (D-319, wave 3).
 *
 * A surface node stands on its planet's sphere in degrees, and the map draws
 * the sphere as it is seen from above one point of it -- the **eye**: an
 * orthographic projection with north up, the eye's point at the origin of
 * the frame. Coordinates near the frame are small whatever the planet's
 * size, because the origin moves with the eye rather than staying at the
 * sphere's centre; that is what keeps a viewBox honest over ten orders of
 * magnitude (plan §2).
 *
 * What is behind the sphere is not drawn: `project` says so, and an edge is
 * a great-circle arc cut where it goes over the horizon. The hand turns the
 * globe by two angles and nothing else -- north stays up (D-237): "the mine
 * is north of the gate" stays true however one drags.
 *
 * Pure arithmetic, apart from React, so a test can turn the globe without a
 * pointer or a DOM.
 */

import type { Point } from "./model";

/** Where the eye stands over the sphere, in degrees. */
export type Eye = { lat: number; lon: number };
/** A point of the sphere, in degrees. */
export type Geo = { lat: number; lon: number };

/** Map units a metre of the surface makes: the flat map's own density at a
 *  city step, so a plot keeps the size it had before the globe. */
export const UNITS_PER_METRE = 5;
/** The eye stays this side of the pole so that "north up" keeps a meaning
 *  (plan §7, п. 2): at the pole every direction is south. */
export const LAST_LAT = 85;
/** How many pieces a great-circle arc is drawn in. */
export const ARC_STEPS = 16;
/** How far a stub into the fog is drawn, in metres of the surface: enough
 *  to show the way. The same length whatever the edge's, so the stub tells
 *  nothing of how far the way goes -- though not where it leads: two stubs
 *  towards one hidden node cross there. */
export const STUB_M = 25;

const RAD = Math.PI / 180;

/** The planet's radius in map units, from its radius in kilometres. */
export function radiusUnits(radiusKm: number): number {
  return radiusKm * 1000 * UNITS_PER_METRE;
}

/**
 * A full circle about the origin as a path, for the disk and its clip.
 *
 * Not a `<circle>`: the browser reads `r`, `cx` and `cy` as CSS lengths and
 * saturates them at 2^25 device pixels, and a planet's radius in map units
 * (`UNITS_PER_METRE` of a planet) passes that on any display scaled past
 * one -- the sea came out two thirds of the ground and the clip cut the
 * land with it. A path's coordinates are numbers, not lengths, and go
 * as far as the ground does.
 */
export function diskPath(radius: number): string {
  return `M${-radius} 0A${radius} ${radius} 0 1 0 ${radius} 0A${radius} ${radius} 0 1 0 ${-radius} 0Z`;
}

/**
 * A point of the sphere as seen from the eye: where it lands on the frame,
 * and whether it faces the eye at all.
 */
export function project(
  eye: Eye,
  radius: number,
  point: Geo,
): { x: number; y: number; front: boolean } {
  const lat0 = eye.lat * RAD;
  const lat = point.lat * RAD;
  const dlon = (point.lon - eye.lon) * RAD;
  const cosc = Math.sin(lat0) * Math.sin(lat) + Math.cos(lat0) * Math.cos(lat) * Math.cos(dlon);
  return {
    x: radius * Math.cos(lat) * Math.sin(dlon),
    //: Screen y grows downwards; north is up.
    y: -radius * (Math.cos(lat0) * Math.sin(lat) - Math.sin(lat0) * Math.cos(lat) * Math.cos(dlon)),
    front: cosc >= 0,
  };
}

/** The point `share` of the way from `a` to `b` along the great circle. */
export function slerp(a: Geo, b: Geo, share: number): Geo {
  const toVec = (p: Geo) => {
    const lat = p.lat * RAD;
    const lon = p.lon * RAD;
    return [Math.cos(lat) * Math.cos(lon), Math.cos(lat) * Math.sin(lon), Math.sin(lat)];
  };
  const [ax, ay, az] = toVec(a);
  const [bx, by, bz] = toVec(b);
  const dot = Math.max(-1, Math.min(1, ax * bx + ay * by + az * bz));
  const omega = Math.acos(dot);
  if (omega < 1e-9) return { lat: a.lat, lon: a.lon };
  const wa = Math.sin((1 - share) * omega) / Math.sin(omega);
  const wb = Math.sin(share * omega) / Math.sin(omega);
  const x = wa * ax + wb * bx;
  const y = wa * ay + wb * by;
  const z = wa * az + wb * bz;
  return { lat: Math.atan2(z, Math.hypot(x, y)) / RAD, lon: Math.atan2(y, x) / RAD };
}

/** The point `metres` along the great circle from `from` on `bearing`
 *  (degrees clockwise from north), on a sphere of `radius` map units. */
export function ahead(radius: number, from: Geo, bearing: number, metres: number): Geo {
  const lat = from.lat * RAD;
  const way = bearing * RAD;
  const span = (metres * UNITS_PER_METRE) / radius;
  const lat2 = Math.asin(
    Math.sin(lat) * Math.cos(span) + Math.cos(lat) * Math.sin(span) * Math.cos(way),
  );
  const dlon = Math.atan2(
    Math.sin(way) * Math.sin(span) * Math.cos(lat),
    Math.cos(span) - Math.sin(lat) * Math.sin(lat2),
  );
  const lon = from.lon + dlon / RAD;
  return { lat: lat2 / RAD, lon: ((lon + 180) % 360 + 360) % 360 - 180 };
}

/**
 * The visible part of the great-circle arc between two points, as a polyline:
 * the whole arc where it faces the eye, nothing where none of it does, and
 * the near piece where it dips over the horizon. One piece at most -- the
 * horizon is a great circle too, and two great circles cross once on a side.
 */
export function arc(eye: Eye, radius: number, a: Geo, b: Geo, steps = ARC_STEPS): Point[] | null {
  const run: Point[] = [];
  for (let i = 0; i <= steps; i++) {
    const here = project(eye, radius, slerp(a, b, i / steps));
    if (here.front) run.push({ x: here.x, y: here.y });
    else if (run.length) break;
  }
  return run.length >= 2 ? run : null;
}

/** Where a label sits on a polyline: its middle point. */
export function midOf(run: readonly Point[]): Point {
  return run[run.length >> 1];
}

/**
 * The eye after the hand has dragged the ground by `dx`, `dy` map units:
 * the ground follows the hand, so the eye moves the other way -- west when
 * the ground goes east, south when it goes down. Two angles, north up.
 */
export function turn(eye: Eye, radius: number, dx: number, dy: number): Eye {
  const lat = Math.max(-LAST_LAT, Math.min(LAST_LAT, eye.lat + (dy / radius) / RAD));
  const stretch = Math.max(Math.cos(eye.lat * RAD), 1e-9);
  const lon = eye.lon - (dx / (radius * stretch)) / RAD;
  return { lat, lon: ((lon + 180) % 360 + 360) % 360 - 180 };
}

/** Every placed node of a scene as the eye sees it; what faces away is left out,
 *  and a node without a place on the sphere is not this function's business. */
export function projectAll(
  eye: Eye,
  radius: number,
  nodes: readonly { key: string; place?: Geo | { x: number; y: number } | null }[],
): Map<string, Point> {
  const out = new Map<string, Point>();
  for (const node of nodes) {
    if (!node.place || !("lat" in node.place)) continue;
    const seen = project(eye, radius, node.place);
    if (seen.front) out.set(node.key, { x: seen.x, y: seen.y });
  }
  return out;
}
