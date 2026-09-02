// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Who somebody is, and how somebody comes to be.
 *
 * `Profile` is the account's own view of itself and `Card` is what the rest of
 * the world may see of it -- the pair is the privacy boundary, and keeping
 * them side by side is how that boundary stays visible when either grows a
 * field.
 *
 * The other half is the beginning: a `Line` is chosen, an `Enrollment` is
 * filed, and a body is printed at a `Door` -- afterwards at a `Printer`, which
 * is the same machine once there is an account to bill (D-013, D-033, D-182).
 * The client meets these before it has an identity at all, which is why they
 * are read from `/public/*` and not from `look`.
 */

/** Account panel (D-187): self-description next to the name. Nothing game-related here. */
export type Profile = {
  email?: string;
  /** The name is unique and unchangeable (D-011): reputation rests on it. */
  name: string;
  surname: string;
  age?: number;
  about: string;
  line: "human" | "nymph";
  since: string;
};

/** Somebody's card: self-description and citizenship, nothing of the body. */
export type Card = {
  name: string;
  surname: string;
  age?: number;
  about: string;
  line: "human" | "nymph";
  since: string;
  city?: string;
};

/** A character line on the selection screen: one is playable in the alpha (D-104). */
export type Line = {
  id: "human" | "nymph";
  name: string;
  world: string;
  playable: boolean;
  summary: string;
  traits: string[];
  /** How many play it: a living world is seen as a number. */
  players: number;
};

/** Registration request: four client steps -- one server command. */
export type Enrollment = {
  email: string;
  password: string;
  password_again: string;
  line: Line["id"];
  name: string;
  surname: string;
  age: number | null;
  about: string;
  node: string;
};

/** A door for a newcomer: where to print for the first time (D-013, D-182).
 *
 * Neither price nor term: the first body is printed at once and for free at
 * any door (D-040). The choice here is about people, not money.
 */
export type Door = {
  node: string;
  name: string;
  city: string | null;
  /** The city's word to newcomers: its promise, not a contract (D-183). Empty -- silent. */
  about: string;
  /** The sales tax, %. The one condition of the three left (D-184, D-281):
   *  citizenship is not a condition any more but what a city door gives, so
   *  `city` above already says it -- there is no key of its own for it. */
  tax: number;
  /** The Forerunners' Printer: an eternal machine, needs nobody's treasury. */
  precursor: boolean;
  citizens: number;
  /** Living bodies on the city's land now -- whom you will meet, not who is registered. */
  population: number;
  /** The settlement grant from the city charter, in minor units. Zero -- does not pay. */
  grant: number;
};

/** A door into the world: where to print a body and for how much (D-028, D-033). */
export type Printer = {
  node: string;
  name: string;
  city?: string;
  /** That very eternal printer: free, but twelve hours. */
  precursor: boolean;
  energy: number;
  iron: number;
  cost: number;
  minutes: number;
  /** What is on hand against what the print asks: iron in the node, energy in
   *  the city pool. The dead choose from the cloud and can look up neither. */
  iron_here: number;
  energy_here: number;
  /** The city prints at its own expense: code-law `body_print` (D-032). */
  at_city_expense: boolean;
};
