// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What the body is at, and what the world is doing to it meanwhile.
 *
 * One body does one thing (D-211), and `Doing` is the server's word for
 * whichever thing that is; `Foraging`, `Sight` and `Outlook` are the
 * occupations detailed enough that the window showing one needs more than a
 * title and a deadline.
 *
 * `Frost` and `Air` sit with them because they are the same kind of statement
 * told the other way round: a level, a rate and a stamp, with the client
 * counting the hand from there rather than being told a number every second
 * (D-226). What the body spends and what it survives on are read together or
 * not at all.
 */

/**
 * One occupation of the body (D-211): the road, the field, sleep, a search, a
 * plot under the plough, a batch, a working face.
 *
 * `kind` is a stable id -- "sleep", "forage", "plot" -- and the client decides
 * by it what to draw and what button ends it; `title` and `what` are the
 * server's words for the same thing, and they may change without notice.
 */
export type Doing = {
  kind: string;
  title: string;
  what: string;
  until?: string;
};

/** The foraging window: the plot's empty land, the search and its find (D-210). */
export type Foraging = {
  /** Empty land, m2: the plot minus the building footprint. */
  area: number;
  /** Below this much there is nowhere to forage. */
  min_area: number;
  /** Whether a new search may start here: own or nobody's land with room. */
  allowed: boolean;
  /** The mean length of one search here, seconds; empty if nothing is found here at all. */
  seconds?: number;
  /** What one search costs in stamina, found or passed. */
  stamina: number;
  /** What the land gives at all and how often, by share; the handful per find. */
  finds: { goods: string; share: number; units: number }[];
  /** No search; a search under way; a find waiting for the decision. */
  state: "idle" | "searching" | "found";
  started_at?: string;
  ready_at?: string;
  found?: { goods: string; units: number; quality: number; mass: number };
};

/** Everything the player sees about the face. Roof stability is not here and cannot be. */
export type Sight = {
  sign: string;
  mined: number;
  swings: number;
  timbers: number;
  stamina: number;
  pace: "steady" | "fast";
  state: "active" | "left" | "collapsed";
  session: string;
};

/** What an exploration run from here will cost (D-156).
 *
 * The price is a property of the place, not the player: untrodden
 * surroundings give a find in minutes, trodden ones in hours and not always.
 * Shown before leaving, otherwise it reads as engine randomness. */
export type Outlook = {
  /** How many finds have already been made from this node. */
  explored: number;
  minutes: { min: number; max: number };
  /** The largest stamina price -- by the longest run. */
  stamina: number;
  /** Chance with the requested species in mind: the rare is found worse (D-151). */
  chance: number;
  /** By how much the species request narrowed the chance; 1 -- no request. */
  aim?: number;
  /**
   * By how much the crowding of the graph narrowed it; 1 -- roomy here (D-207).
   * Edges pile up where everybody wants to be, and a crowded place searches worse.
   */
  crowding?: number;
  /** The node a find will hang on, when it is not this one: from a city, the gate. */
  anchor?: string;
  /** Which species is requested, if any. */
  resource?: string;
};

/**
 * The heat reserve and the node it is spent in (D-231).
 *
 * The hours are **not** a number the server refreshes: `hours` was true at
 * `at`, and it moves by `per_hour` from there. The client counts the hand
 * itself, the way it counts the planet's clock -- the server would otherwise
 * have to speak once a second (D-226).
 */
export type Frost = {
  /** `frost` or `heat` (D-251): what the planet does to a body left in it. */
  climate: string;
  /** Whether **this** node is warm: a stove works here, or it is the board. */
  warm: boolean;
  hours: number;
  at: string;
  /** Hours of reserve gained per hour here; negative is the countdown. */
  per_hour: number;
  /**
   * The ceiling, which depends on what is worn -- the client cannot derive it
   * (D-225). What the frozen body pays is not here for the opposite reason:
   * `frost.frozen_stamina` and `frost.frozen_drain_k` are catalog constants and
   * live in `/public/constants`.
   */
  max: number;
};

/**
 * The air one is breathing (D-233, D-234). The second scale of survival, told
 * the same way the first is: a level, a rate and a stamp, with the client
 * counting the hand -- a number of units would go stale between pushes (D-226).
 */
export type Air = {
  /** Where the breath comes from: the hull's tanks, or a cylinder through a suit. */
  where: "aboard" | "suit";
  units: number;
  /** Units spent per hour; negative is the countdown, zero is nothing to spend
   *  it through -- a bagful of cylinders and no suit to connect them. */
  per_hour: number;
  at: string;
  /** Whether a suit is worn. Without one a cylinder gives nothing at all. */
  suit: boolean;
};
