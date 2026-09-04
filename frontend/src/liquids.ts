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
import { weightOf } from "./arrange";
import { t } from "./locale";
import { goodsName, type Names } from "./names";

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

/**
 * How much of one liquid the vessels in a list hold, all told.
 *
 * With a `tier`, only that tier of it: a liquid has quality like anything
 * else, so one name in one pocket may be two positions -- the spirit of a good
 * batch and of a bad one -- and an order can name only one of them.
 *
 * The hands never hold a liquid loose (D-230), so nothing in `look.inventory`
 * is ever named for it: counting "how much water have I got" means opening
 * every canister. The market needs the number since D-255 -- it is what may be
 * poured into the terminal's tank -- and so does anybody else who has to offer
 * a liquid by amount.
 */
export function carried(things: readonly Thing[], goods: string, tier?: string): number {
  return poured(things, goods)
    .filter((one) => tier === undefined || one.tier === tier)
    .reduce((sum, one) => sum + one.amount, 0);
}

/**
 * Which quality tiers of one liquid the vessels hold.
 *
 * A liquid has quality like anything else -- a crafted spirit carries the
 * batch's, a drilled oil the vein's -- so "spirit" in the pocket is not one
 * position but as many as there are tiers of it. The market needs to know
 * which, to open a name at a tier that has something behind it.
 *
 * **In no order**, and deliberately not: the vessels arrive as an unordered
 * read (`world.contents`), so any order this could promise would be the heap's
 * and would change under the reader between two looks. Whoever needs one has
 * the quality ladder to sort by; this only says which tiers are there.
 */
export function tiersOf(things: readonly Thing[], goods: string): string[] {
  const seen = new Set(poured(things, goods).map((one) => one.tier));
  return [...seen];
}

/** Every pour of one liquid across the vessels, as the stacks the wire sends. */
function poured(things: readonly Thing[], goods: string): Thing[] {
  return things.flatMap((thing) =>
    (thing.content ?? []).filter((one) => (one.key ?? one.goods) === goods),
  );
}

/** What is poured into a vessel, in words: "Вода 12.0 · 2.4 из 20 кг". */
export function fill(book: RecipeBook | null, names: Names | null, thing: Thing): string {
  const inside = thing.content ?? [];
  //: `poured`, not `t`: the name `t` is the message lookup.
  const mass = inside.reduce((sum, poured) => sum + weightOf(poured), 0);
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
