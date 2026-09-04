// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What a coin is made of, read off the vault (D-016, D-086, D-090).
 *
 * A money recipe is a coin: its heavier input is the refined metal, the
 * lighter one the alloy. A third coin or other amounts are data, not a client
 * change -- which is why nothing here is written down as a number.
 *
 * What the batch costs is not counted here: `amounts.spends` says it, because
 * the rule is the world's and not the coin's -- a counted input goes into the
 * work whole (D-212), so the iron ingot is spent entire whatever the count.
 */

import type { RecipeBook } from "./api";

export type Coin = {
  coin: string;
  /** The refined metal: the input the coin is mostly made of. */
  metal: string;
  /** The alloy: the other input, a tenth of iron. */
  alloy: string;
  metalPerCoin: number;
  alloyPerCoin: number;
};

/** The coins the world knows, with their composition. */
export function coinsOf(book: RecipeBook | null): Coin[] {
  return (book?.recipes ?? [])
    .filter((r) => r.kind === "money")
    .map((r) => {
      const parts = Object.entries(r.amounts ?? {}) as [string, number][];
      parts.sort((a, b) => b[1] - a[1]);
      const [metal, metalPerCoin] = parts[0] ?? ["", 0];
      const [alloy, alloyPerCoin] = parts[1] ?? ["", 0];
      //: Ids throughout (D-251): the coin is what `knows` and `coin.mint` name.
      return { coin: r.id ?? r.name, metal, alloy, metalPerCoin, alloyPerCoin };
    });
}
