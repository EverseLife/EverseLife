// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What `ship.view` answers with, and the words the ship's windows are built of.
 *
 * A module of its own so the panel files export components only (fast refresh)
 * and so the chart, the card and the floor plan speak of one hull rather than
 * three shapes of it.
 */

/** What one move costs this hull. The class it was computed with is the
 *  ship's own (`Vessel.class`) and is not repeated here (D-225). */
export type Price = {
  hours: number | null;
  /** What this move burns out of the tanks. */
  fuel: number | null;
  /**
   * What must be **in** the tanks before the order is taken at all. Larger
   * than `fuel` wherever the move ends somewhere with no bunker: an orbit is
   * not a place to be stranded in, so the climb keeps the descent behind it
   * and a crossing keeps the descent at the far end (D-245).
   */
  needs: number | null;
  /** Enough thrust to leave the ground at all. Class closes no route. */
  reachable: boolean;
};

/** A leg to or from the ground, priced by the hour: the climb to the orbit
 *  above the pad, aimed at that one node (D-245). */
export type Leg = Price & {
  node: string;
  name: string;
  planet: string;
};

/** One arc of a crossing, priced for this hull (D-271): the flight time, the
 *  delta-v the sky asks for it, the planet it bends round and what it burns.
 *  What must be in the tanks besides is the route's `reserve`, sent once. */
export type ArcPrice = {
  hours: number;
  dv: number;
  via: string | null;
  fuel: number;
};

/** A destination the console offers: a planet's orbit, and the two ends of
 *  the slider to it -- the fastest arc the engines deliver and the cheapest
 *  the horizon offers. The whole slider is read on demand (`ship.course`). */
export type Route = {
  node: string;
  name: string;
  planet: string;
  /** Enough thrust to leave the ground at all. Class closes no route. */
  reachable: boolean;
  cheap: ArcPrice | null;
  fast: ArcPrice | null;
  /** The descent at the far end, kept in the tanks and not burnt by the
   *  passage (pillar P6): an arc needs its own fuel plus this (D-225). */
  reserve: number;
};

/** A pad under the hull. Nothing but a name: what the descent costs is a fact
 *  about the planet, and it is sent once beside the list (D-225, D-245). */
export type Pad = {
  node: string;
  name: string;
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
  /** What stands on the life support's line (D-288): the oxygen the crew dies by. */
  units: number;
  /** Whether the hull is breathing its own air at all: in port under a sky
   *  that has some, nothing is spent and the rate is zero. */
  sealed: boolean;
  per_hour: number;
  at: string;
};

/**
 * Where the hull is in its journey (D-245): on a pad, in orbit, or under way.
 *
 * The console is built round it -- each stage offers a different move, and no
 * other key says which. From the ground one only climbs; from orbit one
 * crosses to another world or comes down onto this one; under way one may only
 * turn back.
 */
export type Stage = "port" | "orbit" | "flight";

/** A passage under way: where it ends and between which two moments. */
export type Flight = {
  to: string | null;
  name: string | null;
  planet: string | null;
  started_at: string;
  arrives_at: string;
  /** Whether this is the way back (D-242): a turn-back is not turned back, and
   *  the button must be dark rather than collect a refusal per click. */
  back: boolean;
  /** The planet the arc bends round, if any (D-271). */
  via?: string | null;
  /** The arc itself, map units at equal time steps: the chart draws the hull
   *  along it. Absent on the climb and the descent. */
  arc?: [number, number][] | null;
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
  /** Whether a life support system stands aboard at all (D-288): no number of
   *  people any more -- the air on its line is the ceiling, and that is `air`. */
  life_support: boolean;
  /** What the engines' lines hold, in physical units (D-288). */
  fuel: number;
  air: Air;
  /** Which planet's sky the hull stands in -- where the chart draws it. */
  planet: string;
  stage: Stage;
  /**
   * The climb to the orbit above the pad, priced by the planet's gravity.
   * Empty anywhere but on the ground: there is no such move from there.
   */
  climb: Leg | null;
  /** What coming down costs from here: one price for the whole planet (D-245). */
  descent: Price | null;
  /**
   * The pads under the hull, offered from orbit only. This is the moment the
   * pier is actually chosen -- with the planet already below (D-245).
   */
  landings: Pad[];
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
 * What must be in the tanks for the move the hull is offered here.
 *
 * The engine refuses a leg that ends where there is no bunker without the fuel
 * to leave again (D-245, pillar P6), and `needs` is that number: for the climb
 * it is the climb plus the descent home. Nothing offered -- nothing to
 * compare, and the engine names the figure in its own refusal.
 */
export function wanted(move: Price | null): number {
  return move?.needs ?? 0;
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

/** One point of the slider, priced for this hull. */
export type Sample = {
  hours: number;
  dv: number;
  /** The planet the arc bends round, or nothing for a direct arc. */
  via: string | null;
  fuel: number;
  /** Whether the engines can give that delta-v in that time. */
  ok: boolean;
};

/** What `ship.course` answers: the samples, and the reserve once beside them. */
export type CourseAnswer = { planet: string; reserve: number; samples: Sample[] };

/**
 * The slider's range: from the first arc the engines deliver to the cheapest
 * one. Everything faster is refused by thrust, everything slower costs more
 * for nothing -- neither is a choice worth offering.
 */
export function range(samples: Sample[]): [number, number] | null {
  const first = samples.findIndex((s) => s.ok);
  if (first < 0) return null;
  let cheapest = first;
  for (let i = first; i < samples.length; i++) {
    if (samples[i].dv < samples[cheapest].dv) cheapest = i;
  }
  return [first, cheapest];
}
