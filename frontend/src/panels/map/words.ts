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
import type { Names } from "../../names";

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
 *  one, which has none -- the face its ground wears, and failing that its
 *  biome, in the vault's word for either (landscape plan wave 7; D-321:
 *  "слово о найденном узле в отказах и сводке").
 *
 *  The facet first because that is the point of it: six finds on one shore
 *  were six «Берега», and they are a beach, a spit, a rock shelf. The word
 *  comes off `/public/renames` in the reader's language; the biome's off
 *  `/public/constants`. Either table may not have arrived yet -- then the
 *  general word stands in, as it does for a node whose kind was not told.
 *
 *  The refusals of the world still say the biome (`biome.word_of`): the
 *  engine hands out no facet words until the words of a node move into the
 *  locales (OQ-162), and a refusal that named the facet would be a word the
 *  server cannot say in the reader's language. */
export function nodeWord(
  face: { name?: string | null; features?: readonly string[] | null; facet?: string | null },
  names: unknown,
  renames?: Names | null,
): string {
  if (face.name) return face.name;
  if (face.facet) {
    const word = renames?.facets?.[face.facet];
    if (word) return word;
  }
  const table = (names ?? {}) as Record<string, unknown>;
  for (const sign of face.features ?? []) {
    const word = table[sign];
    if (typeof word === "string" && word) return word;
  }
  return t("ui-map-node-unnamed");
}

/**
 * What the map writes beside a node's mark.
 *
 * A city's name where the whole city is one point, the node's own where one
 * can walk into it: «Столица Терры» from orbit, «Ядро: Принтер Предтеч» from
 * the street (D-330). One node, two names, and which is shown is a fact of
 * the frame -- there is no second node to carry the other any more.
 *
 * The city's name comes from the server on the city's row alone and the
 * client cannot work it out (D-225); a row without one -- a dead city of the
 * Forerunners, an older server -- is called what it is called.
 *
 * `own` is the node's own word where that is more than its `name`: the
 * column and the menu head a nameless find by its signs (`nodeWord`), and
 * the rule which of the two names is shown must not be written a second
 * time next to them. Clicking the mark «Столица Терры» from orbit and
 * reading «Ядро: Принтер Предтеч» in the column beside it is two names of
 * one circle in one frame.
 */
export function markWord(
  face: { name: string; city?: string | null },
  settlement: boolean,
  own?: string,
): string {
  return (settlement && face.city) || own || face.name;
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
