// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Grouping and sorting of the goods table (04-items, D-058).
 *
 * The inventory is a list of stacks, and one list reads badly three ways at
 * once: what do I have (by name), what is good (by tier), what is it for (by
 * kind). So the player picks the axis; the choice is kept across reloads.
 * Kept apart from the component so hot reload keeps working.
 */

import type { Thing } from "./api";
import type { RecipeBook } from "./api";
import { json, keep, kept } from "./kept";
import { compare, t } from "./locale";
import { goodsName, tierName, type Names } from "./names";

export type Grouping = "none" | "goods" | "tier" | "kind" | "maker";
export type Sorting = "name" | "quality" | "amount" | "mass" | "condition" | "spoils";

/**
 * The axes, and their words.
 *
 * `label` is a getter on purpose: these two lists are built once, when the
 * module is first imported, and the language can be switched long afterwards.
 * A plain string would be the word of whichever language happened to be
 * spoken at that first import, frozen for the rest of the session.
 */
export const GROUPINGS: { id: Grouping; label: string }[] = [
  { id: "none", get label() { return t("ui-arrange-group-none"); } },
  { id: "goods", get label() { return t("ui-arrange-group-goods"); } },
  { id: "tier", get label() { return t("ui-arrange-group-tier"); } },
  { id: "kind", get label() { return t("ui-arrange-group-kind"); } },
  { id: "maker", get label() { return t("ui-arrange-group-maker"); } },
];

export const SORTINGS: { id: Sorting; label: string }[] = [
  { id: "name", get label() { return t("ui-arrange-sort-name"); } },
  { id: "quality", get label() { return t("ui-arrange-sort-quality"); } },
  { id: "amount", get label() { return t("ui-arrange-sort-amount"); } },
  { id: "mass", get label() { return t("ui-arrange-sort-mass"); } },
  { id: "condition", get label() { return t("ui-arrange-sort-condition"); } },
  { id: "spoils", get label() { return t("ui-arrange-sort-spoils"); } },
];

/**
 * The message that names each recipe kind (D-090). Keyed by the wire's own
 * `kind`, so the map is a lookup and never a sentence; `money` and a coin's
 * `fineness` share one word, and so share one message.
 */
const KIND_WORDS: Record<string, string> = {
  station: "ui-arrange-kind-station",
  furniture: "ui-arrange-kind-furniture",
  tool: "ui-arrange-kind-tool",
  gear: "ui-arrange-kind-gear",
  vehicle: "ui-arrange-kind-vehicle",
  material: "ui-arrange-kind-material",
  consumable: "ui-arrange-kind-consumable",
  money: "ui-arrange-kind-coins",
};

/**
 * What kind of thing this is, as a key -- from vault data, not from the name.
 *
 * Split from the word below because the two are asked for different things:
 * the word is read, the key is stored and compared. `kindOf` spells the same
 * answer in the player's language.
 */
export function kindKey(book: RecipeBook | null, thing: Thing): string {
  if (thing.recipe) return "carriers";
  if (thing.fineness != null) return "money";
  const recipe = (book?.recipes ?? []).find((r) => (r.id ?? r.name) === thing.goods);
  if (!recipe) return "raw";
  if (recipe.food) return "food";
  return KIND_WORDS[recipe.kind] ? recipe.kind : "other";
}

/** The same answer in the player's words, for the group's header. */
export function kindOf(book: RecipeBook | null, thing: Thing): string {
  const key = kindKey(book, thing);
  if (key === "carriers") return t("ui-arrange-kind-carriers");
  if (key === "money") return t("ui-arrange-kind-coins");
  if (key === "raw") return t("ui-arrange-kind-raw");
  if (key === "food") return t("ui-arrange-kind-food");
  return t(KIND_WORDS[key] ?? "ui-arrange-kind-other");
}

/** The group a stack falls into under this axis. The key doubles as the
 *  group's title, so it is spelled in the player's words, not in ids. */
export function groupKey(
  book: RecipeBook | null,
  names: Names | null,
  thing: Thing,
  by: Grouping,
): string {
  switch (by) {
    case "goods":
      return thing.recipe
        ? `${goodsName(names, thing.goods)}: ${goodsName(names, thing.recipe)}`
        : goodsName(names, thing.goods);
    case "tier":
      return thing.quality == null ? t("ui-arrange-no-tier") : tierName(names, thing.tier);
    case "kind":
      return kindOf(book, thing);
    case "maker":
      return thing.maker ?? t("ui-arrange-no-maker");
    default:
      return "";
  }
}

/**
 * The same group, spelled in ids (D-251).
 *
 * `groupKey` answers in the player's language, which makes it a **title** and
 * nothing more: written down as the identity of a group -- in storage, in a
 * set of what is unfolded -- it would be a Russian literal in the role of an
 * identifier, and the fold would come undone the moment the player switched
 * language. Nothing falls over; only the player would ever see it. So what is
 * stored is this, and what is read is the other.
 *
 * `maker` is the exception and cannot be helped: the wire carries a maker as
 * a name, and there is no id under it to reach for.
 */
