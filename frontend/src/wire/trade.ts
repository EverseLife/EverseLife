// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Buying and selling.
 *
 * An order stands in one node's book, a book is what the orders look like from
 * outside, and a reservation is the one way to buy where one is not standing
 * -- a deposit against a term (D-047). Prices are public to everybody (D-047),
 * which is why the book is a `/public/*` read and not something `look` carries.
 */

export type Order = {
  id: string;
  side: "buy" | "sell";
  goods: string;
  tier: string;
  /**
   * A buy's own quality floor (D-239) -- only when named by hand inside the
   * band: the band's start is derivable from the tier (D-225), and a sell has
   * no floor at all. In both of those cases the key is absent.
   */
  min_quality?: number;
  price: number;
  left: number;
};

/** Reservation: the only way to buy remotely -- with a deposit and a term (D-047). */
export type Reservation = {
  id: string;
  goods: string;
  tier: string;
  amount: number;
  price: number;
  deposit: number;
  node: string;
  node_key: string;
  /** When it was taken -- the other end of the term for the deadline bar. */
  placed_at: string;
  expires_at: string;
};

export type Level = { price: number; amount: number };

export type Book = {
  node: string;
  type_key: string;
  tier: string;
  bids: Level[];
  asks: Level[];
  last: number | null;
  spread: number | null;
  /** The price step the rows are glued at, minor units. One -- every price its own row. */
  step: number;
};
