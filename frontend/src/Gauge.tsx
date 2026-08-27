// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * A reading on a track (D-238, amendment 5).
 *
 * The state of a bed or a batch used to be a comma sentence -- "плодородие 62
 * из 70 нужных", "качество 62.4 ± 4.1 · потолок 80". The gauge says the same
 * thing as an instrument: a 4px track, the value filled from the left, a
 * notch where the norm or the ceiling stands, a band where the spread lies.
 * The exact number stays printed beside it -- the track is for the glance,
 * the figure is for the decision.
 *
 * It deliberately borrows the deadline bar's build (the one signature element)
 * but never moves: a gauge is a reading, not a term.
 */

import { trim } from "./amounts";

type Props = {
  label: string;
  value: number;
  /** The right edge of the track. The game's qualities and fertilities speak
   *  in 0..100; anything larger stretches the track rather than lying. */
  max?: number;
  /** A notch on the track: the norm to reach, the ceiling not to. */
  mark?: number;
  markTitle?: string;
  /** A ± band around the value: the spread of a forecast. */
  spread?: number;
  /** What to print on the right; the trimmed value if not said. */
  reading?: React.ReactNode;
  /** The warning pair when the reading is short of its norm. */
  warn?: boolean;
};

export function Gauge({ label, value, max, mark, markTitle, spread, reading, warn }: Props) {
  //: The track never lies by clipping: a value or a mark beyond the nominal
  //: edge stretches the scale instead. `|| 1` -- an all-zero call must not
  //: divide by zero.
  const edge = Math.max(max ?? 100, value + (spread ?? 0), mark ?? 0) || 1;
  const share = (part: number) => `${((Math.max(0, Math.min(part, edge)) / edge) * 100).toFixed(1)}%`;
  return (
    <div
      className={`gauge${warn ? " warn" : ""}`}
      role="meter"
      aria-valuenow={Math.round(value)}
      aria-valuemin={0}
      aria-valuemax={Math.round(edge)}
      aria-label={label}
    >
      <span className="gauge-label">{label}</span>
      <span className="gauge-track">
        <i style={{ width: share(value) }} />
        {spread != null && spread > 0 && (
          <span
            className="gauge-spread"
            style={{
              left: share(Math.max(0, value - spread)),
              width: share(Math.min(edge, value + spread) - Math.max(0, value - spread)),
            }}
          />
        )}
        {mark != null && (
          <span className="gauge-mark" style={{ left: share(mark) }} title={markTitle} />
        )}
      </span>
      <span className="gauge-reading num">{reading ?? trim(value)}</span>
    </div>
  );
}
