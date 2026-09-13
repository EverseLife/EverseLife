// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The field automaton on the wire (D-339): what `agro.view` answers and what
 * `agro.program` takes.
 *
 * A machine is addressed by its item id; the beds it works come with the
 * farm's survey and what lies in its bunker with the node's storages -- only
 * what the client cannot derive rides here (D-225).
 */

/** The closed list of commands (D-120, D-339). */
export const COMMANDS = [
  "plow",
  "sow",
  "moisture",
  "feed",
  "weed",
  "thin",
  "harvest",
  "fallow",
] as const;
export type Command = (typeof COMMANDS)[number];

/** The stages a feeding can be given in: every stage but ripeness. */
export const FEED_STAGES = ["sprout", "leaf", "bloom", "fill"] as const;

/** Why a machine stands: the words `ui-agro-trouble-*` translate. */
export type Trouble =
  | "no_plots"
  | "no_power"
  | "no_lube"
  | "no_water"
  | "no_seeds"
  | "no_fertilizer"
  | "store_full"
  | "no_store"
  | "not_plowed"
  | "unfit"
  | "not_entitled"
  | "fault";

/** One line of a programme, with the parameter its command takes. */
export type Line = {
  do: Command;
  culture?: string;
  target?: number;
  goods?: string;
  stage?: string;
  days?: number;
};

export type FieldMachine = {
  /** The machine's item id: the address `agro.program` and `agro.stop` take. */
  item: string;
  program: Line[];
  /** The line the machine stands on. */
  cursor: number;
  /** The plots given to it, in order. */
  plots: string[];
  /** The storages named: where seeds and fertilizer come from, where the harvest goes. */
  seeds: string | null;
  fertilizer: string | null;
  harvest: string | null;
  trouble: Trouble | null;
};

export type Fields = { machines: FieldMachine[] };
