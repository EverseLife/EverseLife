// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What the market panel offers to trade in (D-003, D-230, D-232).
 *
 * The engine refuses the same three things when an order arrives
 * (`engine/market._tradable`), and it is the engine that decides -- whoever
 * sends an order need not be this screen (D-224). The list here is courtesy:
 * a picker that offers what the world will refuse wastes a click and reads
 * like a bug. Kept as a pure function, off the panel, so that it is testable
 * and so that the next rule is added in one place rather than in a component.
 */

import type { RecipeBook } from "./api";
import { isLiquid } from "./liquids";

/** One rung of the world's quality window, as `/public/quality/tiers` serves it. */
export type QualityTier = { from: number; to: number; name: string };

/** Everything that can stand in a book: made things and the world's own stuff.
 *
 *  What the Forerunners left is not in it (D-232): nobody makes those, takes
 *  them down or carries them away. Neither is a liquid (D-230): it exists
 *  inside a vessel, and a counter is not a vessel. An order for either is an
 *  order for a thing that cannot be delivered. */
export function catalogue(book: RecipeBook | null): string[] {
  if (!book) return [];
  const names = new Set<string>();
  for (const recipe of book.recipes) names.add(recipe.name);
  for (const material of book.materials) if (!material.relic) names.add(material.name);
  for (const name of [...names]) if (isLiquid(book, name)) names.delete(name);
  return [...names].sort((a, b) => a.localeCompare(b, "ru"));
}

/**
 * The floor a tier button means: the start of its band (D-239).
 *
 * A buy takes nothing worse than its floor, and pressing "хорошее" says
 * "no worse than 60" -- which is why the same button now also reaches the
 * lots above it.
 */
export function floorOf(tiers: QualityTier[], name: string): number {
  return Math.round(tiers.find((tier) => tier.name === name)?.from ?? 0);
}

/** Which tier a quality falls into: the last band that has started (D-058). */
export function tierOf(tiers: QualityTier[], quality: number): string | null {
  const started = [...tiers].sort((a, b) => a.from - b.from).filter((tier) => tier.from <= quality);
  return started.length > 0 ? started[started.length - 1].name : (tiers[0]?.name ?? null);
}
