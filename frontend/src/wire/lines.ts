// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The hull's plumbing on the wire (D-288, D-340): what `line.view` answers.
 *
 * Machines and vessels are addressed by the item ids `line.set` and
 * `line.name` take. A port's `lines` empty means the port reaches nothing
 * (D-288 as amended 2026-09-04) -- the client says so itself and is told
 * nothing it could derive (D-225). What it cannot derive is the room a thing
 * stands in: `node_name` names it, because a vessel in another compartment is
 * the ordinary case and the client holds no names for rooms it is not
 * standing in; and the rooms' laying order, which the scheme's lanes follow.
 */

/** Which way a port's liquid runs: drunk, poured with the machine standing
 *  when all is full, or poured with the rest let go -- the beds' oxygen into
 *  the compartment's air, a vent gas overboard from a sealed hull and, under a
 *  sky with air, nowhere: then the vent holds the machine like an outlet (D-340). */
export type PortWay = "in" | "out" | "vent";

export type FeedPort = {
  /** The port's name: the key a line is written under (`fuel`, `oxygen`, `water`). */
  port: string;
  /** What the port takes, by goods key: the vessels worth listing hold one of these, or nothing. */
  liquids: string[];
  way: PortWay;
  /** The vessels on the line, in the order they are drunk from or filled. Empty -- none. */
  lines: string[];
};

export type FeedMachine = {
  item: string;
  goods: string;
  node: string;
  node_name: string;
  ports: FeedPort[];
  /** Why an automat on its lines stands, while it does: the name of the port
   *  that stopped it, or `power`. Absent while it works. */
  stall?: string;
};

export type FeedVessel = {
  item: string;
  goods: string;
  node: string;
  node_name: string;
  /** The owner's name for it (D-340). Absent -- it goes by its goods' name. */
  name?: string;
  /** What is in it, by liquid. One entry since D-288 forbids mixing. */
  holds: { goods: string; amount: number }[];
};

export type FeedRoom = { node: string; node_name: string };

export type Feed = {
  ship: string;
  /** Whether this reader may redraw the lines and name the vessels. */
  yours: boolean;
  /** The compartments in laying order: the scheme's lanes. */
  rooms: FeedRoom[];
  machines: FeedMachine[];
  vessels: FeedVessel[];
};

/** The reason an automat stands that is not a port's own. */
export const STALL_POWER = "power";

/** Whether a vessel may stand on this port: it holds the port's liquid, or nothing yet. */
export function suits(port: FeedPort, vessel: FeedVessel): boolean {
  return vessel.holds.length === 0 || vessel.holds.some((one) => port.liquids.includes(one.goods));
}
