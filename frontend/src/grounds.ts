// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Ledger grounds in words.
 *
 * Every coin the engine moves is written down with a ground, and the ground is
 * an enum: `tax_land`, `court_fee`, `escrow_hold`. Two screens show those to a
 * player -- the personal statement and the city treasury by article -- and both
 * are read by people, not by the engine.
 *
 * The words used to live here, in a dictionary of their own, and that was the
 * second place they had drifted from: `upkeep` gave way to `tax_land` (D-127,
 * D-219) and the word stayed behind under the old key, so every landholder read
 * `tax_land` once a day in the middle of a page in Russian. Now they come from
 * the same locale as everything else the server says (D-251), and the backend's
 * own suite checks the list against `PostingReason` -- a ground added without a
 * word fails there rather than reaching this screen.
 *
 * An unknown ground still falls through as itself: a server newer than the
 * client is better read half in enum than not shown at all.
 */

import { spoken, t } from "./locale";

/** The ground as a person reads it; an unknown one stays as it came. */
export function groundName(ground: string): string {
  const key = `ledger-ground-${ground}`;
  return spoken().has(key) ? t(key) : ground;
}
