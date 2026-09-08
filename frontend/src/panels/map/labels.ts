// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Which names the map has room for.
 *
 * A city's nodes stand metres apart (D-323 addendum) and their names are
 * whole words, so at the scale the streets open «Ядро: Принтер Предтеч» and
 * «Рынок» were written over each other and read as one word --
 * «ПредтечРынок». Zooming does not help: the text is drawn in the map's own
 * units and grows with it, so two names that touch touch at every scale.
 *
 * Nothing is moved to make room. A node is where the server put it (D-237),
 * and a name leaning off its node is a name about the wrong node. What gives
 * way is the name itself: the ones that matter most keep theirs, and a name
 * that would be written over one already placed is left off. The node is
 * still drawn, still clickable, and the column names it the moment it is
 * picked -- an unreadable name says less than no name at all.
 *
 * Pure, and independent of the frame: the boxes are in map units, as the
 * text is, so the answer does not flicker while the hand zooms.
 */

/** The name's size in map units. Mirrors `.node-label` in `map.css`: the
 *  text is drawn in user units, so its `font-size` is a length of the map. */
export const LABEL_EM = 8;
/** A hull's name is set larger (`.node.ship .node-label`). */
export const HULL_EM = 10;
/** And a door's caption smaller and wider (`.node-door`): the letter-spacing
 *  is why it is measured with a wider glyph than a name's. */
export const DOOR_EM = 9;
const DOOR_GLYPH_W = 0.66;
/** How wide one letter runs, as a share of the em. Measured off the sans the
 *  window uses, on the Cyrillic the names are written in -- an estimate on
 *  purpose: measuring every string every frame would cost a layout pass per
 *  node, and the answer only has to be right about which names collide. */
const GLYPH_W = 0.55;
/** Air between two names, map units: names that only just miss each other
 *  still read as one. */
const GAP = 1;

/** A name on the map: where it is written, what it says, and how much it is
 *  wanted -- lower goes first and keeps its place. */
export type Named = {
  key: string;
  /** The node's point, map units. */
  x: number;
  /** The label's own baseline, map units: above the node, or under a hull. */
  y: number;
  text: string;
  em: number;
  /** Whether this is a door's caption rather than a name: it is set in wider
   *  letters, so it takes more room than its length alone would say. */
  door?: boolean;
  rank: number;
};

type Box = { x0: number; x1: number; y0: number; y1: number };

function boxOf(one: Named): Box {
  const width = one.text.length * (one.door ? DOOR_GLYPH_W : GLYPH_W) * one.em + GAP;
  return {
    x0: one.x - width / 2,
    x1: one.x + width / 2,
    //: A baseline with the body of the letters above it, and the gap below.
    y0: one.y - one.em,
    y1: one.y + GAP,
  };
}

function overlaps(a: Box, b: Box): boolean {
  return a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
}

/** Which of these names are written. Ties are settled by the key, so the same
 *  map drops the same name for everybody and does not change its mind between
 *  two renders of one frame. */
export function legible(named: readonly Named[]): Set<string> {
  const placed: Box[] = [];
  const kept = new Set<string>();
  const order = [...named].sort((a, b) => a.rank - b.rank || (a.key < b.key ? -1 : 1));
  for (const one of order) {
    if (!one.text) continue;
    const box = boxOf(one);
    if (placed.some((held) => overlaps(held, box))) continue;
    placed.push(box);
    kept.add(one.key);
  }
  return kept;
}
