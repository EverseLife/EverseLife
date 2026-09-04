// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The client's glyph shapes (D-238, amendment 1).
 *
 * Service marks, not illustrations: stroke only, one width, no fills and no
 * emoji -- the brief forbids the last two outright, and for a reason. An
 * emoji is somebody else's drawing at somebody else's weight, and it drags a
 * whole foreign style into a screen built on hairlines.
 *
 * Glyphs are drawn for **classes**, never for single goods: bread and dried
 * fish both wear the food mark, three picks of three metals wear one pick.
 * A new item costs no new drawing.
 *
 * Data lives here, apart from the `Glyph` component, for two reasons: the
 * map draws these paths inside its own SVG where a nested component has no
 * place, and fast refresh wants component modules to export components only.
 */

export type GlyphName =
  | "me"
  | "work"
  | "money"
  | "goods"
  | "knows"
  | "estate"
  | "net"
  | "state"
  | "stamina"
  | "satiety"
  | "warmth"
  | "water"
  | "pick"
  | "axe"
  | "tool"
  | "station"
  | "gear"
  | "vehicle"
  | "food"
  | "ore"
  | "ingot"
  | "plant"
  | "vessel"
  | "pot"
  | "furniture"
  | "forest"
  | "glade"
  | "plot"
  | "ruins"
  | "market"
  | "port"
  | "orbit"
  | "globe"
  | "rooms"
  | "pinned"
  | "loose"
  | "eye"
  | "fold"
  | "nearer"
  | "farther"
  | "here"
  | "map"
  | "talk"
  | "voice"
  | "whisper"
  | "more"
  | "alpha";

/** One stroke width for all of them, so a row of icons reads as one set. */
export const GLYPH_WIDTH = 1.25;

