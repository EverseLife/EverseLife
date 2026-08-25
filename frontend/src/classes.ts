// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

import type { RecipeBook } from "./api";

/**
 * Thing classes on the client side (D-215).
 *
 * Behaviour binds to a class, never to a name: the engine asks "is there a
 * machine of class «Верфь» here", and so must the client. A panel that compared
 * `stations.includes("Космическая верфь")` (over the names of the bench) worked only while the one yard in
 * the world kept that exact name -- a second yard, or a rename in the vault,
 * would hide the window without a word. The members of a class come from
 * `build/recipes.json` (`classes`), so a new bed or printer is data alone.
 */

/**
 * Concrete item names of a class. A word the catalog does not know as a
 * class falls back to itself, name-for-name -- the same fallback the engine
 * keeps in `world.station_names`, so a bare name still matches itself.
 */
export function membersOf(book: RecipeBook | null, thingClass: string): string[] {
  const members = book?.classes?.[thingClass] as string[] | undefined;
  return members && members.length > 0 ? members : [thingClass];
}

/**
 * Whether the thing is a relic of the Forerunners (D-232): found, never made,
 * and never taken down. The client asks the catalog rather than the look --
 * what a thing **is** does not change from node to node (D-225).
 */
export function isRelic(book: RecipeBook | null, name: string): boolean {
  return Boolean(book?.materials?.some((one) => one.name === name && one.relic));
}

/** The class of a thing, or `null` when it has none. */
export function classOf(book: RecipeBook | null, name: string): string | null {
  const classes: Record<string, string[]> = book?.classes ?? {};
  for (const [thingClass, members] of Object.entries(classes)) {
    if (members.includes(name)) return thingClass;
  }
  return null;
}

/** Whether any of the `names` is a thing of the class. */
export function anyOfClass(book: RecipeBook | null, names: readonly string[], thingClass: string): boolean {
  return firstOfClass(book, names, thingClass) !== undefined;
}

/** The first of the `names` that is a thing of the class, or `undefined`. */
export function firstOfClass(
  book: RecipeBook | null,
  names: readonly string[],
  thingClass: string,
): string | undefined {
  const members = new Set(membersOf(book, thingClass));
  return names.find((name) => members.has(name));
}
