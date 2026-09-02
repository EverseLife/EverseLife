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

import type { ForecastDay, MapRoute } from "../../api";
import { t } from "../../locale";
import { H, W } from "./model";

export const STAR = { x: W / 2, y: H / 2 };
export const TURN = Math.PI * 2;
export const MS_PER_DAY = 86_400_000;
//: The orbit is honest, and an honest orbit is not visible: Terra walks half a
//: degree an hour. So the motion is shown by winding the clock forward rather
//: than by waiting -- as far as the engine's calendar goes, three days a
//: second. `FORECAST_DAYS` is only the fallback for a sky with no corridors:
//: the horizon itself is read off the calendar (`horizon`, D-225).
export const FORECAST_DAYS = 60;
export const FORECAST_SPEED = 3;

/** How far ahead the sky may be wound: the length of the corridors' calendar. */
export function horizon(routes: MapRoute[] | undefined): number {
  const days = (routes ?? []).map((route) => route.days?.length ?? 0);
  const top = Math.max(0, ...days);
  return top > 0 ? top : FORECAST_DAYS;
}
//: How far off its planet a docked ship stands: clear of the body and its
//: name, close enough to read as "at this port".
export const BERTH = 26;
//: How much of the frame is left round the outermost orbit. Radii from the
//: vault are proportions, not pixels: the far ring is fitted into the frame and
//: every other one shrinks with it, or the farthest planet -- the one a player
//: most wants to look at -- goes over the edge.
export const MARGIN = 60;

export const HOURS_PER_DAY = 24;
//: How close to the calendar's dip still counts as "the window is open": a
//: tenth of the way up from the cheapest day to the dearest. Near enough that
//: waiting buys almost nothing.
export const WINDOW_EDGE = 0.1;

/** Which side of its planet a ship is moored on: steady, and its own per ship. */
export function mooring(key: string): number {
  let seed = 0;
  for (const ch of key) seed = (seed * 31 + ch.charCodeAt(0)) % 997;
  return (seed / 997) * Math.PI * 2;
}

/**
 * The corridor's forecast for a day: the cheapest passage leaving then (D-271).
 *
 * The engine drew the calendar, one entry per day from now; the client only
 * leafs through it as the sky is wound forward. A day past the calendar's end
 * has no forecast, and the map says nothing rather than guessing.
 */
export function forecast(route: MapRoute, day: number): ForecastDay | undefined {
  const days = route.days ?? [];
  if (days.length === 0) return undefined;
  const index = Math.min(days.length - 1, Math.max(0, Math.floor(day - days[0].day)));
  return days[index];
}

/** Whether the window is open on that day: the cheapest arc costs within a
 *  tenth of the spread above the calendar's dip. */
export function windowOpen(route: MapRoute, day: number): boolean {
  const today = forecast(route, day);
  const days = route.days ?? [];
  if (!today || days.length === 0) return false;
  let low = Infinity;
  let high = -Infinity;
  for (const one of days) {
    low = Math.min(low, one.dv);
    high = Math.max(high, one.dv);
  }
  return today.dv - low <= (high - low) * WINDOW_EDGE;
}

/**
 * Where along an arc a share of the flight time falls, in the arc's own units.
 *
 * The points are at equal time steps, so the share picks the segment and the
 * remainder interpolates inside it: no orbital arithmetic on the client, the
 * server drew the line (D-225, D-271).
 */
export function along(arc: [number, number][], share: number): [number, number] {
  const last = arc.length - 1;
  if (last <= 0) return arc[0] ?? [0, 0];
  const held = Math.min(1, Math.max(0, share));
  const at = held * last;
  const i = Math.min(last - 1, Math.floor(at));
  const rest = at - i;
  return [
    arc[i][0] + (arc[i + 1][0] - arc[i][0]) * rest,
    arc[i][1] + (arc[i + 1][1] - arc[i][1]) * rest,
  ];
}

/** A term in words: hours until they turn into days. Real time, not the planet's.
 *
 * Rounded before the unit is chosen, not after. The other way round, 23.9 hours
 * printed "24 ч" while 24 hours printed "1.0 сут" -- two adjacent moments in
 * two different units, the earlier one reading as the longer.
 */
export function term(hours: number): string {
  const shown = hours < 10 ? Number(hours.toFixed(1)) : Math.round(hours);
  //: The number is handed over already written out: Fluent would otherwise
  //: format it by the language -- "1,5" for "1.5" -- and the term would stop
  //: matching the numbers set beside it in the code.
  if (shown < HOURS_PER_DAY) return t("ui-map-term-hours", { term: String(shown) });
  return t("ui-map-term-days", { term: (shown / HOURS_PER_DAY).toFixed(1) });
}
