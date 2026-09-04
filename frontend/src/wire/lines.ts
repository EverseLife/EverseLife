// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The hull's plumbing on the wire (D-288): what `line.view` answers.
 *
 * Machines and vessels are addressed by the item ids `line.set` takes. A
 * port's `lines` empty means "any installed vessel aboard" -- the client draws
 * that fan itself and is told nothing it could derive (D-225). What it cannot
 * derive is the room a thing stands in: `node_name` names it, because a
 * vessel in another compartment is the ordinary case and the client holds no
 * names for rooms it is not standing in.
 */

export type FeedPort = {
  /** The port's name: the key a line is written under (`fuel`, `oxygen`). */
  port: string;
  /** What the port takes, by goods key: the vessels worth listing hold one of these, or nothing. */
  liquids: string[];
  /** The vessels on the line, in the order they are drunk from. Empty -- any. */
  lines: string[];
};

export type FeedMachine = {
  item: string;
  goods: string;
  node: string;
  node_name: string;
  ports: FeedPort[];
};

export type FeedVessel = {
  item: string;
  goods: string;
  node: string;
  node_name: string;
  /** What is in it, by liquid. One entry since D-288 forbids mixing. */
  holds: { goods: string; amount: number }[];
};

export type Feed = {
  ship: string;
  machines: FeedMachine[];
  vessels: FeedVessel[];
};
