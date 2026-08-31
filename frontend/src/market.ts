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
import { compare, type Compare } from "./locale";
import { MONEY_SCALE } from "./money";
import { goodsName, type Names } from "./names";

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
export function catalogue(
  book: RecipeBook | null,
  names: Names | null = null,
  order: Compare = compare,
): string[] {
  if (!book) return [];
  const liquid = new Set(book.liquid ?? []);
  const ids = new Set<string>();
  for (const recipe of book.recipes) {
    const id = recipe.id ?? recipe.name;
    if (!liquid.has(id)) ids.add(id);
  }
  for (const material of book.materials) {
    const id = material.id ?? material.name;
    if (!material.relic && !liquid.has(id)) ids.add(id);
  }
  //: Ids out, Russian order: the list is picked from by its display words.
  return [...ids].sort((a, b) => order(goodsName(names, a), goodsName(names, b)));
}

/** One rung of the world's quality window, as `/public/quality/tiers` serves it. */
export type QualityTier = { from: number; to: number; name: string };

/**
 * The floor a tier button means: the start of its band (D-239).
 *
 * A buy takes nothing worse than its floor, and pressing a tier says
 * "no worse than its start" -- which is why the same button now also
 * reaches the lots above it.
 */
export function floorOf(tiers: QualityTier[], name: string): number {
  return Math.round(tiers.find((tier) => tier.name === name)?.from ?? 0);
}

/** Which tier a quality falls into: the last band that has started (D-058). */
export function tierOf(tiers: QualityTier[], quality: number): string | null {
  const started = [...tiers].sort((a, b) => a.from - b.from).filter((tier) => tier.from <= quality);
  return started.length > 0 ? started[started.length - 1].name : (tiers[0]?.name ?? null);
}
