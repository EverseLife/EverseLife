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
