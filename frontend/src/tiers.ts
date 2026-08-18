/** Helpers for the quality picker (`Tier.tsx`). Kept apart from the component
 *  so hot reload keeps working: a module of components exports components. */

import type { Thing } from "./api";

/** One tier of a thing in the hands: the name and how much of it there is. */
export type TierStock = { tier: string; amount: number; low: number; high: number };

/** What tiers of `goods` are in `things`, best first. */
export function tiersOf(things: Thing[], goods: string): TierStock[] {
  const found = new Map<string, TierStock>();
  for (const thing of things) {
    if (thing.goods !== goods || thing.quality === null) continue;
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

/** How the label reads: the tier, the amount and the quality span behind it. */
export function tierLabel(stock: TierStock): string {
  const span =
    stock.low === stock.high ? `${stock.low.toFixed(0)}` : `${stock.low.toFixed(0)}–${stock.high.toFixed(0)}`;
  return `${stock.tier} · ${stock.amount} · кач. ${span}`;
}

