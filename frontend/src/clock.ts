// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Time in words: local time of the planet and human-readable moments.
 *
 * The world has its own day: a Terran one lasts `time.day_terra` hours and on
 * purpose does not match anybody's wall clock (D-029). So a date is shown as
 * the world counts it -- "day 12, 07:40" -- and not as the browser's locale
 * would render an ISO string.
 *
 * Moments of real time (a batch is ready, a claim expires) are shown by how far
 * away they are: "in 3 min", "an hour ago". An absolute stamp is only added
 * where the exact hour matters, and then in the world's own clock.
 */

import { t } from "./locale";

/** Where the world's count starts and how long its day is (from `look.clock`). */
export type Clock = { planet: string; epoch?: string; day_hours: number };

const MS_PER_HOUR = 3_600_000;

/** Local time of the planet at this real moment. */
export function worldTime(clock: Clock, at: Date = new Date()) {
  const epoch = clock.epoch ? new Date(clock.epoch).getTime() : at.getTime();
  const hoursGone = Math.max(0, (at.getTime() - epoch) / MS_PER_HOUR);
  const day = Math.floor(hoursGone / clock.day_hours) + 1;
  const inDay = hoursGone % clock.day_hours;
  const hour = Math.floor(inDay);
  const minute = Math.floor((inDay - hour) * 60);
  return { day, hour, minute };
}

/** "07:40" -- the hands of the local clock. */
export function hands(clock: Clock, at: Date = new Date()): string {
  const { hour, minute } = worldTime(clock, at);
  return `${two(hour)}:${two(minute)}`;
}

/** "сутки 12 · 07:40" -- the full local stamp. */
export function stamp(clock: Clock, at: Date = new Date()): string {
  const { day } = worldTime(clock, at);
  //: The day is a count, not a measure: through `NUMBER` it would grow a
  //: thousands separator on the day the world turns 1200.
  return t("ui-clock-stamp", { day: String(day), hands: hands(clock, at) });
}

/**
 * How far away a moment is, with nothing said about which side of now it
 * falls on: "3 мин", "вот-вот", where `when` would say "через 3 мин".
 *
 * For a caller whose own sentence already carries the direction -- «ещё на
 * столько-то». That caller used to cut the word off `when`'s answer with
 * `.replace("через ", "")`: a rule of one language living in a panel, and in
 * every other language it left the sentence whole and nonsensical.
 */
export function span(iso: string | null | undefined, at: Date = new Date()): string {
  if (!iso) return t("ui-clock-never");
  const left = (new Date(iso).getTime() - at.getTime()) / 1000;
  const size = Math.abs(left);
  if (size < 45) return t(left >= 0 ? "ui-clock-soon" : "ui-clock-just-now");
  return duration(size);
}

/**
 * A moment relative to now: "через 3 мин", "5 мин назад", "вот-вот".
 *
 * Duration is what a person acts on -- whether to wait or to go do something
 * else, -- so it comes first. The exact hour is added by the caller where it
 * is needed, through `stamp`.
 */
export function when(iso: string | null | undefined, at: Date = new Date()): string {
  if (!iso) return t("ui-clock-never");
  const left = (new Date(iso).getTime() - at.getTime()) / 1000;
  const said = span(iso, at);
  //: "вот-вот" and "только что" already say which way they point.
  if (Math.abs(left) < 45) return said;
  return t(left >= 0 ? "ui-clock-ahead" : "ui-clock-ago", { span: said });
}

/** Duration in words: "3 мин", "2 ч 10 мин", "1.5 сут" by the world's day.
 *
 * Rounded **before** it is split, and the carry taken: rounding each part on
 * its own printed "7 ч 60 мин" for eight hours and "60 мин" for an hour, and
 * that showed up wherever a term was nearly whole -- the build site, the road,
 * the statement. */
export function duration(seconds: number, dayHours = 24): string {
  //: Every count goes in as a string: a term is read, not summed, and
  //: `NUMBER` would put a separator inside "1 200 ч".
  if (seconds < 60) {
    const said = Math.round(seconds);
    if (said < 60) return t("ui-clock-seconds", { n: String(said) });
    seconds = said;
  }
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return t("ui-clock-minutes", { n: String(minutes) });
  const hours = minutes / 60;
  if (hours < dayHours) {
    const rest = minutes % 60;
    return rest
      ? t("ui-clock-hours-minutes", { n: String((minutes - rest) / 60), rest: String(rest) })
      : t("ui-clock-hours", { n: String(minutes / 60) });
  }
  return t("ui-clock-days", { n: (hours / dayHours).toFixed(1) });
}

function two(value: number): string {
  return String(value).padStart(2, "0");
}
