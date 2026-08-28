// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Money, in the one form the world keeps it: minor units.
 *
 * Its own module, and a small one, for a dull reason: `api.ts` reads `window`
 * as it loads, so anything that imports it needs a browser. Money arithmetic
 * needs nothing of the sort, and the modules that do it -- and the tests that
 * check them -- must not drag a DOM in behind them. `api` re-exports these
 * three, so every call site that ever knew them still does.
 */

/** Money comes in minor units: 1 TC = 10 000. Not a cent is lost. */
export const MONEY_SCALE = 10_000;

/** A sum for the eye: two decimals, trailing zeros trimmed. */
export const tk = (minor: number) => (minor / MONEY_SCALE).toFixed(2).replace(/\.?0+$/, "");

/** A typed sum back into minor units. */
export const minor = (tk: number) => Math.round(tk * MONEY_SCALE);
