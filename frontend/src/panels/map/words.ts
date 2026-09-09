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

/** A price in материал, whole units. Rounded down to nothing it read as free:
 *  «Подсыпать за 0» on a road worn by a hair offered work for no price, and a
 *  button that asks for nothing is a button nobody believes. Under a unit the
 *  number is not the answer -- that it is less than one is. */
export function bulk(amount: number): string {
  if (amount <= 0) return "0";
  return amount < 1 ? "<1" : amount.toFixed(0);
}

/** What to call a node where a name is expected and only the name is at
 *  hand. A found node has none (D-321) -- its sign on the map is the whole of
 *  what it is called, and an empty label there is right. A heading, a menu
 *  and a leg of a walk are not labels: empty, they read as a breakage, so
 *  they say what the thing is. Where the node itself is at hand, `nodeWord`
 *  says the better word. */
export function nameWord(name: string | null | undefined): string {
  return name || t("ui-map-node-unnamed");
}

/** What to call a node, the node being at hand: its own name, or -- a found
 *  one, which has none -- its biome, in the vault's word for it
 *  (`biome.names`, D-321: "слово о найденном узле в отказах и сводке").
 *
 *  The same word the world uses: the engine writes it into the refusal that
 *  turns a scout away and into the leg of a walk (`biome.word_of`), so the
 *  column and the world do not call one node two things. The table comes off
 *  `/public/constants` and may not have arrived yet -- then the general word
 *  stands in, as it does for a node whose kind the map was not told. */
export function nodeWord(
  face: { name?: string | null; features?: readonly string[] | null },
  names: unknown,
): string {
  if (face.name) return face.name;
  const table = (names ?? {}) as Record<string, unknown>;
  for (const sign of face.features ?? []) {
    const word = table[sign];
    if (typeof word === "string" && word) return word;
  }
  return t("ui-map-node-unnamed");
}

/** The province a node lies in, in the vault's word for it (landscape plan,
 *  wave 3; D-251): the wire carries the id, `/public/renames` the name in
 *  the reader's language. Nothing for a node without a province, and the id
 *  itself while the table has not arrived -- better a key than a blank. */
export function provinceWord(
  face: { province?: string | null },
  names: { provinces?: Record<string, string> } | null | undefined,
): string | null {
  if (!face.province) return null;
  return names?.provinces?.[face.province] ?? face.province;
}
