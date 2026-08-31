// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Terms and prices in the player's own words.
 *
 * A run costs minutes or hours and a fraction of the body's strength, and both
 * are read at a glance rather than compared: "40 мин" and "<0.1" say what a
 * number with three decimals does not.
 */

import { t } from "../../locale";

const MINUTES_PER_HOUR = 60;

export function long(minutes: number): string {
  return `${account(minutes)} ${unit(minutes)}`;
}

export function spread(from: number, until: number): string {
  return unit(from) === unit(until)
    ? `${account(from)}–${account(until)} ${unit(until)}`
    : `${long(from)} – ${long(until)}`;
}

//: The unit alone, because `spread` names it once for both ends and has to be
//: able to ask whether the two ends share one. Compared as the rendered word:
//: two keys that came out the same are the same unit in this language.
function unit(minutes: number): string {
  return t(minutes < MINUTES_PER_HOUR ? "ui-map-unit-minutes" : "ui-map-unit-hours");
}

function account(minutes: number): string {
  if (minutes < MINUTES_PER_HOUR) return String(Math.round(minutes));
  const hours = minutes / MINUTES_PER_HOUR;
  return hours % 1 === 0 ? String(hours) : hours.toFixed(1);
}

/** The road's price to the body. A step across town costs a fraction of a unit -- and "0.0" would lie here. */
export function price(stamina: number): string {
  if (stamina <= 0) return "0";
  return stamina < 0.1 ? "<0.1" : stamina.toFixed(1);
}
