// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Which glyph a thing or a node wears (D-238, amendment 1).
 *
 * Marks are given to **classes**, never to single goods: the catalog already
 * says what a thing is -- a tool class, a recipe kind (D-090), a material
 * class -- and the mark follows that, so a new item costs no new drawing and
 * no new rule. A word the catalog does not know falls back to the plain
 * crate: an honest "goods", never a wrong picture.
 */

import type { RecipeBook } from "./api";
import { classOf } from "./classes";
import type { GlyphName } from "./glyphs";
import { t } from "./locale";

/** Tool classes (by class id, D-251) with a face of their own; the rest wear the plain hammer. */
const TOOL_MARKS: Record<string, GlyphName> = {
  pickaxe: "pick",
  axe: "axe",
  vessel: "vessel",
  cookware: "pot",
};

/** Material classes, by class id. */
const MATERIAL_MARKS: Record<string, GlyphName> = {
  minable: "ore",
  mineral: "ore",
  metal: "ingot",
  flora: "plant",
  liquid: "water",
};

//: The Forerunners' machinery is classed by what it is (a reactor, a yard),
//: but on a shelf it is a station like any other.
const RELIC_STATIONS = new Set(["heat_plant", "precursor_reactor", "shipyard", "bioprinter"]);

const KIND_MARKS: Record<string, GlyphName> = {
  station: "station",
  tool: "tool",
  gear: "gear",
  vehicle: "vehicle",
  furniture: "furniture",
  money: "money",
};

/** The class mark of one goods name. */
export function goodsGlyph(book: RecipeBook | null, goods: string): GlyphName {
  if (!book) return "goods";
  const name = book.synonyms?.[goods] ?? goods;
  if (book.liquid?.includes(name)) return "water";
  for (const [toolClass, members] of Object.entries(book.tool_classes ?? {})) {
    if (members.includes(name)) return TOOL_MARKS[toolClass] ?? "tool";
  }
  const recipe = book.recipes.find((r) => (r.id ?? r.name) === name);
  if (recipe) {
    if (recipe.kind === "consumable") return recipe.food ? "food" : "goods";
    const byKind = KIND_MARKS[recipe.kind];
    if (byKind) return byKind;
  }
  const material = book.materials?.find((m) => (m.id ?? m.name) === name);
  const thingClass = material?.class ?? classOf(book, name);
  if (thingClass) {
    const byClass = MATERIAL_MARKS[thingClass];
    if (byClass) return byClass;
    if (RELIC_STATIONS.has(thingClass)) return "station";
  }
  return "goods";
}

/**
 * The marks an owner may nail on a node (D-238): the engine's closed list
 * (`estate.EMBLEMS`), word for word. The world's own signs -- the
 * Forerunners, a settlement -- are deliberately not offered: the map must
 * not be forgeable.
 */
export const EMBLEM_MARKS: Record<string, GlyphName> = {
  house: "estate",
  field: "plant",
  woods: "forest",
  meadow: "glade",
  stones: "ore",
  workshop: "station",
  market: "market",
  warehouse: "goods",
  food: "food",
  water: "water",
  markup: "plot",
};

/**
 * The emblem ids in the player's words. The renames bundle does not carry
 * this domain -- half the marks ("house", "market") are neither goods nor node
 * properties -- so the picker's labels live beside the glyph list they label.
 *
 * Each word is a getter: the map is built once, when the module is first
 * imported, and the language is switched long after that. Read as a plain
 * table by whoever draws it -- `EMBLEM_WORDS[mark] ?? mark` still falls back
 * to the id for a mark a newer server invented.
 */
export const EMBLEM_WORDS: Record<string, string> = {
  get house() { return t("ui-emblem-house"); },
  get field() { return t("ui-emblem-field"); },
  get woods() { return t("ui-emblem-woods"); },
  get meadow() { return t("ui-emblem-meadow"); },
  get stones() { return t("ui-emblem-stones"); },
  get workshop() { return t("ui-emblem-workshop"); },
  get market() { return t("ui-emblem-market"); },
  get warehouse() { return t("ui-emblem-warehouse"); },
  get food() { return t("ui-emblem-food"); },
  get water() { return t("ui-emblem-water"); },
  get markup() { return t("ui-emblem-markup"); },
};

/**
 * The mark of a ledger ground (D-238): the statement's articles sorted at a
 * glance. Person-to-person transfers wear the person; what the state takes
 * or pays wears the colonnade; the market's own -- the scales.
 */
const GROUND_MARKS: Record<string, GlyphName> = {
  genesis: "money",
  trade: "market",
  tax_trade: "state",
  market_fee: "market",
  duty: "state",
  salary: "state",
  tax_land: "state",
  energy_bill: "estate",
  court_fee: "state",
  fine: "state",
  escrow_hold: "market",
  escrow_release: "market",
  loan: "money",
  loan_repayment: "money",
  seigniorage: "money",
  bank_margin: "money",
  transfer: "me",
};

export function groundGlyph(ground: string): GlyphName {
  return GROUND_MARKS[ground] ?? "money";
}

type NodeFace = {
  /** The owner's nailed mark, if any: their word beats the land's signs. */
  emblem?: string | null;
  features?: readonly string[];
  /** A settlement (the map's group node): the colonnade. */
  settlement?: boolean;
  /** The city's spaceport door. */
  port?: boolean;
};

/**
 * The type mark of a map node: the owner's emblem first, then the place
 * signs (the rarer resource outranks the woods grown over it), then what
 * the map itself knows. `null` -- a bare circle.
 */
export function nodeGlyph({ emblem, features, settlement, port }: NodeFace): GlyphName | null {
  if (emblem && EMBLEM_MARKS[emblem]) return EMBLEM_MARKS[emblem];
  const signs = new Set(features ?? []);
  if (signs.has("precursors")) return "ruins";
  if (signs.has("stones")) return "ore";
  if (signs.has("woods")) return "forest";
  if (signs.has("meadow")) return "glade";
  if (signs.has("plot")) return "plot";
  if (settlement) return "state";
  if (port) return "port";
  return null;
}
