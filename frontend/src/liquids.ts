// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Liquids and vessels on the client side (D-230).
 *
 * A liquid -- water, spirit, rocket fuel -- never lies loose: it is inside a
 * **vessel**, a canister in the hands or a tank in a ship's room, and the one
 * way it moves is `liquid.pour` from one vessel into another. Both facts are
 * the vault's (`liquid`, `holds`), read off the book the same way the engine
 * reads them; no name is compared here.
 */

import type { RecipeBook, Thing } from "./api";
import { t } from "./locale";
import { goodsName, type NamesRu } from "./names";

/** The value of `holds` that makes a storage a vessel (the property id, D-251). */
const LIQUID = "liquid";

export function isLiquid(book: RecipeBook | null, goods: string): boolean {
  return (book?.liquid ?? []).includes(book?.synonyms?.[goods] ?? goods);
}

export function isVessel(book: RecipeBook | null, goods: string): boolean {
  const name = book?.synonyms?.[goods] ?? goods;
  return (book?.recipes ?? []).some(
    (r) => (r.id ?? r.name) === name && r.holds === LIQUID && !!r.store,
  );
}

/** The vessel's capacity, kg: the catalog's `store` (D-181), not a server key (D-225). */
export function capacityOf(book: RecipeBook | null, goods: string): number {
  const name = book?.synonyms?.[goods] ?? goods;
  return (book?.recipes ?? []).find((r) => (r.id ?? r.name) === name)?.store ?? 0;
}

/** What is poured into a vessel, in words: "Вода 12.0 · 2.4 из 20 кг". */
export function fill(book: RecipeBook | null, names: NamesRu | null, thing: Thing): string {
  const inside = thing.content ?? [];
  //: `poured`, not `t`: the name `t` is the message lookup.
  const mass = inside.reduce((sum, poured) => sum + poured.mass * poured.amount, 0);
  //: A word and a number with a space between them is not a sentence, so it
  //: stays here; every word the line has is in the messages below.
  const what =
    inside.map((poured) => `${goodsName(names, poured.goods)} ${poured.amount.toFixed(1)}`).join(", ") ||
    t("ui-liquid-empty");
  //: The three numbers go in already rounded, as strings: they are read off a
  //: gauge, and `NUMBER` would space out the thousands of a ship's tank.
  return t("ui-liquid-fill", {
    what,
    mass: mass.toFixed(1),
    capacity: capacityOf(book, thing.goods).toFixed(0),
  });
}
