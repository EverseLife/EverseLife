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
   * What the round trip takes: this move and the way back down. Larger than
   * `fuel` wherever the move ends somewhere with no bunker. The console's
   * warning, not the engine's refusal (D-289): a hull short of it climbs and
   * sits on its circle until fuel reaches it.
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
 *  delta-v the sky asks for it and what it burns. What must be in the tanks
 *  besides is the route's `reserve`, sent once. */
export type ArcPrice = {
  hours: number;
  dv: number;
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
  /** The descent at the far end: an arc needs its own fuel plus this to
   *  come down at the end (D-225). The console's warning, not the engine's
   *  refusal (D-289): short of it, the hull goes adrift rather than stays. */
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

export type Engine = {
  name: string;
  count: number;
  thrust: number;
  class: number;
};

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
 * turn back; adrift one lays a course from where inertia left the hull; lost
 * one does nothing -- the hull is gone with its crew (D-289).
 */
export type Stage = "port" | "orbit" | "flight" | "adrift" | "lost";

/**
 * Where inertia takes the hull (D-289): round for ever, onto a body, or out
 * of the system -- and when. The line is the coast ahead, map units at equal
 * time steps, for the chart to draw.
 */
export type Fate = {
  kind: "stable" | "crash" | "escape";
  at: string;
  body: string | null;
  trace: [number, number][];
};

/** The hull in the sky (D-289): where it is at `at`, and the coast ahead as
 *  the tick last counted it -- nothing until the first tick since the order.
 *  Nothing at all at a pad, on a climb, or on the circle. */
export type Sky = {
  x: number;
  y: number;
  at: string;
  inertia: Fate | null;
};

/** The order under way, in two numbers (D-289): the plan's delta-v, and what is
 *  left of it to burn. The line itself is `Flight.arc`. */
export type Order = {
  dv: number;
  left: number;
};

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
  /** The hull in the sky (D-289), or nothing at a pad or on a leg. */
  sky: Sky | null;
  /** What speed the tanks buy at this mass, units a day: the plan's delta-v is
   *  read against it, and the console warns before the button (D-289). */
  dv: number;
  /** The order under way, in numbers; nothing when there is none. */
  course: Order | null;
  routes: Route[];
  /** Who else is in the sky near this hull (D-289, wave 3). */
  sightings: Sighting[];
  /** The hull this one flies as one with, either way round. */
  held: Held | null;
  /** Whether the two are joined by an edge connector to connector: the
   *  edge is to the hull held, so its name is `held.name`. */
  docked_to_ship: boolean;
  /** Where the consents to dock stand: given by this hull and not yet
   *  returned; given by the other hull and not yet by this one. */
  dock: { asked: boolean; wanted: boolean };
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
 * What the tanks should hold for the move the hull is offered here.
 *
 * The climb plus the descent home: what the console warns against, not what
 * the engine refuses (D-289) -- a hull short of it climbs and stays on its
 * circle until fuel reaches it. Nothing offered -- nothing to compare.
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
  fuel: number;
  /** Whether the engines can give that delta-v in that time. */
  ok: boolean;
  /** The arc the chart draws while the slider stands on this point (D-289):
   *  the planner's line, map units at equal time steps. */
  trace?: [number, number][];
};

/** What `ship.course` answers: the samples, and the reserve once beside them.
 *  One of `planet` and `ship` names what the slider runs to (D-289, wave 3). */
export type CourseAnswer = {
  planet: string | null;
  ship: string | null;
  reserve: number;
  samples: Sample[];
};

/** What the console's course is set for: a planet's orbit, or another hull
 *  in sight (D-289, wave 3). */
export type Target = { planet: string } | { ship: string };

/** Whether two targets are the same thing. */
export function sameTarget(a: Target | null, b: Target | null): boolean {
  if (a === null || b === null) return a === b;
  if ("planet" in a) return "planet" in b && a.planet === b.planet;
  return "ship" in b && a.ship === b.ship;
}

/**
 * Another hull in the sky as this one sees it (D-289, wave 3): where it is,
 * what it is doing, whose it is, and whether it may be aimed at -- a drifter
 * with a line to be met on, on nobody's hold.
 */
export type Sighting = {
  ship: string;
  name: string;
  x: number;
  y: number;
  doing: "orbit" | "flight" | "adrift" | "held";
  mine: boolean;
  target: boolean;
};

/** The hull this one flies as one with (D-289, wave 3). */
export type Held = { ship: string; name: string };

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
