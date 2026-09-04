// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

import type { RecipeBook } from "./api";
import { t } from "./locale";

/** Helpers for the quantity field (`Amount.tsx`) and for reading a quantity.
 *  Kept apart from the component so hot reload keeps working: a module of
 *  components exports components. */

/** How much to move: what was typed, or the whole stack if nothing was. */
export function chosen(value: number | null, whole: number): number {
  return value === null ? whole : Math.min(value, whole);
}

/**
 * Piece or weight (D-212).
 *
 * The vault names what is **measured** -- ore, grain, water, oils -- and calls
 * everything else a piece: an ingot, a board, a nail, a coin. A piece is whole
 * always, and it reads as "12 шт.", the way mass reads in kilograms.
 *
 * The list lives here as one module value rather than a prop, and on purpose:
 * it comes from the same catalog the server reads, it is fetched once at
 * login and never changes while the page lives, and quantities are drawn in
 * a dozen panels -- half of which have no `book` prop and would gain one for
 * this alone. `learn` is called where the catalog is loaded (`App.tsx`).
 */
let measured = new Set<string>();
let aliases: Record<string, string> = {};
let units: Record<string, string> = {};

export function learn(book: RecipeBook | null): void {
  measured = new Set<string>(book?.bulk ?? []);
  aliases = book?.synonyms ?? {};
  units = book?.units ?? {};
}

/** What the vault says to draw next to the number: "шт", "м", "л". */
export function unit(goods: string): string {
  return units[aliases[goods] ?? goods] ?? "";
}

/** Whether the thing is counted in pieces rather than measured. */
export function counted(goods: string): boolean {
  return !measured.has(aliases[goods] ?? goods);
}

/**
 * A quantity as the player reads it: "12 шт." for a piece, "47.5" for what is
 * measured.
 *
 * A piece is whole by the engine's rule, so the number is never dressed up
 * here: were a fraction of one ever to show, it is a bug worth seeing.
 */
export function tally(goods: string, amount: number): string {
  //: The vault may name the unit itself -- wire is measured in metres, and
  //: "3 шт." of it would read as three coils. Unnamed, a piece stays "шт." and
  //: what is measured stays a bare number, as it always was.
  //: The vault's own unit word, not ours: it arrives with the catalog and is
  //: data, so it is joined to the number rather than looked up in a message.
  const named = unit(goods);
  if (named) return `${trim(amount)} ${named}`;
  //: `trim` already spells the number the way a column of them reads; handing
  //: it to Fluent as a number would reformat it by locale.
  return counted(goods) ? t("ui-amount-pieces", { amount: trim(amount) }) : trim(amount);
}

/**
 * A share of one as whole percent: "62%", and "<1%" where rounding would say
 * nothing. A find dealt one card in four hundred is rare, not impossible,
 * and "0%" next to a thing the land does give reads as a lie.
 */
export function percent(share: number): string {
  const whole = share * 100;
  if (whole > 0 && whole < 1) return "<1%";
  return `${Math.round(whole)}%`;
}

/** The number itself, without trailing zeros: 3, 3.5, 0.25. */
export function trim(amount: number): string {
  return Number(amount.toFixed(3)).toString();
}

/** The step of the quantity field: a piece moves by one, the measured by any part. */
export function step(goods: string): number | "any" {
  return counted(goods) ? 1 : "any";
}

//: Where a number stops being a number and becomes noise. Amounts live in
//: thousandths, so 2.9999999 ingots are three ingots, not four (`goods._DUST`).
const DUST = 1 / 1000;

/**
 * What a batch spends of one input at the recipe's norm (D-212).
 *
 * A counted thing gives itself to the work whole: a coin needs a tenth of an
 * iron ingot, and seven coins eat the ingot as entirely as ten do. What is
 * measured keeps its fraction, because a fraction of it is honest. The window
 * says this before the click, because the forecast must be the number the
 * batch runs on (D-092); the engine's half is `goods.whole(..., up=True)`.
 *
 * **The norm, not the bill.** Ordinary craft adds a waste share on top and
 * rounds that share to the nearest piece (`goods.spent`), and that arithmetic
 * belongs to the server, which the bench asks by `craft.plan`. This is for the
 * mint, whose work has no waste: its inputs are the composition itself.
 */
export function spends(goods: string, amount: number): number {
  return counted(goods) ? Math.ceil(amount - DUST) : amount;
}
