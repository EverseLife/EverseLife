// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The glyph, drawn (D-238, amendment 1).
 *
 * The shapes and the rules live in `glyphs.ts`; this component only puts one
 * on screen. A glyph stands **beside the label, never instead of it** in
 * navigation; in a table row it stands before the name -- the name stays,
 * because the game is complicated and guessing what a pictogram means is not
 * something to ask of a player who is trying to find their money.
 */

import type { RecipeBook } from "./api";
import { GLYPH_WIDTH, SHAPES, type GlyphName } from "./glyphs";
import { goodsGlyph } from "./marks";

const BOX = { viewBox: "0 0 16 16", fill: "none", stroke: "currentColor" } as const;

export function Glyph({ name }: { name: GlyphName }) {
  return (
    <svg {...BOX} strokeWidth={GLYPH_WIDTH} className="glyph" aria-hidden="true" focusable="false">
      <path d={SHAPES[name]} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** The class mark of one goods row: the glyph its class wears, by the book. */
export function GoodsMark({ book, goods }: { book: RecipeBook | null; goods: string }) {
  return (
    <span className="goods-mark">
      <Glyph name={goodsGlyph(book, goods)} />
    </span>
  );
}
