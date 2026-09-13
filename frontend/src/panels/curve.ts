// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The wire between two cards, as an SVG path.
 *
 * Shared by the two node pictures the client draws: the factory floor's wires
 * between automats (D-253) and the ship's scheme of lines from a machine's
 * port to a vessel (D-288, D-340). Both leave a card on its right and arrive
 * at the next on its left, and a curve that bent differently in the two would
 * be two drawing languages for one idea.
 */

/** The least a wire bends, px: two cards side by side still read as joined. */
const LEAST_BEND = 24;

/** A cubic from (x1, y1) leaving rightwards to (x2, y2) arriving from the left. */
export function curve(x1: number, y1: number, x2: number, y2: number): string {
  const bend = Math.max(LEAST_BEND, (x2 - x1) / 2);
  return `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`;
}
