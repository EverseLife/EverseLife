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
 * Not a timer on data (D-226): on the map the wind runs only while the hand
 * holds it, and the globe before the world winds it by itself as a picture
 * of time passing (`running` below, D-337) -- nothing is asked of the server
 * either way. The map's pace is the watcher's to pick (owner, 2026-09-12)
 * and is kept in the browser, as the map's layer is.
 */

import { useCallback, useEffect, useRef, useState } from "react";

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
  /** Move a wind nobody holds on by this many milliseconds of the caller's
   *  frames (`running` below). Nothing for the hand's wind, which keeps
   *  frames of its own. */
  tick: (ms: number) => void;
};

/** The longest step one frame may wind, milliseconds: a tab come back from
 *  the background resumes where it stopped, rather than leaping the days it
 *  was hidden for at once. */
export const WIND_STEP_MAX_MS = 200;

/** Days wound a real second at a pace: a year of the planet's in
 *  `YEAR_WIND_SECONDS` at the plain one, so the pace is a share of the year
 *  and a planet's day passes as fast as its year divides into days. */
export function windPerSecond(horizon: number, pace: Pace): number {
  return (horizon / YEAR_WIND_SECONDS) * PACE_OF[pace];
}

/** The day ahead after `ms` of winding at `perSecond` days a second. The
 *  hand's wind comes round to now past the horizon; a wind nobody holds runs
 *  on (`useYear`). */
export function advance(
  ahead: number,
  ms: number,
  perSecond: number,
  horizon: number,
  loops: boolean,
): number {
  const next = ahead + (Math.min(Math.max(ms, 0), WIND_STEP_MAX_MS) / 1000) * perSecond;
  return loops ? next % horizon : next;
}

/**
 * `running`: whose hand is on the wind. Left out, the watcher's -- the map's
 * winder, started and stopped by the hand at the pace the watcher keeps, on
 * frames of its own. Given, nobody's: the globe before the world, where time
 * is shown passing rather than looked up (owner, 2026-09-13, D-337). A pace
 * winds it at that pace by the caller's frames (`tick`) -- one loop on the
 * screen, so the globe's turn and the time it shows are one commit and one
 * draw of the ground, not two -- and `null` holds it at now: the door step,
 * where the planet is shown as it really is, and a screen with no clock yet.
 * A pace given after `null` runs from now again.
 *
 * A wind nobody holds does not come round to now at the horizon: a year does
 * not divide into whole days or whole slices of weather, and the wrap jumped
 * the sun and the clouds once a year of the wind. On the map the wrap stays,
 * since there the slider shows the day ahead and a year is as far as it
 * reaches.
 */
export function useYear(horizon: number, running?: Pace | null): Year {
  const [ahead, setAhead] = useState(0);
  const aheadRef = useRef(0);
  const [held, setWinding] = useState(false);
  const [kept, setPace] = useKept<Pace>(PACE, "one", oneOf(PACES));
  const pace = running ?? kept;
  const byHand = running === undefined;
  const winding = byHand ? held : running !== null;

  //: Told to hold, a wind nobody holds is at now from the very render that
  //: says so -- not a render later, with a frame of the wound sun drawn in
  //: between. The ref comes back too, so a pace given again runs from now.
  const shownAhead = running === null ? 0 : ahead;
  useEffect(() => {
    if (running !== null) return;
    aheadRef.current = 0;
  }, [running]);

  useEffect(() => {
    if (!byHand || !held) return;
    let raf = 0;
    let last = performance.now();
    let shown = last;
    //: A pace picked mid-wind restarts the loop here; the day ahead lives
    //: in the ref and carries over, so the hand does not jump.
    const perSecond = windPerSecond(horizon, pace);
    const step = (now: number) => {
      aheadRef.current = advance(aheadRef.current, now - last, perSecond, horizon, true);
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
  }, [byHand, held, horizon, pace]);

  const tick = useCallback(
    (ms: number) => {
      if (typeof running !== "string") return;
      aheadRef.current = advance(aheadRef.current, ms, windPerSecond(horizon, running), horizon, false);
      setAhead(aheadRef.current);
    },
    [running, horizon],
  );

  const wind = (day: number) => {
    setWinding(false);
    const clamped = Math.max(0, Math.min(horizon, day));
    aheadRef.current = clamped;
    setAhead(clamped);
  };

  return {
    ahead: shownAhead,
    horizon,
    winding,
    setWinding,
    wind,
    atMs: Date.now() + shownAhead * MS_PER_REAL_DAY,
    pace,
    setPace,
    tick,
  };
}
