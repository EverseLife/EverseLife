// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The factory floor on the wire (D-253): what `auto.view` answers.
 *
 * Machines and wires are addressed by the same item ids the commands take
 * (`auto.program`, `auto.link`); everything the client can derive -- the
 * machine's kind, its place -- stays off the wire (D-225).
 */

export type FloorRow = {
  /** The machine's item id: the address every `auto.*` command takes. */
  item: string;
  /** The programmed output (a D-251 goods key), or null -- the machine idles. */
  recipe: string | null;
  /** Units worked but not yet paid out: a piece mid-way, a liquid waiting for room. */
  backlog: number;
  /** Up to what moment work is computed, ISO. The tick moves it, never the client. */
  counted_at: string;
};

export type Wire = {
  from: string;
  to: string;
};

export type Floor = {
  machines: FloorRow[];
  links: Wire[];
};