export function groupId(
  book: RecipeBook | null,
  thing: Thing,
  by: Grouping,
): string {
  switch (by) {
    case "goods":
      return thing.recipe ? `${thing.goods}:${thing.recipe}` : thing.goods;
    case "tier":
      return thing.quality == null ? "" : (thing.tier ?? "");
    case "kind":
      return kindKey(book, thing);
    case "maker":
      return thing.maker ?? "";
    default:
      return "";
  }
}

/** Two numbers, "nothing" last. Named apart from `compare`, which orders words. */
const order = (a: number | undefined, b: number | undefined) =>
  a == null && b == null ? 0 : a == null ? 1 : b == null ? -1 : a - b;

/** Sorted copy: `desc` flips the order but keeps "nothing" last either way.
 *  Names sort by the display word in the player's language, not the wire id
 *  (D-251). */
export function arrange(
  things: Thing[],
  by: Sorting,
  desc: boolean,
  names: Names | null = null,
): Thing[] {
  const sign = desc ? -1 : 1;
  //: `thing`, not `t`: the name `t` belongs to the message lookup now.
  const word = (thing: Thing) => goodsName(names, thing.goods);
  const key = (thing: Thing): number | undefined => {
    switch (by) {
      case "quality":
        return thing.quality;
      case "amount":
        return thing.amount;
      case "mass":
        return thing.mass * thing.amount;
      case "condition":
        return thing.condition;
      case "spoils":
        return thing.spoils_at ? new Date(thing.spoils_at).getTime() : undefined;
      default:
        return undefined;
    }
  };
  return [...things].sort((a, b) => {
    if (by === "name") {
      return sign * compare(word(a), word(b)) || (b.quality ?? -1) - (a.quality ?? -1);
    }
    const ka = key(a);
    const kb = key(b);
    if (ka == null && kb == null) return compare(word(a), word(b));
    if (ka == null) return 1;
    if (kb == null) return -1;
    return sign * order(ka, kb) || compare(word(a), word(b));
  });
}

/** What a group says with its rows still hidden. */
export type Summary = {
  /** The one goods the group holds, if it holds only one -- then a total can be named. */
  goods: string | null;
  /** How much there is in all: a number only worth showing when `goods` is one. */
  amount: number;
  /** Average quality by amount, and nothing where the goods have no quality. */
  quality: number | null;
  mass: number;
};

/**
 * The group folded into one line.
 *
 * The average is **weighted by amount**, not by row: a hundred kilos of poor
 * ore and one kilo of good ore average out near the poor, because that is what
 * the pile is. The unweighted mean would say fifty and be a different pile.
 *
 * A total quantity is named only where every row is the same goods: pieces and
 * kilograms do not add up, and "17" over nails and ore would mean nothing.
 * Mass is the one number that always adds up, so it is always there.
 */
export function summarize(rows: Thing[]): Summary {
  const kinds = new Set(rows.map((row) => row.key));
  let weight = 0;
  let weighed = 0;
  for (const row of rows) {
    if (row.quality == null) continue;
    weight += row.amount;
    weighed += row.quality * row.amount;
  }
  return {
    goods: kinds.size === 1 && rows.length > 0 ? rows[0].goods : null,
    amount: rows.reduce((sum, row) => sum + row.amount, 0),
    quality: weight > 0 ? weighed / weight : null,
    mass: rows.reduce((sum, row) => sum + row.mass * row.amount, 0),
  };
}

/** Group headers in a sensible order: tiers best first, everything else by name. */
export function orderGroups(
  keys: string[],
  by: Grouping,
  things: Thing[],
  names: Names | null = null,
): string[] {
  if (by === "tier") {
    const best = new Map<string, number>();
    //: Named `thing`, not `t`: `t` is the message the header is drawn from.
    for (const thing of things) {
      //: The same spelling `groupKey` gave the header, or the orders miss.
      const k = thing.quality == null ? t("ui-arrange-no-tier") : tierName(names, thing.tier);
      best.set(k, Math.max(best.get(k) ?? -1, thing.quality ?? -1));
    }
    return [...keys].sort((a, b) => (best.get(b) ?? -1) - (best.get(a) ?? -1));
  }
  return [...keys].sort(compare);
}

const STORE = "everselife.inventory.arrange";

export type Axes = { group: Grouping; sort: Sorting; desc: boolean };

const PLAIN: Axes = { group: "none", sort: "name", desc: false };

//: Every field is checked against the word lists above rather than merely
//: spread over the defaults: an axis renamed or dropped in a later build would
//: otherwise come back out of storage as a grouping nothing groups by.
const AXES = json<Axes>((value) => {
  if (value === null || typeof value !== "object") return null;
  const said = value as Record<string, unknown>;
  return {
    group: GROUPINGS.find((axis) => axis.id === said.group)?.id ?? PLAIN.group,
    sort: SORTINGS.find((axis) => axis.id === said.sort)?.id ?? PLAIN.sort,
    desc: said.desc === true,
  };
});

/** The player's last choice of axes, if any. */
export function remembered(): Axes {
  return kept(STORE, PLAIN, AXES);
}

export function remember(choice: Axes): void {
  keep(STORE, choice, AXES);
}
