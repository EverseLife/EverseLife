// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

import type { Air, RecipeBook, Thing } from "./api";

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
 * The counter goods are sold off (D-047). It lives here rather than in the
 * panel that opens the market, because two panels ask about it now: the market
 * itself, and the inventory, which offers "В терминал" only where there is one.
 * A vault word written twice is a vault word that will be renamed once.
 */
export const TERMINAL = "terminal";

/**
 * The class that connects a body to a cylinder (D-234) -- the engine's
 * `oxygen.SUIT`, and a class for the same reason the terminal is one.
 */
export const SUIT = "spacesuit";

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
  return Boolean(book?.materials?.some((one) => (one.id ?? one.name) === name && one.relic));
}

/** Whether the station is built in place and never carried (D-268): the
 *  window does not offer to take it up, as it does not for a relic. */
export function isBuilt(book: RecipeBook | null, name: string): boolean {
  return Boolean(book?.recipes?.some((one) => (one.id ?? one.name) === name && one.built));
}

/** The vault's kind of a thing's recipe ("station", "furniture", ...), or
 *  `null` for a material, which has none. One lookup for every question
 *  about what a thing is (D-090, D-225). */
export function recipeKind(book: RecipeBook | null, name: string): string | null {
  return book?.recipes?.find((one) => (one.id ?? one.name) === name)?.kind ?? null;
}

/** Whether a thing of this kind is put up rather than put down (D-278): a
 *  machine, a piece of furniture -- or a vessel, which stands on the hull's
 *  lines once put up (D-288) -- what `station.place` accepts, and so what
 *  the "install" button is offered for. */
/**
 * Whether this worn thing is a frame that **lifts** (D-268): the vault's
 * `inventory.exo_bonus` names them, and it is the same table the engine reads.
 *
 * Asked before taking one off: the limit falls with it, and what no longer
 * fits lies down underfoot (D-306). Told before the click, not after.
 */
export function lifts(book: RecipeBook | null, name: string): boolean {
  const bonuses = book?.constants?.["inventory.exo_bonus"] as Record<string, number> | undefined;
  return Boolean(bonuses && (bonuses[name] ?? 0) > 0);
}

/**
 * The slot whose suit stays on here (D-343), or `null`.
 *
 * Where there is no air and the body breathes through a worn suit, the suit
 * neither comes off nor gives its slot to anything but another suit: the
 * server refuses, and the window says so before the click. Read off what the
 * look already sends -- the air reading (`where: "suit"` is outside with
 * nothing to breathe, `suit` that one is worn) and the worn things -- so it
 * needs no key of its own (D-225).
 */
export function suitSlot(
  book: RecipeBook | null,
  air: Air | undefined,
  equipped: Record<string, Thing>,
): string | null {
  if (air?.where !== "suit" || !air.suit) return null;
  const suits = new Set(membersOf(book, SUIT));
  const found = Object.entries(equipped).find(([, worn]) => suits.has(worn.goods));
  return found ? found[0] : null;
}

/** Whether putting this thing on would push the kept suit off (D-343). */
export function pushesSuitOff(book: RecipeBook | null, kept: string | null, thing: Thing): boolean {
  return kept !== null && thing.slot === kept && !membersOf(book, SUIT).includes(thing.goods);
}

export function isGear(book: RecipeBook | null, name: string): boolean {
  const kind = recipeKind(book, name);
  return kind === "station" || kind === "furniture" || isVessel(book, name);
}

/** A storage that holds liquids (D-230): the vault's `holds`, not a kind. The
 *  same test as `liquids.isVessel`, kept here so that a question about what
 *  a thing **is** does not pull the pouring module in behind it. */
function isVessel(book: RecipeBook | null, name: string): boolean {
  const recipe = book?.recipes?.find((one) => (one.id ?? one.name) === name);
  return recipe?.holds === "liquid" && Boolean(recipe.store);
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
