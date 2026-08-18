/**
 * Grouping and sorting of the goods table (04-items, D-058).
 *
 * The inventory is a list of stacks, and one list reads badly three ways at
 * once: what do I have (by name), what is good (by tier), what is it for (by
 * kind). So the player picks the axis; the choice is kept across reloads.
 * Kept apart from the component so hot reload keeps working.
 */

import type { Thing } from "./api";

export type Grouping = "none" | "goods" | "tier" | "kind" | "maker";
export type Sorting = "name" | "quality" | "amount" | "mass" | "condition" | "spoils";

export const GROUPINGS: { id: Grouping; label: string }[] = [
  { id: "none", label: "без групп" },
  { id: "goods", label: "по предмету" },
  { id: "tier", label: "по качеству" },
  { id: "kind", label: "по типу" },
  { id: "maker", label: "по клейму" },
];

export const SORTINGS: { id: Sorting; label: string }[] = [
  { id: "name", label: "по названию" },
  { id: "quality", label: "по качеству" },
  { id: "amount", label: "по количеству" },
  { id: "mass", label: "по массе" },
  { id: "condition", label: "по состоянию" },
  { id: "spoils", label: "по годности" },
];

/** What kind of thing this is, in the player's words -- from vault data, not from the name. */
export function kindOf(book: any, thing: Thing): string {
  if (thing.recipe) return "носители";
  if (thing.fineness !== null) return "монеты";
  const recipe = (book?.recipes ?? []).find((r: any) => r.name === thing.goods);
  if (!recipe) return "сырьё";
  if (recipe.food) return "еда";
  const KIND: Record<string, string> = {
    station: "рабочие станции",
    furniture: "мебель",
    tool: "инструменты",
    gear: "снаряжение",
    vehicle: "транспорт",
    material: "материалы",
    consumable: "расходники",
    money: "монеты",
  };
  return KIND[recipe.kind] ?? "прочее";
}

/** The group a stack falls into under this axis. */
export function groupKey(book: any, thing: Thing, by: Grouping): string {
  switch (by) {
    case "goods":
      return thing.recipe ? `${thing.goods}: ${thing.recipe}` : thing.goods;
    case "tier":
      return thing.quality === null ? "без качества" : thing.tier;
    case "kind":
      return kindOf(book, thing);
    case "maker":
      return thing.maker ?? "без клейма";
    default:
      return "";
  }
}

const compare = (a: number | null, b: number | null) =>
  a === null && b === null ? 0 : a === null ? 1 : b === null ? -1 : a - b;

/** Sorted copy: `desc` flips the order but keeps "nothing" last either way. */
export function arrange(things: Thing[], by: Sorting, desc: boolean): Thing[] {
  const sign = desc ? -1 : 1;
  const key = (t: Thing): number | null => {
    switch (by) {
      case "quality":
        return t.quality;
      case "amount":
        return t.amount;
      case "mass":
        return t.mass * t.amount;
      case "condition":
        return t.condition;
      case "spoils":
        return t.spoils_at ? new Date(t.spoils_at).getTime() : null;
      default:
        return null;
    }
  };
  return [...things].sort((a, b) => {
    if (by === "name") {
      return sign * a.goods.localeCompare(b.goods, "ru") || (b.quality ?? -1) - (a.quality ?? -1);
    }
    const ka = key(a);
    const kb = key(b);
    if (ka === null && kb === null) return a.goods.localeCompare(b.goods, "ru");
    if (ka === null) return 1;
    if (kb === null) return -1;
    return sign * compare(ka, kb) || a.goods.localeCompare(b.goods, "ru");
  });
}

/** Group headers in a sensible order: tiers best first, everything else by name. */
export function orderGroups(keys: string[], by: Grouping, things: Thing[]): string[] {
  if (by === "tier") {
    const best = new Map<string, number>();
    for (const t of things) {
      const k = t.quality === null ? "без качества" : t.tier;
      best.set(k, Math.max(best.get(k) ?? -1, t.quality ?? -1));
    }
    return [...keys].sort((a, b) => (best.get(b) ?? -1) - (best.get(a) ?? -1));
  }
  return [...keys].sort((a, b) => a.localeCompare(b, "ru"));
}

const STORE = "octoverse.inventory.arrange";

/** The player's last choice of axes, if any. */
export function remembered(): { group: Grouping; sort: Sorting; desc: boolean } {
  try {
    const raw = localStorage.getItem(STORE);
    if (raw) return { group: "none", sort: "name", desc: false, ...JSON.parse(raw) };
  } catch {
    /* a browser without storage forgets, and that is fine */
  }
  return { group: "none", sort: "name", desc: false };
}

export function remember(choice: { group: Grouping; sort: Sorting; desc: boolean }): void {
  try {
    localStorage.setItem(STORE, JSON.stringify(choice));
  } catch {
    /* see above */
  }
}
