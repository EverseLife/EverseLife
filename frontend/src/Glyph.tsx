// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The sidebar's icons.
 *
 * Service marks, not illustrations: stroke only, one width, no fills and no
 * emoji -- the brief forbids the last two outright, and for a reason. An emoji
 * is somebody else's drawing at somebody else's weight, and it drags a whole
 * foreign style into a screen built on hairlines.
 *
 * They stand **beside the label, never instead of it**: the game is complicated,
 * and guessing what a pictogram means is not something to ask of a player who
 * is trying to find their money.
 */

const BOX = { viewBox: "0 0 16 16", fill: "none", stroke: "currentColor" } as const;

/** One stroke width for all of them, so a row of icons reads as one set. */
const WIDTH = 1.25;

export type GlyphName =
  | "me"
  | "work"
  | "money"
  | "goods"
  | "knows"
  | "estate"
  | "state";

const SHAPES: Record<GlyphName, string> = {
  //: A person: head and shoulders.
  me: "M8 2.4a2.6 2.6 0 100 5.2 2.6 2.6 0 100-5.2M2.6 14c0-3 2.4-4.6 5.4-4.6s5.4 1.6 5.4 4.6",
  //: A list of works: lines of unequal length, because a queue is never even.
  work: "M2.5 4.5h11M2.5 8h11M2.5 11.5h7",
  //: A coin: a circle with a bar through it.
  money: "M8 2.5a5.5 5.5 0 100 11 5.5 5.5 0 100-11M8 4.6v6.8M6 6.6h4M6 9.4h4",
  //: Goods in the hands: a crate.
  goods: "M2.6 5.4l5.4-2.8 5.4 2.8v5.2L8 13.4 2.6 10.6zM2.6 5.4L8 8.2l5.4-2.8M8 8.2v5.2",
  //: An open book: two leaves and a spine.
  knows: "M3 3.2h4.2c.5 0 .8.3.8.8v9c0-.5-.3-.8-.8-.8H3zM13 3.2H8.8c-.5 0-.8.3-.8.8v9c0-.5.3-.8.8-.8H13z",
  //: A house: a roof over walls.
  estate: "M2.5 7L8 2.8 13.5 7v6.2h-11z",
  //: A colonnade: the administration, and the only building with columns.
  state: "M2.5 13.4h11M4 13.4V6.6M7 13.4V6.6M9 13.4V6.6M12 13.4V6.6M2.4 6.6L8 2.8l5.6 3.8z",
};

export function Glyph({ name }: { name: GlyphName }) {
  return (
    <svg {...BOX} strokeWidth={WIDTH} className="glyph" aria-hidden="true" focusable="false">
      <path d={SHAPES[name]} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
