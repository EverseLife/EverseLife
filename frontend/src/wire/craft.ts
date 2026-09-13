// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Making things: the book, the estimate, and the work.
 *
 * `RecipeBook` is the vault's catalog as `/public/recipes` serves it -- what
 * can be made at all, out of what, at which machine. `Plan` is that book
 * applied to the hands actually holding the tools: the same recipe, priced in
 * this quality, this waste and this energy. `Batch` is the work once it is
 * running, and `Invention` is what comes back when there was no recipe to
 * begin with (D-064, D-209).
 *
 * One file because it is one arc, and because the fields line up along it: a
 * batch's `output` and `station` are a recipe's, and a plan is the arithmetic
 * between them.
 */

export type RecipeBook = {
  bulk: string[];
  /** Liquids (D-230): they exist only inside a vessel, never loose in the hands. */
  liquid?: string[];
  /** Vent gases (D-340): liquids let out where there is no air outside and
   *  burned in the node's flare stack where there is -- hydrogen. */
  vent?: string[];
  /**
   * Things no recipe makes: world raw material and operation products (D-215).
   * `/public/recipes` has always sent them; they are typed here because the
   * alpha widget prints by name and a name is either a material or a recipe's
   * output -- deriving the list beats a second server key for it (D-225).
   */
  /**
   * Everything that is not made by a recipe (D-215). `relic` marks what the
   * Forerunners left (D-232): it is machinery, but nobody makes it, takes it
   * down or carries it away -- and the client must not offer to.
   */
  materials: { name: string; id?: string; class?: string | null; relic?: boolean }[];
  units: Record<string, string>;
  operations: Operation[];
  recipes: Recipe[];
  /** Thing classes (D-215, D-251): class id -> member goods ids. */
  classes: Record<string, string[]>;
  tool_classes: Record<string, string[]>;
  /** Every Russian name and colloquial synonym -> stable id (D-251). Ids are
   *  not keys here: an id resolves to itself by falling through. */
  synonyms: Record<string, string>;
  /** Russian class name -> class id (D-251). */
  class_ids?: Record<string, string>;
  /** The world's constants ride along (D-209): one book through every panel.
   *  Not all of them are numbers -- `quality.scale` is a pair of bounds --
   *  so a reader narrows what it takes. */
  constants?: Record<string, unknown>;
};

/**
 * The vault's recipe book as `/public/recipes` serves it -- the fields the
 * client reads (mirrors `constants/catalog.py`; the server sends more).
 */
export type Recipe = {
  name: string;
  /** The stable id (D-251): what the wire, `knows` and commands name. The
   *  Russian `name` stays for display. Optional so hand-built test books work. */
  id?: string;
  kind: string;
  /** Built in place (D-268): stands where it was made, never taken up. */
  built?: boolean;
  /** Runs on electricity (D-269): a batch at it draws from the grid or the cells beside it. */
  powered?: boolean;
  roles: boolean;
  food: boolean;
  inputs: string[];
  amounts: Record<string, number>;
  station?: string;
  /** Capacity as a storage, kg (D-181); `holds` says what it admits (D-230). */
  store?: number | null;
  holds?: string | null;
  /** What else a batch gives per unit of this (D-340): the hydrogen of electrolysis. */
  byproduct?: Record<string, number>;
};

export type Operation = {
  name: string;
  /** The stable id (D-251): `craft.start`'s `way` names it. */
  id?: string;
  requires: string[];
  gives: string[];
  consumes: string[];
  place?: string;
};

export type Plan = {
  output: string;
  units: number;
  quality: number;
  spread: number;
  ceiling: number;
  accuracy: number;
  waste: number;
  minutes: number;
  consumes: Record<string, number>;
  /** Electricity for a machine on it and what the grid bills (D-269); absent at a machine driven by the hands. */
  energy?: number;
  price?: number;
  /** Where each liquid of the batch goes and the room it finds now (D-340); absent for a batch with no liquid. */
  outlets?: Outlet[];
};

/**
 * Where one liquid of a batch goes (D-340), as the engine's finish will pour
 * it: into the vessels on the port's line aboard (`line`), into those in the
 * hands and at the machine (`reach`) or only at the machine (`place`), with
 * the `room` they have now -- or, for a vent gas with a way out of the place,
 * out where there is no air (`void`), into its line and the rest overboard
 * from a sealed hull (`overboard`), or into the node's flare (`flare`), with
 * no room to fall short of. The room is shown, not reserved: what the batch
 * will give is the client's to count, from the batch's size and the recipe's
 * byproduct (D-225).
 */
export type Outlet = {
  goods: string;
  where: "line" | "reach" | "place" | "void" | "overboard" | "flare";
  room?: number;
};

export type Batch = {
  id: string;
  work: "make" | "repair" | "recycle";
  output: string;
  units: number;
  quality: number;
  /** The machine it needs; empty for what is made by hand. */
  station?: string;
  /**
   * Under way, or waiting (D-209): behind another work of yours (`queued`),
   * frozen in another node (`away`), or here but with no free machine
   * (`no_station`).
   */
  state: "running" | "waiting";
  waiting?: "queued" | "away" | "no_station";
  /** Where the work is: a frozen batch is waited for in its node. */
  node?: string;
  /** The current run's ends: the deadline bar shows a share, and a share needs a beginning. */
  started_at?: string;
  ready_at?: string;
  /** Work left while waiting, seconds. */
  left_seconds?: number;
  /** For a carrier being written: which recipe goes onto it. */
  recipe?: string;
  /** A running make's liquids: where they go and the room there now (D-340). */
  outlets?: Outlet[];
};

/** What came of an attempt to make something without a recipe (D-064, D-209). */
export type Invention = {
  success: boolean;
  learned: string[];
  burned: Record<string, number>;
  note?: string;
  batch?: { id: string; output: string; quality: number; ready_at?: string };
};
