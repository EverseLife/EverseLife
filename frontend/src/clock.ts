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
  return `сутки ${day} · ${hands(clock, at)}`;
}

/**
 * A moment relative to now: "через 3 мин", "5 мин назад", "вот-вот".
 *
 * Duration is what a person acts on -- whether to wait or to go do something
 * else, -- so it comes first. The exact hour is added by the caller where it
 * is needed, through `stamp`.
 */
export function when(iso: string | null | undefined, at: Date = new Date()): string {
  if (!iso) return "—";
  const left = (new Date(iso).getTime() - at.getTime()) / 1000;
  const size = Math.abs(left);
  if (size < 45) return left >= 0 ? "вот-вот" : "только что";
  const said = duration(size);
  return left >= 0 ? `через ${said}` : `${said} назад`;
}

/** Duration in words: "3 мин", "2 ч 10 мин", "1.5 сут" by the world's day. */
export function duration(seconds: number, dayHours = 24): string {
  const minutes = seconds / 60;
  if (minutes < 1) return `${Math.round(seconds)} с`;
  if (minutes < 60) return `${Math.round(minutes)} мин`;
  const hours = minutes / 60;
  if (hours < dayHours) {
    const whole = Math.floor(hours);
    const rest = Math.round((hours - whole) * 60);
    return rest ? `${whole} ч ${rest} мин` : `${whole} ч`;
  }
  return `${(hours / dayHours).toFixed(1)} сут`;
}

function two(value: number): string {
  return String(value).padStart(2, "0");
}
