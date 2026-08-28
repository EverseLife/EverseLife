// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What the market panel counts and lists, apart from the panel itself.
 *
 * Two questions with exact answers -- how much money is this, and what may be
 * ordered at all -- and both are the kind that break quietly: a price rounded
 * to nothing, a name the counter will never accept. They live here so that
 * tests can ask them without a browser.
 */

import type { RecipeBook } from "./api";
import { MONEY_SCALE } from "./money";

/**
 * A sum of money, to the last minor unit.
 *
 * `api.tk` rounds to the coin's two decimals, which is right for a price in a
 * table and wrong under an order: a bid of one minor unit read "0 ₭" beside a
 * live button, and a zero beside a live button is a lie, not brevity.
 */
export function coins(minor: number): string {
  return (minor / MONEY_SCALE).toFixed(4).replace(/\.?0+$/, "") || "0";
}

/** A quantity without lies: fractional is shown fractional, whole -- whole. */
export function exactly(qty: number): string {
  return qty.toFixed(3).replace(/\.?0+$/, "") || "0";
}

/**
 * Everything an order may name: made things and the world's own stuff.
 *
 * Two kinds are kept out, and for the same reason -- an order for them could
 * never be filled, and the money would stand held until the order expires:
 *
 * - what the Forerunners left (D-232): nobody makes it, takes it down or
 *   carries it away;
 * - liquids (D-230): they exist only inside a vessel, so no stack of one can
 *   ever be laid on the counter.
 */
export function catalogue(book: RecipeBook | null): string[] {
  if (!book) return [];
  const liquid = new Set(book.liquid ?? []);
  const names = new Set<string>();
  for (const recipe of book.recipes) if (!liquid.has(recipe.name)) names.add(recipe.name);
  for (const material of book.materials) {
    if (!material.relic && !liquid.has(material.name)) names.add(material.name);
  }
  return [...names].sort((a, b) => a.localeCompare(b, "ru"));
}
