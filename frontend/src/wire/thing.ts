// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * A thing, and the three places a thing is found.
 *
 * `Thing` is the unit the whole game moves around, and the rest of this file
 * is what holds one: the body carries it, a chest keeps it, a machine is one
 * standing in the room and worked at rather than picked up. They are together
 * because they are the same shape seen from different sides -- each names
 * `Thing` or the ids inside it, and a change to what a thing is reaches all
 * three in the same edit.
 */

export type Thing = {
  id: string;
  goods: string;
  amount: number;
  quality?: number;
  tier: string;
  condition: number;
  /** Dish kind: the combination decides the kind, not the quality (D-128). */
  flavor?: string;
  /** Edibility comes from vault data, not the client's guesses. */
  food: boolean;
  /** Fits the pot: a product, not a pickaxe (16-cooking). */
  ingredient: boolean;
  spoils_at?: string;
  /** Coin fineness in thousandths: a coin has no quality, it has metal (D-016). */
  fineness?: number;
  /** The mark: whose work this is (D-058). */
  maker?: string;
  /** For seeds: cultivar and batch strength, % (D-057). */
  variety?: string;
  vigor?: number;
  /** For a battery: charge with self-discharge (D-071). */
  charge?: number;
  /** Unit weight, kg, and the slot if this is gear (D-146). */
  mass: number;
  slot?: string;
  /**
   * For a knowledge carrier: the recipe written on it, and the name the counter
   * knows the stack by -- "Рецепт: Стекло" (D-209). `key` equals `goods` for
   * everything else.
   */
  recipe?: string;
  key: string;
  /**
   * For a vessel only -- a canister, a tank (D-230): what is poured into it.
   * A liquid never lies in the pocket by itself, so without this the water in
   * the hands would be invisible. The capacity is the catalog's (`store`).
   */
  content?: Thing[];
};

/** Carried load: how much is carried, how much can be, and what is worn (D-146). */
export type Carry = {
  load: number;
  capacity: number;
  slots: string[];
  equipped: Record<string, { id: string; goods: string }>;
};

/** A machine in the node: one person works at a machine (D-150). */
export type Bench = {
  id: string;
  goods: string;
  quality?: number;
  condition: number;
  busy: boolean;
  mine: boolean;
  /** Charge belongs to the battery standing here as a machine (D-179). */
  charge?: number;
};

/** A node storage: a chest or a shelf (D-181).
 *
 * The chest itself is visible to anyone -- it stands in the room; the contents
 * come only to whoever may open it, for the rest `content` is empty.
 */
export type Storage = {
  id: string;
  goods: string;
  /** Capacity, kg. */
  capacity: number;
  /** How many kilograms are already taken. */
  mass: number;
  /** Whether the viewer may put and take. */
  mine: boolean;
  content: Thing[];
};
