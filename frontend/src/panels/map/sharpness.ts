// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * How sharp the GPU ground is drawn: how many pixels of its canvas it
 * shades for every pixel of the map it lies under.
 *
 * Every pixel of the ground runs the whole fragment -- the relief, the
 * water, the cast shadow, the grain -- so the ground costs what its pixels
 * cost, and nothing else about a frame comes close. A phone has two to
 * three device pixels to the CSS pixel, up to nine times a desktop's
 * pixels for a map of the same size, on a GPU a tenth as fast or slower:
 * the same frame that a laptop paints in two milliseconds is a hundred on
 * a middling phone, the globe stutters under the finger and the phone runs
 * hot on the login screen, where the planet turns by itself.
 *
 * Two rules, and neither changes what the ground shows, only how finely:
 *
 * - **never finer than `SHARP_DPR_MAX`** device pixels to the CSS pixel.
 *   The ground is a soft picture -- its edges are blended over a few
 *   pixels on purpose (`AA_PX`) -- and past two pixels a CSS pixel the eye
 *   cannot tell it from the screen's own density; what must stay crisp,
 *   the nodes, the ways and the names, is the svg's and keeps the screen's
 *   density whatever the ground does;
 * - **while it moves, as fine as the GPU keeps up with.** Every paint made
 *   in motion is judged a frame later by whether the GPU has finished it
 *   (`judged`); two late in a row and the scale steps down, a run on time
 *   and a finer step is tried again -- and a try that fails makes the next
 *   one wait twice as long, so the scale does not see-saw about the edge.
 *   Once the ground has stood still `SETTLE_MS` it is painted once more at
 *   the full scale. A still map is as sharp as it ever was; a moving one is
 *   as smooth as the device allows, and a laptop that keeps up never leaves
 *   the top step at all.
 *
 * The shader measures what it draws finely in pixels of its own canvas
 * (`u_units`): the level of the rasters, the spread of its taps, the fading
 * in of the grain and of the ragged edge. A coarser canvas is read coarser
 * and draws a smoother ground, not a blockier one -- in motion the grain
 * thins out as a coarser pixel can no longer hold it, and comes back with
 * the still paint. What the frame shows by its **scale** -- the clouds of
 * the far frames -- is measured on the screen (`u_screen_units`) and does
 * not change with the canvas.
 */

/** The finest the ground is ever drawn, canvas pixels to a CSS pixel. */
export const SHARP_DPR_MAX = 2;

/** The scales the ground steps through in motion, of its finest: each step
 *  the fourth root of a half in the side, so two steps halve the pixels,
 *  and the last is an eighth of them. */
export const SHARP_STEPS: readonly number[] = [1, 0.84, 0.71, 0.59, 0.5, 0.42, 0.35];

/** Milliseconds with no paint after which the ground is still, and is
 *  painted whole. Well past the turn of the entry globe between its paints
 *  (a fifteenth of a second), or a planet turning by itself would be
 *  painted twice over at every step; in time and not in frames, which a
 *  screen of two hundred hertz has four times as many of. */
export const SETTLE_MS = 160;

/** When the GPU is asked whether it has finished a paint: at the first
 *  frame that starts this many milliseconds after the frame the paint was
 *  made in -- the next frame of a sixty-hertz screen, the second of a
 *  hundred and twenty, a sixtieth of a second on both. Counted from the
 *  frame's start and not from the paint: whatever the frame spent before
 *  the paint is spent, and the GPU has what is left of it, as a frame has.
 *  So a fast screen does not halve the ground for frames nobody asked for.
 *  Screens whose frames do not divide a sixtieth are asked at the frame
 *  after it -- 22 ms on ninety hertz, 21 on a hundred and forty-four -- and
 *  are held to a little less than sixty frames a second; never to more. */
export const JUDGE_MS = 14;

/** Late paints in a row before the scale steps down: one alone may be a
 *  hitch -- a still frame painted whole just before, a collection of the
 *  garbage -- and not the pace of the device. */
export const LATE_IN_A_ROW = 2;

/** Paints on time in a row before a finer step is tried, at first; doubled
 *  at every try that failed, up to the last. */
export const PATIENCE_MIN = 30;
export const PATIENCE_MAX = 960;

/** What the page has learned of its GPU's pace. */
export type Sharpness = {
  /** The step of `SHARP_STEPS` a paint in motion is drawn at. */
  step: number;
  /** Paints judged late in a row, and on time in a row, at this step. */
  late: number;
  onTime: number;
  /** Paints on time in a row before the next finer try. */
  patience: number;
  /** Whether this step was reached by a try at a finer one and has not yet
   *  held for `PATIENCE_MIN` paints: a step down while trying is a try
   *  that failed. */
  trying: boolean;
};

export const SHARP_START: Sharpness = {
  step: 0,
  late: 0,
  onTime: 0,
  patience: PATIENCE_MIN,
  trying: false,
};

/** Canvas pixels to a CSS pixel for a paint: the screen's own density,
 *  held to `SHARP_DPR_MAX`, and in motion the step's share of that. */
export function ratioOf(dpr: number, pace: Sharpness, moving: boolean): number {
  const finest = Math.min(dpr > 0 ? dpr : 1, SHARP_DPR_MAX);
  return moving ? finest * SHARP_STEPS[pace.step] : finest;
}

/** The pace after the verdict on one paint in motion: whether the GPU had
 *  finished it `JUDGE_MS` after it was made. */
export function judged(pace: Sharpness, onTime: boolean): Sharpness {
  if (!onTime) {
    const late = pace.late + 1;
    if (late < LATE_IN_A_ROW) return { ...pace, late, onTime: 0 };
    return {
      step: Math.min(pace.step + 1, SHARP_STEPS.length - 1),
      late: 0,
      onTime: 0,
      patience: pace.trying ? Math.min(pace.patience * 2, PATIENCE_MAX) : pace.patience,
      trying: false,
    };
  }
  const onTimeNow = pace.onTime + 1;
  if (pace.step > 0 && onTimeNow >= pace.patience) {
    return { ...pace, step: pace.step - 1, late: 0, onTime: 0, trying: true };
  }
  //: A tried step that has held is the device's own: a hitch minutes later
  //: is news, not a try that failed.
  const held = pace.trying && onTimeNow >= PATIENCE_MIN;
  return { ...pace, late: 0, onTime: onTimeNow, trying: held ? false : pace.trying };
}
