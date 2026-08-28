// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The heat reserve, counted on the client (D-226, D-231).
 *
 * The server says what the reserve was at a stamp and how fast it moves here;
 * the hand is drawn locally, the way the planet's clock is. Asking the server
 * for the hours every second would be a poll, and the number would still be
 * stale between two answers.
 *
 * It lives in its own module because the same arithmetic is written three
 * times over -- here, in `engine/frost.view` and in the agents' digest -- and
 * one of the three is testable only if it is not buried in a component.
 */

import type { Frost } from "./api";

const MS_PER_HOUR = 3_600_000;

/** What the reserve is at `at`, given what it was at the stamp. */
export function reserveAt(frost: Frost, at: number): number {
  const hours = (at - new Date(frost.at).getTime()) / MS_PER_HOUR;
  return Math.max(0, Math.min(frost.max, frost.hours + frost.per_hour * hours));
}

/** What the reserve is now. */
export const reserveNow = (frost: Frost): number => reserveAt(frost, Date.now());

/**
 * The air left in the cylinder or the tanks, counted the same way (D-233).
 *
 * The server names a level, a rate and the moment both were measured at; the
 * hand is the client's, so a screen that has not been pushed to in a minute
 * still shows the truth. Never below nothing: an empty cylinder is empty, and
 * how long ago it emptied is the world's business, not the gauge's.
 */
export type Breathing = { units: number; per_hour: number; at: string };

export function leftAt(air: Breathing, at: number): number {
  const hours = (at - new Date(air.at).getTime()) / MS_PER_HOUR;
  return Math.max(0, air.units + air.per_hour * hours);
}

export const leftNow = (air: Breathing): number => leftAt(air, Date.now());
