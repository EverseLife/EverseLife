// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The space layer: orbits, passages and where a ship stands (D-037, D-201).
 *
 * The one layer that is **not** laid out and never was. A planet's place is a
 * function of time -- angle from the world's epoch, radius from the vault --
 * so the distance between two planets, and with it the price of the passage
 * between them, changes by itself while nobody touches the map. A stored place
 * would be a second, lying opinion about where a planet is (D-237 places the
 * ground, not the sky).
 */

import type { MapRoute } from "../../api";
import { H, W } from "./model";

export const STAR = { x: W / 2, y: H / 2 };
export const TURN = Math.PI * 2;
export const MS_PER_DAY = 86_400_000;
//: The orbit is honest, and an honest orbit is not visible: Terra walks half a
//: degree an hour. So the motion is shown by winding the clock forward rather
//: than by waiting -- a month ahead, three days a second.
export const FORECAST_DAYS = 60;
export const FORECAST_SPEED = 3;
//: How far off its planet a docked ship stands: clear of the body and its
//: name, close enough to read as "at this port".
export const BERTH = 26;
//: How much of the frame is left round the outermost orbit. Radii from the
//: vault are proportions, not pixels: the far ring is fitted into the frame and
//: every other one shrinks with it, or the farthest planet -- the one a player
//: most wants to look at -- goes over the edge.
export const MARGIN = 60;

export const HOURS_PER_DAY = 24;
//: How close to the short end still counts as "the window is open". A tenth of
//: the spread: near enough that waiting buys almost nothing.
export const WINDOW_EDGE = 0.1;

/** Which side of its planet a ship is moored on: steady, and its own per ship. */
export function mooring(key: string): number {
  let seed = 0;
  for (const ch of key) seed = (seed * 31 + ch.charCodeAt(0)) % 997;
  return (seed / 997) * Math.PI * 2;
}

/**
 * What a passage between two planets costs at this distance, in hours (D-037).
 *
 * The vault gives the two ends -- the planets on one side of the star and on
 * opposite sides of it -- and the distance says where between them the moment
 * falls. The same rule the engine settles a flight by, so the map's forecast
 * and the server's price come from one formula rather than two.
 */
export function passage(route: MapRoute, gap: number, near: number, far: number): number {
  const share = far > near ? (gap - near) / (far - near) : 0;
  const held = Math.min(1, Math.max(0, share));
  return route.window_hours + (route.apart_hours - route.window_hours) * held;
}

/** A term in words: hours until they turn into days. Real time, not the planet's.
 *
 * Rounded before the unit is chosen, not after. The other way round, 23.9 hours
 * printed "24 ч" while 24 hours printed "1.0 сут" -- two adjacent moments in
 * two different units, the earlier one reading as the longer.
 */
export function term(hours: number): string {
  const shown = hours < 10 ? Number(hours.toFixed(1)) : Math.round(hours);
  if (shown < HOURS_PER_DAY) return `${shown} ч`;
  return `${(shown / HOURS_PER_DAY).toFixed(1)} сут`;
}
