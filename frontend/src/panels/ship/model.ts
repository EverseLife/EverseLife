// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What `ship.view` answers with, and the words the ship's windows are built of.
 *
 * A module of its own so the panel files export components only (fast refresh)
 * and so the chart, the card and the floor plan speak of one hull rather than
 * three shapes of it.
 */

/** A destination the console offers: a place, and what it costs **this** hull. */
export type Route = {
  node: string;
  name: string;
  planet: string;
  /**
   * What the ship is: the weakest engine aboard. Not a demand of the route --
   * no route makes one (D-235) -- but the number the fuel was computed with.
   */
  class: number | null;
  hours: number | null;
  fuel: number | null;
  /** Enough thrust to leave the ground at all. Class closes no route. */
  reachable: boolean;
  /**
   * The whole planet stands behind this row: it takes a landing anywhere on
   * its surface (D-233), and the node the hull comes down in is rolled at the
   * landing (D-235). There is no pier to choose.
   */
  anywhere?: boolean;
};

export type Engine = { name: string; count: number; thrust: number; class: number };

/**
 * The air aboard (D-233, D-234). A level and a rate rather than hours: the
 * client counts the hand itself, as it does the cold and the clock (D-226).
 */
export type Air = {
  units: number;
  water: number;
  /** Whether the hull is breathing its own air at all: in port under a sky
   *  that has some, nothing is spent and the rate is zero. */
  sealed: boolean;
  per_hour: number;
  at: string;
};

/** A passage under way: where it ends and between which two moments. */
export type Flight = {
  to: string | null;
  name: string | null;
  planet: string | null;
  started_at: string;
  arrives_at: string;
};

export type Vessel = {
  ship: string;
  name: string;
  nodes: number;
  mass: number;
  /** Where the mass comes from: what to cut is read off this, not off the total (D-230). */
  mass_parts: { hull: number; machines: number; cargo: number };
  engines: Engine[];
  thrust: number;
  ratio: number;
  min_ratio: number;
  class: number | null;
  crew: number;
  life_support: number;
  fuel: number;
  air: Air;
  /** Which planet's sky the hull stands in -- where the chart draws it. */
  planet: string;
  docked: string | null;
  port: string | null;
  /** Which berth of that port: the gangway is as long as its number (D-201). */
  berth: number | null;
  /** Whether the hull carries a console of its own. It is the receiver: a
   *  ground console talks to it, and a hull without one takes no order at all
   *  -- its own crew's or anybody's (D-242). */
  bridge: boolean;
  /** The pier it cast off from, if it has ever cast off: what a turn-back
   *  aims at, by name. Empty means there is nothing to turn back to. */
  left: string | null;
  connector: string | null;
  flight: Flight | null;
  routes: Route[];
  /** Whether this hull is the viewer's own: a guest reads the card and is
   *  offered none of the buttons the engine would refuse (D-240). */
  yours: boolean;
  /**
   * The grid the floor plan snaps to, in the map units places are given in.
   * The server's own number: a copy of it here would silently skew every hull
   * the day the server changes it, and the client cannot derive it (D-225).
   */
  grid: { cell: number; reach: number };
};

/**
 * The cheapest passage the ship could make from here.
 *
 * The engine refuses to undock without fuel for the way back, and that way
 * back is the cheapest passage there is -- so the cheapest route on the board
 * is the number to compare against. No routes to compare with (a single port
 * in the world): let the engine speak, and it names the figure in its refusal.
 */
export function cheapest(v: Vessel): number {
  const fuels = v.routes.map((route) => route.fuel).filter((fuel): fuel is number => fuel != null);
  return fuels.length ? Math.min(...fuels) : 0;
}

/**
 * How long the air aboard lasts, in hours -- or null where it lasts.
 *
 * Counted from the level and the rate the server gave, never asked for as a
 * number that would go stale between pushes (D-226).
 */
export function autonomy(air: Air): number | null {
  if (!air.sealed || air.per_hour >= 0) return null;
  return air.units / -air.per_hour;
}
