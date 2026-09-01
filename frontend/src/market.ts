// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What the market panel counts and lists, apart from the panel itself.
 *
 * Three questions with exact answers -- how much money is this, what may be
 * ordered at all, and how much of the counter is still one's own to move --
 * and all three are the kind that break quietly: a price rounded to nothing,
 * a name the counter will never accept, a lot offered twice because an order
 * already holds it. They live here so that tests can ask them without a browser.
 */

import type { Order, RecipeBook, Thing } from "./api";
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
 * One kind is kept out, and for the reason an order for it could never be
 * filled while the money stood held until the term ran out: what the
 * Forerunners left (D-232) -- nobody makes it, takes it down or carries it
 * away.
 *
 * **A liquid is in** since D-255. It used to be out for a good reason -- a
 * liquid exists only inside a vessel (D-230), so no stack of one could lie on
 * a counter -- and the reason went away when the terminal grew a tank of its
 * own: the cells behind the counter are its inside, the seller pours out of
 * their canister and the buyer pours into theirs.
 */
export function catalogue(
  book: RecipeBook | null,
  names: Names | null = null,
  order: Compare = compare,
): string[] {
  if (!book) return [];
  const ids = new Set<string>();
  for (const recipe of book.recipes) ids.add(recipe.id ?? recipe.name);
  for (const material of book.materials) {
    if (!material.relic) ids.add(material.id ?? material.name);
  }
  //: Ids out, Russian order: the list is picked from by its display words.
  return [...ids].sort((a, b) => order(goodsName(names, a), goodsName(names, b)));
}

/** A position of the book: goods plus quality tier (D-058). */
export type Position = { goods: string; tier: string };

/**
 * What of the counter is free -- the shelf less one's own sell orders on it.
 *
 * An order commits a lot without moving it, so the stack lying in the terminal
 * is not what may be sold again or taken back: the engine counts the free part
 * (`market._free`) and refuses the rest. The count here is the same one, so
 * that the panel says it before the refusal does.
 *
 * By goods and tier -- the pair an order and a take both name, whatever
 * stacks the shelf splits it into -- and only over orders **in this node**,
 * which is why every order arrives with the node it stands in (D-225).
 */
export function freeOnCounter(
  //: The counter as the shelf holds it, under the counter's own name for
  //: each stack (`key ?? goods`, D-209) -- the name an order is written in.
  shelf: readonly { goods: string; tier: string; amount: number }[],
  orders: readonly Pick<Order, "side" | "node_key" | "goods" | "tier" | "left">[],
  node: string | undefined,
  goods: string,
  tier: string,
): number {
  const lying = shelf
    .filter((one) => one.goods === goods && one.tier === tier)
    .reduce((sum, one) => sum + one.amount, 0);
  const held = orders
    .filter(
      (one) =>
        one.side === "sell" && one.node_key === node && one.goods === goods && one.tier === tier,
    )
    .reduce((sum, one) => sum + one.left, 0);
  return Math.max(0, lying - held);
}

/**
 * The free part of each pair laid out over the stacks it lies in.
 *
 * A row of the terminal is a stack, an order is a pair of goods and tier, and
 * the free amount is the pair's. The first stack of a pair takes as much of
 * the free amount as it holds, the next what is left of it. The sum over a
 * pair's rows is then exactly what the engine would give -- two rows each
 * offering the whole remainder would have the button promise four and two
 * arrive.
 *
 * **In the engine's own order**, not the shelf's. `look.stall` arrives
 * unordered, so which of two stacks got the free share would change under the
 * player between rereads; and a take moves the worst quality first
 * (`market.counter._stacks`), so the shelf's own order could mark free the very
 * stack the engine will not touch. Worst quality first then, unqualified
 * before qualified, the id breaking ties -- the wire carries no `created_at`,
 * and any stable tiebreak will do.
 */
export function shareFree(
  shelf: readonly Pick<Thing, "id" | "key" | "goods" | "tier" | "amount" | "quality">[],
  free: (goods: string, tier: string) => number,
): Map<string, number> {
  const mine = new Map<string, number>();
  const left = new Map<string, number>();
  const worst = [...shelf].sort(
    (a, b) =>
      (a.quality ?? -Infinity) - (b.quality ?? -Infinity) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
  );
  for (const stack of worst) {
    const name = stack.key ?? stack.goods;
    const pair = `${name}|${stack.tier}`;
    const rest = left.get(pair) ?? free(name, stack.tier);
    const taken = Math.min(stack.amount, rest);
    mine.set(stack.id, taken);
    left.set(pair, rest - taken);
  }
  return mine;
}

/**
 * The tier to open a name at.
 *
 * What trades here wins: looking at "ore, excellent" and then searching out
 * bread must not land on "bread, excellent" -- the books are matched by tier
 * exactly (D-058), and an order in a tier nobody deals in would stand for ever.
 * The tier being looked at is kept only when this name is traded in it.
 *
 * `held` is what the hands hold of a name that is nowhere among the traded
 * positions and nowhere on the counter -- which is the case a liquid is always
 * in before it is poured (D-230, D-255): the water is inside a canister, so it
 * is in no list of stacks, and without this the panel would open it at
 * whatever tier the eye was last on. A liquid has quality like anything else
 * -- a crafted spirit carries its batch's, a drilled oil its vein's -- so
 * there is no band it belongs to by its kind, only the one it is actually in.
 *
 * Of several held tiers the **worst**, and sorted here rather than taken as
 * given: the vessels arrive unordered, so `held[0]` would be the heap's answer
 * and would change under the player between two looks. Worst first is the
 * engine's own habit with a pour that names no tier (`market.counter._stacks`).
 */
export function openAt(
  goods: string,
  near: readonly Position[],
  held: readonly string[],
  looking: Position | null,
  tiers: readonly QualityTier[],
): string {
  const here = near.filter((one) => one.goods === goods).map((one) => one.tier);
  if (looking && here.includes(looking.tier)) return looking.tier;
  //: A name the ladder does not know goes last rather than first: -1 would
  //: sort it ahead of every real band.
  const rung = (name: string) => {
    const at = tiers.findIndex((one) => one.name === name);
    return at < 0 ? tiers.length : at;
  };
  const worst = [...held].sort((a, b) => rung(a) - rung(b))[0];
  return here[0] ?? worst ?? looking?.tier ?? tiers[2]?.name ?? "common";
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
