// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Ledger grounds in words.
 *
 * Every coin the engine moves is written down with a ground, and the ground is
 * an enum: `tax_land`, `court_fee`, `escrow_hold`. Two screens show those to a
 * player -- the personal statement and the city treasury by article -- and both
 * are read by people, not by the engine. The dictionary lived beside the
 * statement alone, so the treasury printed `court_fee 20.00, duty 79.02` in the
 * middle of a page otherwise in Russian.
 *
 * An unknown ground falls through as itself: a server newer than the client is
 * better read half in enum than not shown at all.
 */

const GROUND: Record<string, string> = {
  genesis: "эмиссия",
  trade: "сделка",
  tax_trade: "налог с продажи",
  market_fee: "сбор рынка",
  duty: "пошлина",
  salary: "жалованье",
  //: The daily land tax (D-127). It took the place of `upkeep`, which went
  //: with its own mechanic (D-219) -- and the word stayed behind here while
  //: the ground itself changed, so the one line every landholder sees every
  //: day read `tax_land` in the middle of a statement in Russian.
  tax_land: "земельный налог",
  energy_bill: "энергия",
  court_fee: "пошлина суда",
  fine: "штраф",
  escrow_hold: "задаток",
  escrow_release: "возврат задатка",
  loan: "кредит",
  loan_repayment: "погашение",
  seigniorage: "сеньораж",
  bank_margin: "маржа города",
  transfer: "перевод",
};

/** The ground as a person reads it; an unknown one stays as it came. */
export function groundName(ground: string): string {
  return GROUND[ground] ?? ground;
}
