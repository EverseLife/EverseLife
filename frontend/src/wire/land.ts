// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The two papers that come with a plot.
 *
 * A deed says the ground is yours (D-116); a holding says what keeping it
 * costs per meter period (D-149). Neither is about the place one is standing
 * in -- both are about places one owns, read from a list and shown far from
 * the node they name -- which is what separates them from `look`'s `node`
 * block, and what makes the pair one file.
 */

/** A deed for a plot: ownership documented (D-116). */
export type DeedView = {
  id: string;
  node?: string;
  name?: string;
  area?: number;
  owner?: string;
  /** The issue price: the purchase price, zero for taken wild land. */
  paid: number;
  /** Listed for sale: the price and the addressee, if the contract is addressed. */
  sale_price?: number;
  sale_to?: string;
  issued_at: string;
};

/** Own node and the household bill (D-149). */
export type Holding = {
  node: string;
  name: string;
  area: number;
  /** Whether there is a city grid: outside a city there are no bills at all. */
  grid: boolean;
  energy_per_period: number;
  cost_per_period: number;
  debt: number;
  cut_off: boolean;
  last_energy: number;
};