export const SHAPES: Record<GlyphName, string> = {
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
  //: The Net: three nodes and the links between them.
  net: "M8 3.2a1.3 1.3 0 100 2.6 1.3 1.3 0 100-2.6M3.6 11.2a1.3 1.3 0 100 2.6 1.3 1.3 0 100-2.6M12.4 11.2a1.3 1.3 0 100 2.6 1.3 1.3 0 100-2.6M7.3 5.7l-2.9 4.5M8.7 5.7l2.9 4.5M4.9 12.5h6.2",
  //: A colonnade: the administration, and the only building with columns.
  state: "M2.5 13.4h11M4 13.4V6.6M7 13.4V6.6M9 13.4V6.6M12 13.4V6.6M2.4 6.6L8 2.8l5.6 3.8z",
  //: A pulse line: the body's charge, spent by work and returned by sleep.
  stamina: "M1.5 8h3l1.5-4 2.5 8 1.5-4h4.5",
  //: A bowl with steam: a meal lowers the spend for a while.
  satiety: "M2.5 7.5h11a5.5 4.5 0 01-11 0zM5.5 5V3.4M8 5V3M10.5 5V3.4",
  //: A flame: the heat reserve where cold exists at all (D-231).
  warmth: "M8 1.8c1.8 2.6 4.2 4.6 4.2 7.5a4.2 4.2 0 11-8.4 0C3.8 6.4 6.2 4.4 8 1.8zM8 13.2a2.2 2.2 0 002.2-2.4",
  //: A drop: watering, and every liquid.
  water: "M8 2.2c2 2.8 4 4.8 4 7.2a4 4 0 11-8 0c0-2.4 2-4.4 4-7.2z",
  //: A pick: the mining tool, and the outcrop worked with it.
  pick: "M2.6 6.2C4.8 3.4 8 2.2 12.4 2.6 12.8 7 11.6 10.2 8.8 12.4M12 3L3.4 13.6",
  //: An axe: blade and haft.
  axe: "M3 3c2.6-.4 4.8.4 6.4 2L6.2 8.2C4.6 6.6 3.4 5 3 3zM7.3 7.3l6 6",
  //: A hammer: any working tool without a class of its own.
  tool: "M3 5.2L6.5 2l3 1.2 3.5 3.4-2 2-3-3L4.8 8zM7.2 7.8l-4.6 5.4 1.6 1L9 8.6",
  //: An anvil: a working station.
  station: "M3 4.5h10v2H9.5v3h2l1 2.5H3.5l1-2.5h2v-3H3z",
  //: A tunic: whatever the body wears.
  gear: "M5.5 2.8h5L13.4 5l-1.6 2.4-1-.8v6.6H5.2V6.6l-1 .8L2.6 5z",
  //: A cart: two wheels and a bed.
  vehicle: "M2 6.8h9.6l2.4-3.2M2.6 6.8l.7 2.2M11.8 6.8l-.6 2.2M5 12.8a1.9 1.9 0 100-3.8 1.9 1.9 0 100 3.8M11 12.8a1.9 1.9 0 100-3.8 1.9 1.9 0 100 3.8",
  //: A loaf: everything eaten.
  food: "M2.5 9.5c0-3 2.4-5.5 5.5-5.5s5.5 2.5 5.5 5.5v2.5h-11zM5.6 7.2v2M8 6.6v2.6M10.4 7.2v2",
  //: A rock: minerals and what the ground yields.
  ore: "M4.5 3.5L11 3l2.5 5-3 5-6 .5L2 8.5zM7.5 3.2L6 8.4l3.6 5",
  //: An ingot: metal out of the furnace.
  ingot: "M4.6 6.4h6.8l1.8 4.2H2.8zM4.6 6.4l1.2-2.2h4.4l1.2 2.2",
  //: A sprout over a seed: everything grown.
  plant: "M8 13.5c-2 0-3.2-1.2-3.2-2.8S6 8 8 8s3.2 1.1 3.2 2.7S10 13.5 8 13.5zM8 8V5.2M8 5.2C8 3.8 9 2.8 10.6 2.6c.2 1.6-.8 2.6-2.6 2.6M8 5.2C8 3.8 7 2.8 5.4 2.6c-.2 1.6.8 2.6 2.6 2.6",
  //: A canister: what liquid lives in (D-230).
  vessel: "M5 4.6h6l1.5 1.8v7.1h-9V6.4zM6.2 4.6V2.8h3.6v1.8M4.5 8h7",
  //: A pot: kitchenware.
  pot: "M3.2 7h9.6M4.4 7v4c0 1.4 1.5 2.4 3.6 2.4s3.6-1 3.6-2.4V7M8 7V5.4M6.4 5.4h3.2",
  //: A bed: furniture, the home's comforts.
  furniture: "M2.5 12.4V5.2M2.5 10h11v2.4M13.5 10V8.4c0-.9-.7-1.6-1.6-1.6H6.8V10",
  //: A fir: the forest node.
  forest: "M8 1.8L4.6 6.6h2L3.4 11h9.2L9.4 6.6h2zM8 11v3",
  //: Blades of grass: a meadow.
  glade: "M3.4 13.4h9.2M5.2 13.4c0-2.4-.8-4.2-2.2-5.4M8 13.4V6.6M8 9.2c0-1.6 1-2.8 2.6-3.4M10.8 13.4c0-1.8.8-3 2-3.8",
  //: Survey brackets: a marked plot.
  plot: "M3 5.4V3h2.4M10.6 3H13v2.4M13 10.6V13h-2.4M5.4 13H3v-2.4",
  //: Broken columns: what the Forerunners left.
  ruins: "M2.5 13.4h11M4.2 13.4V6.6l2-.8v7.6M8.6 13.4V9l2-.8v5.2M3 5.4L8 2.8l5 1.6",
  //: Scales: the market.
  market: "M8 2.6v10.8M5 13.4h6M3.4 4.6h9.2M3.4 4.6L2 8.6a2 1.6 0 004 0zM12.6 4.6L11 8.6a2 1.6 0 004 0z",
  //: A rocket over the pad: the spaceport.
  port: "M8 1.8c1.6 1.4 2.4 3.2 2.4 5.4L8 9.4 5.6 7.2c0-2.2.8-4 2.4-5.4zM5.6 7.6L4.2 10M10.4 7.6l1.4 2.4M8 9.4v1.8M4.5 13.4h7",
  //: A star and a body on its ring: the space layer.
  orbit: "M8 6.4a1.6 1.6 0 100 3.2 1.6 1.6 0 100-3.2M1.6 8a6.4 3.2 0 1012.8 0 6.4 3.2 0 10-12.8 0M2.6 9.6a1.1 1.1 0 102.2 0 1.1 1.1 0 10-2.2 0",
  //: A globe with a latitude and a meridian: one planet's surface.
  globe: "M8 2.4a5.6 5.6 0 100 11.2 5.6 5.6 0 100-11.2M2.8 6.4c3.2 1.6 7.2 1.6 10.4 0M8 2.4c-2.2 3-2.2 8.2 0 11.2",
  //: A plan cut into rooms: the sub-nodes of one place.
  rooms: "M2.6 3.2h10.8v9.6H2.6zM8 3.2v9.6M8 8h5.4",
  //: A sight on the middle: the camera holds the body.
  pinned: "M8 3.6a4.4 4.4 0 100 8.8 4.4 4.4 0 100-8.8M8 6.8a1.2 1.2 0 100 2.4 1.2 1.2 0 100-2.4M8 1.4v2.2M8 12.4v2.2M1.4 8h2.2M12.4 8h2.2",
  //: Arrows to the four sides: the frame goes where the hand takes it.
  loose: "M8 2.2v11.6M2.2 8h11.6M6.4 3.8L8 2.2l1.6 1.6M6.4 12.2L8 13.8l1.6-1.6M3.8 6.4L2.2 8l1.6 1.6M12.2 6.4L13.8 8l-1.6 1.6",
  //: An eye: look closer -- the details of a row, opened in place.
  //: A loupe with a plus and one with a minus: the two zoom buttons of the
  //: map, for a screen without a wheel.
  nearer: "M6.8 2.4a4.4 4.4 0 100 8.8 4.4 4.4 0 100-8.8M10 10l3.6 3.6M6.8 4.8v4M4.8 6.8h4",
  farther: "M6.8 2.4a4.4 4.4 0 100 8.8 4.4 4.4 0 100-8.8M10 10l3.6 3.6M4.8 6.8h4",
  eye: "M1.8 8C3.4 4.9 5.5 3.4 8 3.4s4.6 1.5 6.2 4.6C12.6 11.1 10.5 12.6 8 12.6S3.4 11.1 1.8 8zM8 6.2a1.8 1.8 0 100 3.6 1.8 1.8 0 100-3.6",
  //: A chevron at a wall: the sidebar folds to its rail and opens again.
  //: Drawn pointing left; the folded state mirrors it in CSS.
  fold: "M10.2 3.2L5.4 8l4.8 4.8M2.6 3v10",
  //: The phone's four sections (brief section 9): where the body stands is a
  //: marker on the ground, the map is a folded sheet, the talk is a bubble.
  here: "M8 14.2c-2.8-3.4-4.2-6-4.2-8a4.2 4.2 0 118.4 0c0 2-1.4 4.6-4.2 8zM8 4.8a1.4 1.4 0 100 2.8 1.4 1.4 0 100-2.8",
  map: "M2.6 4.2l3.6-1.4 3.6 1.4 3.6-1.4v9l-3.6 1.4-3.6-1.4-3.6 1.4zM6.2 2.8v9M9.8 4.2v9",
  talk: "M2.6 3.4h10.8v7H7.4L4.4 13v-2.6H2.6z",
  //: How far the voice carries, for the switch in the talk's strip: a bubble
  //: with two rings coming off it, and the same bubble with one. The pair
  //: draws what the switch does rather than decorating the word beside it --
  //: half a voice still reaches the circle one stands in and barely the next
  //: (D-043), so the near ring stays and the far one goes. The bubble is
  //: narrower than `talk`'s to leave the rings room; they are what differs,
  //: and a bubble filling the box would leave them none.
  voice: "M2.2 3.8h7.2v5.2H5.4L3.4 11.2V9H2.2zM11 5.6a3.2 3.2 0 010 3.8M13 3.8a5.2 5.2 0 010 7.4",
  //: The inner ring is drawn exactly as the voice's, and the outer one is
  //: simply gone: the two shapes differ by a whole ring rather than by the
  //: size of one, which is the difference a reader can see at 14px.
  whisper: "M2.2 3.8h7.2v5.2H5.4L3.4 11.2V9H2.2zM11 5.6a3.2 3.2 0 010 3.8",
  //: The header's overflow on a phone: what did not fit the strip is behind
  //: three dots, the way a row's menu is behind its handle.
  more: "M3.2 7.1a.9.9 0 100 1.8.9.9 0 100-1.8M8 7.1a.9.9 0 100 1.8.9.9 0 100-1.8M12.8 7.1a.9.9 0 100 1.8.9.9 0 100-1.8",
  //: The alpha's debug tab (D-229): a flask -- a tool of the workshop the
  //: game is built in, not a thing of the world. Goes out with the alpha.
  alpha: "M6.2 2.6h3.6M7 2.6v4.2l-3.4 5.6c-.5.8.1 1.6 1 1.6h6.8c.9 0 1.5-.8 1-1.6L9 6.8V2.6M5 10.4h6",
};
