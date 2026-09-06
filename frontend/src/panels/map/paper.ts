// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The paper the entry globe is drawn on (D-319): where the planet stands on a
 * canvas that is the whole window, and how much ground is worth laying.
 *
 * The globe used to be drawn in a box of its own, and a zoomed globe was cut
 * at that box's edge -- a seam a third of the way across the screen with the
 * page behind it. So the canvas is the window now. But the planet must not
 * move for that: it stands where an empty square in the flow says it stands,
 * the size that square is. This turns the three boxes -- the square, the half
 * of the screen the globe has, and the canvas -- into the viewBox that keeps
 * that promise, and into the two numbers that keep the promise affordable.
 *
 * The size of a printer's mark is here too, for the same reason: it is
 * arithmetic about the drawing, and the shape of that arithmetic is the whole
 * of what it promises.
 *
 * Pure arithmetic, so a test can hold the browser's part of it still: the
 * disk's centre and diameter are the whole of what a reader notices, and
 * nothing but a test says they did not change.
 */

/** The smallest and the largest a printer's mark is drawn, in shares of the
 *  mark's own size: a door of ten citizens must still be a target for a
 *  finger, and a door of a million must not swallow the planet. */
export const MARK_LEAST = 0.7;
export const MARK_MOST = 2.2;
/** The crowd at which a mark is as large as it gets. Above it the marks stop
 *  telling cities apart -- a city of a million and one of three million are
 *  both simply the big one -- and that is the honest end of the scale. */
export const MARK_CROWD = 100000;

/**
 * How large a printer's mark is drawn, from the citizens behind it: a share of
 * the mark's own size, between `MARK_LEAST` and `MARK_MOST`.
 *
 * By the logarithm, not by the count. Cities differ by orders of magnitude --
 * ten citizens against a million -- and a radius drawn to the number would put
 * a young city under a pixel and a capital across the ocean it stands on. What
 * a newcomer reads off the globe is "this one is bigger than that one", which
 * a compressed scale says at every size; the exact number is in the card.
 */
export function markShare(citizens: number): number {
  const people = Math.max(0, citizens);
  const share = Math.log10(1 + people) / Math.log10(1 + MARK_CROWD);
  return MARK_LEAST + (MARK_MOST - MARK_LEAST) * Math.min(1, share);
}

/** A rectangle as the browser measures it, in the client's own coordinates. */
export type Box = { x: number; y: number; w: number; h: number };

export type Paper = {
  /** Picture units to a pixel: the planet's own scale, from the square. */
  perPixel: number;
  /** How far from the planet's middle the ground is laid, in picture units:
   *  to the edges of the half the globe has, and no further -- the rest of
   *  the canvas is behind the way in, which is opaque. */
  reach: number;
  /** How many squares of the old canvas the drawn ground spans. The grid is
   *  read that much coarser: the same ground at the same fineness over five
   *  times the area is five times the paths, laid afresh at every turn -- and
   *  the globe turns by itself here, on whatever the visitor has. Never below
   *  one, because the square lies inside the half. */
  spread: number;
  /** How much of the picture the seen half holds: whether the ground is drawn
   *  cell by cell or as one tone under the eye. */
  across: number;
  /** The window in picture units, with the planet's middle where the square
   *  puts it. The sides are the canvas's own, so nothing is letterboxed. */
  viewBox: string;
};

/**
 * `span` is the picture across the square -- the planet and the room round it
 * at this zoom. Every box is measured the same way, so the canvas's own
 * corner is what the other two are read against: a canvas that does not start
 * at the client's origin still lands the planet on the square.
 */
export function paper(square: Box, half: Box, canvas: Box, span: number): Paper {
  const side = Math.min(square.w, square.h);
  const perPixel = span / side;
  //: The planet's middle, in the canvas's own pixels.
  const x = square.x + square.w / 2 - canvas.x;
  const y = square.y + square.h / 2 - canvas.y;
  //: The furthest edge of the seen half from that middle: a square about the
  //: planet that covers the half leaves no corner of it unlaid.
  const reach =
    Math.max(
      square.x + square.w / 2 - half.x,
      half.x + half.w - (square.x + square.w / 2),
      square.y + square.h / 2 - half.y,
      half.y + half.h - (square.y + square.h / 2),
    ) * perPixel;
  return {
    perPixel,
    reach,
    spread: (2 * reach) / span,
    across: Math.max(half.w, half.h) * perPixel,
    viewBox: `${-x * perPixel} ${-y * perPixel} ${canvas.w * perPixel} ${canvas.h * perPixel}`,
  };
}
