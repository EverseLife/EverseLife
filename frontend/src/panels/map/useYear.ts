// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Winding a planet's year forward (D-334): the one way to see a season turn.
 *
 * The sky has its winder (`useSky`): the planets move too slowly to watch,
 * so the motion is shown by winding time. The season is slower still -- a
 * week of Terra's from snow to thaw -- and gets the same: a day ahead the
 * sun stands over another latitude, the snow line has moved, and the map
 * says which moment it is showing. Nothing of the world changes for it:
 * the engine keeps its own clock, and the winder is a hand on the map's.
 * Not a timer on data (D-226): the wind runs only while the hand holds it.
 * The pace of the wind is the watcher's to pick (owner, 2026-09-12) and is
 * kept in the browser, as the map's layer is.
 */

import { useEffect, useRef, useState } from "react";

import { oneOf, useKept } from "../../kept";
import type { Winding } from "./Winder";

const MS_PER_REAL_DAY = 86_400_000;
/** How long a wound year takes on the glass at the plain pace, seconds: a
 *  season a few seconds, long enough to see the snow come and go. */
export const YEAR_WIND_SECONDS = 30;
/** How often the winding hand moves the map while it runs, a second: the
 *  ground redraws at each move, and the whole map with it; ten a second
 *  is a season passing smoothly, sixty was the map drawn for nothing. */
export const YEAR_WIND_HZ = 10;
/** Without a book there is no year to wind over: a day, and the winder
 *  is inert. */
export const YEAR_FALLBACK_DAYS = 1;

/** The paces the year may be wound at, as multiples of the plain one: a
 *  sixteenth (a day in a quarter of a minute, to watch a shadow swing), an
 *  eighth, a quarter (a season in half a minute, to watch the snow line
 *  creep), the plain, four (a year in a few breaths) and sixteen (a year
 *  in two seconds, to find a day). Named, not numbered, as the book keeps
 *  them. */
export const PACES = ["sixteenth", "eighth", "quarter", "one", "four", "sixteen"] as const;
export type Pace = (typeof PACES)[number];
export const PACE_OF: Record<Pace, number> = {
  sixteenth: 1 / 16,
  eighth: 1 / 8,
  quarter: 1 / 4,
  one: 1,
  four: 4,
  sixteen: 16,
};
/** The pace kept across sessions, next to the map's layer (`kept.ts`). */
const PACE = "everselife.map.year-pace";

/** The planet's year, real days (`orbit.period_days`), as the sky counts
 *  it; a planet the book has no year for is wound over Terra's, as
 *  `sky.circle_of` reads its year off the book -- not off a number here. */
export function yearOf(constants: Record<string, unknown> | null | undefined, planet: string): number {
  const periods = (constants?.["orbit.period_days"] ?? {}) as Record<string, number>;
  const own = Number(periods[planet]);
  if (Number.isFinite(own) && own > 0) return own;
  const terra = Number(periods.terra);
  return Number.isFinite(terra) && terra > 0 ? terra : YEAR_FALLBACK_DAYS;
}

export type Year = Winding & {
  /** The moment shown, as the sun and the season are asked for it. */
  atMs: number;
  pace: Pace;
  setPace: (pace: Pace) => void;
};

export function useYear(horizon: number): Year {
  const [ahead, setAhead] = useState(0);
  const aheadRef = useRef(0);
  const [winding, setWinding] = useState(false);
  const [pace, setPace] = useKept<Pace>(PACE, "one", oneOf(PACES));

  useEffect(() => {
    if (!winding) return;
    let raf = 0;
    let last = performance.now();
    let shown = last;
    //: A pace picked mid-wind restarts the loop here; the day ahead lives
    //: in the ref and carries over, so the hand does not jump.
    const perSecond = (horizon / YEAR_WIND_SECONDS) * PACE_OF[pace];
    const step = (now: number) => {
      aheadRef.current = (aheadRef.current + ((now - last) / 1000) * perSecond) % horizon;
      last = now;
      //: The hand moves every frame, the map YEAR_WIND_HZ times a second.
      if (now - shown >= 1000 / YEAR_WIND_HZ) {
        shown = now;
        setAhead(aheadRef.current);
      }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [winding, horizon, pace]);

  const wind = (day: number) => {
    setWinding(false);
    const clamped = Math.max(0, Math.min(horizon, day));
    aheadRef.current = clamped;
    setAhead(clamped);
  };

  return {
    ahead,
    horizon,
    winding,
    setWinding,
    wind,
    atMs: Date.now() + ahead * MS_PER_REAL_DAY,
    pace,
    setPace,
  };
}
