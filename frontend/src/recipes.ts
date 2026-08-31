// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

import type { RecipeBook } from "./api";
import { compare } from "./locale";
import { goodsName, type Names } from "./names";

/**
 * Reading the vault catalog on the client side.
 *
 * Exactly one task: answer **what is made at this machine**. The recipe list
 * comes from `build/recipes.json`, so a new machine or recipe appears in the
 * interface by itself, without a client change (D-090, D-133).
 */

/** The canonical machine name: "Furnace" and "Smelting furnace" are one and the same. */
function canon(book: RecipeBook | null, name: string | null): string | null {
  if (emptyName(name)) return null;
  const synonyms: Record<string, string> = book?.synonyms ?? {};
  return synonyms[name as string] ?? (name as string);
}

/**
 * "No machine needed". One word, the same one the engine knows as
 * `craft.HANDS` (D-216).
 *
 * There used to be a second such word in vault data, «Стройка», and this
 * function did not know it: eighteen recipes -- the workshop, the
 * administration, the bioprinter, the road surface -- were offered nowhere at
 * all, neither here as handwork nor in a node as a machine. The word is gone
 * from the data; the list stays a list of one so that the next such word has
 * to be added in both places at once.
 */
const BENCHLESS = ["by_hand"];

const emptyName = (name?: string | null) => name == null || BENCHLESS.includes(name);

/** What the player can make at this machine (`null` -- by hand). Ids out,
 *  ordered by their Russian display words -- the list feeds a picker. */
export function craftableAt(
  book: RecipeBook | null,
  machine: string | null,
  knows: string[],
  names: Names | null = null,
): string[] {
  if (!book) return [];

  //: A dish and a coin have their own door: the pot counts roles, the mint the
  //: fineness. The sign comes from vault data (`roles`, `kind`), not a name list.
  //: Everything is keyed by the stable id (D-251): `knows`, the wire and the
  //: commands all speak ids now, and `r.name` stays a display word.
  const special = new Set<string>(
    (book.recipes ?? [])
      .filter((r) => r.roles || r.kind === "money")
      .map((r) => r.id ?? r.name),
  );

  const recipes = (book.recipes ?? [])
    .filter(
      (r) =>
        canon(book, r.station ?? null) === machine &&
        !special.has(r.id ?? r.name) &&
        knows.includes(r.id ?? r.name),
    )
    .map((r) => r.id ?? r.name);

  //: Operations without a recipe everyone can do: smelting is the boundary
  //: between "mined" and "made", and locking it behind knowledge would stop the economy.
  const operations = (book.operations ?? [])
    .filter(
      (o) =>
        (o.consumes ?? []).length > 0 &&
        (o.requires ?? []).some((withWhat: string) => canon(book, withWhat) === machine),
    )
    .flatMap((o) => o.gives);

  return [...new Set([...recipes, ...operations])]
    .filter((name) => !special.has(name))
    .sort((a, b) => compare(goodsName(names, a), goodsName(names, b)));
}

/** At which machine this thing is made. Needed to repair at one's own machine. */
export function stationOf(book: RecipeBook | null, name: string): string | null {
  const recipe = (book?.recipes ?? []).find((r) => (r.id ?? r.name) === name);
  return recipe?.station ? canon(book, recipe.station) : null;
}

/**
 * What goes into making `name`, by canonical input names: the recipe's inputs,
 * or, for an operation output, what the operation consumes. Empty for what is
 * taken from the world (felling, mining) and for the unknown.
 */
export function inputsOf(book: RecipeBook | null, name: string, way?: string | null): string[] {
  if (!book) return [];
  const resolve = (n: string) => (book.synonyms?.[n] ?? n) as string;
  const recipe = (book.recipes ?? []).find((r) => (r.id ?? r.name) === name);
  if (recipe) return (recipe.inputs as string[]).map(resolve);
  //: `way` is the operation id (D-251); the name matches too for old callers.
  const operations = (book.operations ?? []).filter(
    (o) => (o.gives ?? []).includes(name) && (!way || (o.id ?? o.name) === way || o.name === way),
  );
  const consumes = operations[0]?.consumes ?? [];
  return (consumes as string[]).map(resolve);
}
