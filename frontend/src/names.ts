// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Russian display names for the wire's stable ids (D-251, wave II).
 *
 * The socket and the catalogs speak snake_case English ids now -- "iron_ore",
 * "pickaxe", "fine" -- and the player still reads Russian. The bridge is the
 * `/public/renames` bundle: id -> Russian word, one map per domain. Every
 * helper falls back to the raw id, because old data -- a stored flavor, a key
 * from a newer server -- must never crash a render (wave III brings the real
 * locale layer).
 *
 * The helpers take the bundle as an argument rather than reading a module
 * global: components reach it through `useNames()` (actions.tsx), pure code
 * takes a parameter, and a test passes a hand-built table.
 */

/** The `names_ru` half of `/public/renames`: id -> Russian, per domain. */
export type Names = {
  goods: Record<string, string>;
  classes: Record<string, string>;
  operations: Record<string, string>;
  slots: Record<string, string>;
  tiers: Record<string, string>;
  building_kinds: Record<string, string>;
  node_properties: Record<string, string>;
  planets: Record<string, string>;
  /** Crop cultures (D-057): a culture is not its produce -- «Полба» is sown,
   *  «Зерно» is harvested -- so it is a domain of its own. */
  plants: Record<string, string>;
  virtual_stations: Record<string, string>;
};

/**
 * What `/public/renames` answers with: one table per language.
 *
 * Every language at once, because the table is small, changes only when the
 * vault does, and a switch that had to go back to the server for words would
 * be a visible stutter for nothing.
 */
export type Renames = Record<`names_${string}`, Names | undefined>;

/**
 * The names of one language, or the default one where it has none.
 *
 * The vault refuses to build an incomplete overlay, so a half-translated
 * language cannot reach here -- but a language nobody has started can, and it
 * reads in Russian. That is the honest degradation: «Железная руда» to an
 * English reader is an untranslated name, while `iron_ore` in the middle of an
 * English sentence is a broken sentence.
 */
export function namesOf(renames: Renames | null, locale: string): Names | null {
  return renames?.[`names_${locale}`] ?? renames?.names_ru ?? null;
}

/** A goods (or recipe output, or virtual station) id in the player's words. */
export function goodsName(names: Names | null, id: string): string {
  return names?.goods?.[id] ?? names?.virtual_stations?.[id] ?? id;
}

/** A thing-class id ("pickaxe", "terminal") in the player's words. */
export function className(names: Names | null, id: string): string {
  return names?.classes?.[id] ?? id;
}

/** A quality tier id ("fine") in the player's words ("отличное"). */
export function tierName(names: Names | null, id: string): string {
  return names?.tiers?.[id] ?? id;
}

/** A gear slot id ("back") in the player's words ("спина"). */
export function slotName(names: Names | null, id: string): string {
  return names?.slots?.[id] ?? id;
}

/** A node property id ("woods", "aboard") in the player's words. */
export function propertyName(names: Names | null, id: string): string {
  return names?.node_properties?.[id] ?? id;
}

/** A building kind id ("wooden") in the player's words ("деревянный"). */
export function buildingKindName(names: Names | null, id: string): string {
  return names?.building_kinds?.[id] ?? id;
}

/** An operation id ("logging") in the player's words ("Рубка дерева"). */
export function operationName(names: Names | null, id: string): string {
  return names?.operations?.[id] ?? id;
}

/**
 * An operation requirement is either a thing class or a concrete goods
 * (the vault closes it with whichever fits), so the name is looked up in both.
 */
export function requirementName(names: Names | null, id: string): string {
  return names?.classes?.[id] ?? goodsName(names, id);
}

/** The separator of a carrier's counter key: "recorded_recipe: glass" (D-209). */
const KEY_SEP = ": ";

/**
 * The counter's goods key in the player's words. A written carrier is keyed
 * "recorded_recipe: glass" -- both halves are ids and both are translated;
 * everything else is a plain goods id.
 */
export function goodsKeyName(names: Names | null, key: string): string {
  const at = key.indexOf(KEY_SEP);
  if (at > 0) {
    const head = key.slice(0, at);
    const tail = key.slice(at + KEY_SEP.length);
    //: Only a key whose head the bundle knows is split: an old stored key
    //: ("Рецепт: Стекло") passes through verbatim.
    if (names?.goods?.[head]) {
      return `${goodsName(names, head)}${KEY_SEP}${goodsName(names, tail)}`;
    }
  }
  return goodsName(names, key);
}

/** What flavor tokens are joined by: "soup · beans" (D-128). */
const FLAVOR_SEP = " · ";

/**
 * A dish flavor for the player: each id token via the names, unknown tokens --
 * old stored Russian flavors included -- verbatim.
 */
export function flavorText(names: Names | null, flavor: string): string {
  return flavor
    .split(FLAVOR_SEP)
    .map((token) => goodsName(names, token))
    .join(FLAVOR_SEP);
}
