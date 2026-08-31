// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** Helpers for the quality picker (`Tier.tsx`). Kept apart from the component
 *  so hot reload keeps working: a module of components exports components. */

import type { Thing } from "./api";
import { t } from "./locale";
import { tierName, type NamesRu } from "./names";

/** One tier of a thing in the hands: the name and how much of it there is. */
export type TierStock = { tier: string; amount: number; low: number; high: number };

/**
 * Everything at hand, with what sits inside a vessel unpacked alongside it.
 *
 * A liquid is never a stack of its own (D-230): it lies in a canister, as that
 * canister's `content`. The engine reaches into the vessels when it gathers a
 * batch's materials (`liquid.reach`), so whoever asks what is at hand must
 * reach the same way -- and both questions below ask it, which is why the
 * unpacking lives here rather than in one of them.
 */
function reach(things: Thing[]): Thing[] {
  return things.flatMap((thing) => [thing, ...reach(thing.content ?? [])]);
}

/**
 * How much of `goods` is at hand, vessels included.
 *
 * Deliberately not `tiersOf(...).reduce(...)`: a tier is a band of quality, and
 * plenty of matter has none at all -- seeds carry a cultivar instead, and so
 * does raw material bought rather than made. Summed over tiers, all of that
 * counted as zero, so the bench told a master holding ten of a thing that they
 * had none of it -- while the batch, which takes unqualified stacks like any
 * other, started perfectly well.
 */
export function stockOf(things: Thing[], goods: string): number {
  return reach(things).reduce(
    (sum, thing) => (thing.goods === goods ? sum + thing.amount : sum),
    0,
  );
}

/** What tiers of `goods` are at hand, best first. Only qualified stacks have a
 *  tier at all -- what has no quality is not a band of it (D-058) -- and the
 *  vessels are looked into, because a liquid keeps its quality when it is
 *  poured (`liquid.settle`) and the engine honours the tier chosen for it. */
export function tiersOf(things: Thing[], goods: string): TierStock[] {
  const found = new Map<string, TierStock>();
  for (const thing of reach(things)) {
    if (thing.goods !== goods || thing.quality == null) continue;
    const have = found.get(thing.tier);
    if (have) {
      have.amount += thing.amount;
      have.low = Math.min(have.low, thing.quality);
      have.high = Math.max(have.high, thing.quality);
    } else {
      found.set(thing.tier, {
        tier: thing.tier,
        amount: thing.amount,
        low: thing.quality,
        high: thing.quality,
      });
    }
  }
  return [...found.values()].sort((a, b) => b.high - a.high);
}

/** How the label reads: the tier in the player's words, the amount and the
 *  quality span behind it. `stock.tier` itself stays the wire's id (D-251). */
export function tierLabel(stock: TierStock, names: NamesRu | null = null): string {
  const span =
    stock.low === stock.high ? `${stock.low.toFixed(0)}` : `${stock.low.toFixed(0)}–${stock.high.toFixed(0)}`;
  //: The numbers go in as strings: Fluent formats a real number through Intl
  //: for the locale, and a quality of 1200 would come back spaced.
  return t("ui-tier-stock", {
    tier: tierName(names, stock.tier),
    amount: String(stock.amount),
    span,
  });
}

