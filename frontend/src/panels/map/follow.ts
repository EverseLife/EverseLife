// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The two rules by which the camera follows a body on a globe (D-238).
 *
 * Both were three lines inside an effect, and both were wrong in a way that
 * only showed on a planet: the frame shook while walking, and the tether tied
 * back on sometimes turned nothing. Pure and apart, so what they promise can
 * be pinned.
 */

import type { Geo } from "./globe";
import type { Point } from "./model";

/** A place a globe can be turned to, out of whatever the caller has: the
 *  first candidate that is a point of a sphere. A flat place -- a room's, a
 *  hull's own layout -- is not one, and neither is a missing node. */
export function firstOnGlobe(
  places: readonly (Geo | { x: number; y: number } | null | undefined)[],
): Geo | null {
  for (const place of places) {
    if (place && "lat" in place) return place;
  }
  return null;
}

/**
 * Whether the eye should be turned to bring the walking dot back to the
 * middle.
 *
 * Two conditions, and the second is the one that was missing. **Half a
 * pixel**: a stray smaller than that is not worth a redraw of the whole
 * ground. **A new eye**: the dot is projected with the eye of the last
 * render, and a turn is a state change React lands a frame or more later --
 * so every frame until it does sees the same stray and asks for the same
 * turn again. The asks add up, the eye overshoots by as many frames as the
 * render took, and the next correction throws it back: that beat is the
 * shake (owner, 2026-09-08). One turn per eye, and the loop converges.
 */
export function needsTurn({
  dot,
  scale,
  eye,
  corrected,
}: {
  /** Where the dot stands, map units from the middle. */
  dot: Point;
  /** Map units to pixels, so the stray can be judged in pixels. */
  scale: number;
  /** The eye the dot was projected with: the last rendered one. */
  eye: Geo | null;
  /** The eye the last turn was asked for against. */
  corrected: Geo | null;
}): boolean {
  if (corrected === eye) return false;
  return Math.hypot(dot.x, dot.y) * scale >= HALF_PIXEL;
}

/** The stray a turn is worth, pixels. */
export const HALF_PIXEL = 0.5;
