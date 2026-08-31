// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The graph, and moving along it.
 *
 * One subject in two grains. `MapNode`, `MapEdge`, `MapRoute` and `WorldMap`
 * are the world as `/public/map` draws it; `Exit`, `Transit`, `RoadWork`,
 * `Vehicle` and `Convoy` are the same graph as the body meets it -- where one
 * may step from here, how long the step takes, what is harnessed for it and
 * what the road is made of. `InSight` is the third grain: a ship's own little
 * graph, which is never on the public map (D-201).
 *
 * `SURFACE` and `spell` are here rather than in a panel because the surface
 * and the seconds are fields of these shapes, and every screen that shows an
 * edge shows both.
 */
import { t } from "../locale";

/** Vehicles standing in the node: one harnesses to them, not stands at them (D-157). */
export type Vehicle = {
  id: string;
  goods: string;
  condition: number;
  /** Hold capacity, kg. Empty -- the vault did not name it. */
  capacity?: number;
  /** Multiplier to walking speed: a barrow is slower than legs, a wagon faster. */
  speed_k: number;
  /** Taken by somebody else's harness. */
  taken: boolean;
};

/** Convoy: what it is harnessed to and what it carries (D-157). */
export type Convoy = {
  id: string;
  type_key: string;
  condition: number;
  capacity: number;
  /** How much it already carries, kg. */
  mass: number;
  speed_k: number;
  heavy: boolean;
  cargo: { id: string; type_key: string; amount: number; quality?: number }[];
};

/** A road as work on an edge (D-107, D-158). */
export type RoadWork = {
  edge: string;
  /** Where it leads. */
  to: string;
  surface: "trail" | "road" | "paved";
  /** Surface condition 0..100: overgrows without maintenance. */
  condition: number;
  seconds: number;
  /** The next tier, or empty for a highway. */
  next?: "road" | "paved";
  /** How much surface laying a tier takes, and how much resurfacing does. */
  needs?: number;
  mend_needs?: number;
  /** How much surface is in the hands right now. */
  at_hand: number;
  working: boolean;
};

/** Where one can go from here, how much it costs in time and how much in body (D-147). */
export type Exit = {
  key: string;
  name: string;
  surface: "trail" | "road" | "paved";
  seconds: number;
  /** Stamina spend for the road. With a vehicle -- zero. */
  stamina: number;
};

/** While walking -- you are absent: everything in-person is closed (D-107). */
export type Transit = {
  to: string;
  to_key: string;
  from_key: string;
  started_at: string;
  arrives_at: string;
  /** Autopath (D-045): the route's final goal, if it is beyond this leg. */
  final?: string;
  final_key?: string;
  legs_left?: number;
};

/** The world map: nodes and edges. Cities and highways are public (D-097). */
export type MapNode = {
  key: string;
  name: string;
  /** Display layer: the world is one graph, layers are a way to look at it (D-045). */
  layer: "space" | "planet" | "city" | "location";
  /** The group the node belongs to: location -> city -> planet. */
  parent: string | null;
  /** The city gate: every road beyond the walls starts here (D-206). */
  exit: boolean;
  /** The spaceport: the city's second door, the one ships couple to (D-206). */
  port: boolean;
  /** Which planet the node belongs to. The space layer paints by it. */
  planet: string;
  /** Where the node stands, once and for everybody (D-237). Given by the
   *  server when the node is created and never recomputed, so the map is the
   *  same map for every player and the same one tomorrow. Absent on the space
   *  layer -- a planet's point comes from the clock -- and on a node laid
   *  before the rule, where the client falls back to its own layout. */
  place?: { x: number; y: number } | null;
  /** A planet's place in the system: display radius, a full circle in real
   *  days and the phase at the world's epoch. Only planets have one -- on the
   *  space layer a place is a function of time, not of a settled layout. */
  orbit: { radius: number; period_days: number; phase: number } | null;
  /** Drawn, but not playable yet: Aquatica is out of the alpha (D-104). */
  deferred: boolean;
  /** Part of a ship: its delegate on the space layer or a room aboard (D-201). */
  aboard: boolean;
  /** A ship under way. It has no edges at all while it flies, so its place on
   *  the map is a share of the way between the port it left and the one it is
   *  due at -- nothing in the graph could say it. */
  flight: { to: string; started_at: string; arrives_at: string } | null;
  /** Place-sign property ids ("woods", "stones"): the map draws the node's
   *  type glyph by them (D-238, D-251). Optional: older servers do not send it. */
  features?: string[];
  /** The owner's nailed mark, if any (D-238): beats the place signs. */
  emblem?: string | null;
};

export type MapEdge = { a: string; b: string; surface: Exit["surface"]; seconds: number };

/** A corridor between two planets: not an edge of the graph but the price of a
 *  passage (D-037). The two ends are the vault's -- in conjunction and in
 *  opposition -- and where between them a given hour falls is decided by where
 *  the planets stand then. Ends by planet, not by node key. */
export type MapRoute = {
  a: string;
  b: string;
  window_hours: number;
  apart_hours: number;
};

export type WorldMap = { nodes: MapNode[]; edges: MapEdge[]; routes: MapRoute[] };

/** What of ships is visible from where one stands, and nothing beyond it
 *  (D-201): at a pier the moored ships, aboard the rooms between which one
 *  walks. None of it is on the public map -- from outside a ship is a single
 *  hull, and its layout is what a boarder would want to know. */
export type InSight = { nodes: MapNode[]; edges: MapEdge[] };

/** Surface in words, by message key: a module-scope map holds keys, not text. */
export const SURFACE: Record<Exit["surface"], string> = {
  trail: "ui-map-surface-trail",
  road: "ui-map-surface-road",
  paved: "ui-map-surface-paved",
};

/** Travel time in words: seconds for a step across the city, minutes for a road. */
export function spell(seconds: number): string {
  //: Rounded before the unit is chosen, so 59.7 seconds reads as a minute
  //: rather than as "60 с" -- the same carry `clock.duration` takes.
  //:
  //: The units are `clock`'s own messages rather than a second set: the two
  //: print the same "3 мин", and one of them would have gone stale. Every
  //: count goes in as a string -- a term is read, not summed, and `NUMBER`
  //: would put a separator inside "1 200 ч".
  if (Math.round(seconds) < 60) {
    return t("ui-clock-seconds", { n: String(Math.round(seconds)) });
  }
  if (Math.round(seconds / 60) < 60) {
    return t("ui-clock-minutes", { n: String(Math.round(seconds / 60)) });
  }
  return t("ui-clock-hours", { n: (seconds / 3600).toFixed(1) });
}
