// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The figures of the growth on the near frames (D-331): a fir, a
 * broadleaf, a palm, an acacia, a bush, a tuft of grass, a reed, a patch of
 * moss -- one figure a biome by the vault's `biome.figure`, scattered by
 * the biome's shares of woods and meadow (`biome.marks`), both read off
 * `/public/constants` and never off the raster passport (D-225: what the
 * client can derive it derives). Drawn in metres of ground a little under
 * the node's circle, on a lattice of a few metres within every cell, from
 * the frames where a node is a circle and not a dot. Split out of `contours.ts` 2026-09-12: a
 * figure is a thing of the country drawn on the frame's mesh, a contour a
 * reading of it, and the two files are each their own hundred-odd lines.
 */

import type { RasterPassport } from "../../api";
import { nodeRadius } from "./bands";
import type { Samples, Segment } from "./contours";
import { UNITS_PER_METRE, type Geo } from "./globe";
import { ang2pix, atlasIndex, cellCentre } from "./healpix";
import type { Rasters } from "./rasters";

const RAD = Math.PI / 180;

/** The figures the map knows how to draw (the vault's `biome.figure`, off
 *  `/public/constants`, D-331): every biome names one of these or
 *  `none`. Trees and bushes are scattered by the biome's share of woods,
 *  the ground figures by its share of meadow. */
export const FIGURES = ["fir", "broadleaf", "palm", "acacia", "bush", "grass", "reed", "moss"] as const;
export type Figure = (typeof FIGURES)[number];
export const GROUND_FIGURES: readonly Figure[] = ["grass", "reed", "moss"];
/** How much of the share becomes a figure, on the lattice below: a tree on
 *  every point of a forest is a mat of ink, and a wood on a topographic
 *  sheet is a scatter. The ground figures -- grass, reed, moss -- are small
 *  and may stand thicker, or a meadow does not read as one. Chosen by the
 *  point's own numbers, so the same ground always grows the same figures
 *  and the scatter does not crawl when the eye moves. */
export const FIGURE_DENSITY = 0.7;
export const GROUND_DENSITY = 0.9;
/** The lattice the figures stand on, metres of ground: one candidate every
 *  so many metres, a cell of fifty holding thirty-odd. A figure to a cell
 *  was a tree every fifty metres, and on the frames the figures are drawn
 *  at -- tens to a few hundred metres across -- that was one tree on the
 *  screen (owner, 2026-09-12: more of them, and closer in). */
export const FIGURE_SPACING_M = 8;
/** ...and no denser than this many across the frame: on the widest frames
 *  the figures are drawn on, eight metres apart they were thousands of
 *  three-pixel marks, a mat of ink and a second of work at every step of
 *  the eye. The lattice opens with the frame (`contours.figureLines`). */
export const FIGURE_ACROSS = 30;
/** How tall a figure is against the node's circle: a little under it. The
 *  circle is a fifth of the seating gap (`bands.nodeRadius`), drawn in
 *  metres of ground like every node, and the figures are drawn the same
 *  way -- they grow as the map is brought closer, as the nodes do, and are
 *  a little smaller than a node (owner, 2026-09-12). A symbol held to a
 *  share of the frame's width, as they were for a few hours, read as a
 *  size of its own beside nodes that grew. */
export const FIGURE_OF_NODE = 0.85;
/** How many tiers of branches a fir is drawn with. Three is what reads as a
 *  conifer and not as a bristle: two is an arrow, four is a comb. */
export const TREE_TIERS = 3;

/** What the growth of each biome is drawn with, by the biome's code in the
 *  passport's `biomes`: the figure's name (or `none`) and the shares of
 *  places with a wood and with a meadow, per cent -- and how tall a figure
 *  stands, metres of ground, off the node's circle. */
export type Growth = { figure: string[]; woods: number[]; meadow: number[]; tallM: number };

/** The growth read off the book of constants for the passport's biomes:
 *  `biome.figure` names the figure, `biome.marks` the shares, and the
 *  seating gap (`map.min_gap_m`, through `bands.nodeRadius`) the height.
 *  Null while the book is not here or does not carry them. */
export function growthOf(
  constants: Record<string, unknown> | null | undefined,
  passport: RasterPassport | null,
): Growth | null {
  const figure = constants?.["biome.figure"] as Record<string, string> | undefined;
  const marks = constants?.["biome.marks"] as Record<string, Record<string, number>> | undefined;
  if (!figure || !marks || !passport) return null;
  return {
    figure: passport.biomes.map((name) => figure[name] ?? "none"),
    woods: passport.biomes.map((name) => Number(marks[name]?.woods ?? 0)),
    meadow: passport.biomes.map((name) => Number(marks[name]?.meadow ?? 0)),
    tallM: (FIGURE_OF_NODE * 2 * nodeRadius({ constants })) / UNITS_PER_METRE,
  };
}

/** The cell's number mixed to one share in a hundred: an integer hash over
 *  `Math.imul`, so that a scatter does not fall into rows. A linear mix of
 *  the number (`cell * 7 + (cell >> 3) * 13`) had a period of eight
 *  hundred cells and read as stripes at the thicker shares. */
export function scatter(cell: number): number {
  let h = Math.imul(cell ^ 0x9e3779b9, 0x85ebca6b) >>> 0;
  h ^= h >>> 13;
  h = Math.imul(h, 0xc2b2ae35) >>> 0;
  h ^= h >>> 16;
  //: Back to unsigned: the xor of two int32s is an int32, and a negative
  //: remainder would be under every share.
  return (h >>> 0) % 100;
}

/**
 * The figures of the growth on the near frames (D-331): over every cell of
 * the frame whose biome names a figure, a lattice of candidates
 * (`FIGURE_SPACING_M`), and on each a figure by the share the vault gives
 * that biome. Returned by figure, so each is drawn as its own path and
 * coloured by the stylesheet. The **cell** is the figure's identity, and
 * the point's place in it the rest of it: the mesh the window is read on
 * hangs off the eye and slides over the cells as the eye moves; a tree that
 * was told apart by its place in the mesh jumped to another cell at every
 * step (owner, 2026-09-11: the trees jittered as the camera moved). The
 * cell's number is the same from every eye, and so is its centre
 * (`cellCentre`); the lattice is laid from that centre.
 */
export function figures(
  rasters: Rasters,
  samples: Samples,
  passport: RasterPassport,
  growth: Growth,
  radius: number,
  /** The lattice's pitch, metres: `FIGURE_SPACING_M` on the near frames,
   *  wider on the wide ones (`contours.figureLines`). */
  spacingM = FIGURE_SPACING_M,
): Partial<Record<Figure, Segment[]>> {
  const out: Partial<Record<Figure, Segment[]>> = {};
  const { nr, nc } = samples;
  const radiusM = radius / UNITS_PER_METRE;
  const tall = growth.tallM;
  const wide = 0.35 * tall;
  const known = new Set<string>(FIGURES);
  const seen = new Set<number>();
  //: The lattice within a cell: so many points a side, the cell's width
  //: over the spacing, one at the least.
  const cellM = passport.step_m;
  const side = Math.max(1, Math.round(cellM / spacingM));
  const pitch = cellM / side;
  for (let i = 1; i < nr - 1; i++) {
    for (let j = 1; j < nc - 1; j++) {
      const point = samples.geo(i, j);
      const cell = ang2pix(passport.nside, point.lat, point.lon);
      if (seen.has(cell)) continue;
      seen.add(cell);
      const atlas = atlasIndex(passport, cell);
      const code = rasters.biome[atlas];
      const figure = growth.figure[code];
      if (!figure || !known.has(figure)) continue;
      //: Nothing under water: a wooded cell of the shore's last strip.
      if (rasters.height[atlas] < 0) continue;
      const kind = figure as Figure;
      const ground = GROUND_FIGURES.includes(kind);
      const share = ((ground ? growth.meadow[code] : growth.woods[code]) ?? 0) * (ground ? GROUND_DENSITY : FIGURE_DENSITY);
      if (share <= 0) continue;
      const here = cellCentre(passport.nside, cell);
      const cos = Math.max(Math.cos(here.lat * RAD), 0.05);
      //: Metres of ground into degrees, at this latitude.
      const north = (m: number) => here.lat + m / radiusM / RAD;
      const east = (m: number) => here.lon + m / (radiusM * cos) / RAD;
      const list = (out[kind] ??= []);
      //: Each point of the lattice its own number -- the cell's and its
      //: place in it -- hashed: the share says whether a figure stands
      //: there, and two more hashes jitter it within its pitch, so the
      //: figures do not stand in rows, and stand still.
      for (let a = 0; a < side; a++) {
        for (let b = 0; b < side; b++) {
          const spot = cell * side * side + a * side + b;
          if (scatter(spot) >= share) continue;
          const dx = (a + 0.5 - side / 2) * pitch + (scatter(spot + 0x5bd1e995) / 100 - 0.5) * pitch * 0.8;
          const dy = (b + 0.5 - side / 2) * pitch + (scatter(spot ^ 0x27d4eb2f) / 100 - 0.5) * pitch * 0.8;
          const at = (up: number, sideM: number): Geo => ({
            lat: north(dy + up),
            lon: east(dx + sideM),
          });
          drawFigure(kind, list, at, tall, wide);
        }
      }
    }
  }
  return out;
}

/** A closed ring of chords about a centre `up` high, `r` in radius, a
 *  little wider than tall: the crown of a broadleaf, the head of a bush. */
function ring(list: Segment[], at: (up: number, side: number) => Geo, up: number, r: number, sides: number) {
  let last = at(up + r, 0);
  for (let k = 1; k <= sides; k++) {
    const angle = (k / sides) * 2 * Math.PI;
    const next = at(up + r * Math.cos(angle), 1.15 * r * Math.sin(angle));
    list.push([last, next]);
    last = next;
  }
}

/**
 * One figure, as strokes from its foot: the marks of a topographic sheet,
 * each a few strokes so that it reads as what it is at ten pixels and as a
 * scatter of its colour at three. `tall` is the figure's height on the
 * ground (`Growth.tallM`, a little under the node's circle), `wide` its
 * half-width.
 */
export function drawFigure(
  figure: Figure,
  list: Segment[],
  at: (up: number, side: number) => Geo,
  tall: number,
  wide: number,
): void {
  switch (figure) {
    case "fir": {
      //: The trunk, and then a crown: three tiers narrowing to the top, the
      //: way a conifer is drawn on every map there has ever been. One
      //: stroke apiece, so a tree is a figure and not the letter A.
      list.push([at(0, 0), at(tall, 0)]);
      for (let tier = 0; tier < TREE_TIERS; tier++) {
        const share = tier / TREE_TIERS;
        const up = tall * (0.3 + 0.7 * share);
        const arm = wide * (1 - share);
        const peak = at(up + 0.22 * tall, 0);
        list.push([at(up, -arm), peak], [peak, at(up, arm)]);
      }
      return;
    }
    case "broadleaf": {
      //: A trunk and a round head: the deciduous tree of the sheet.
      list.push([at(0, 0), at(0.45 * tall, 0)]);
      ring(list, at, 0.7 * tall, 0.3 * tall, 8);
      return;
    }
    case "palm": {
      //: A trunk that leans, and fronds that fall away from the top.
      const top = at(tall, 0.15 * tall);
      list.push([at(0, 0), at(0.5 * tall, 0.07 * tall)], [at(0.5 * tall, 0.07 * tall), top]);
      for (const [side, drop] of [[-1, 0.05], [1, 0.05], [-0.55, 0.2], [0.55, 0.2]] as const) {
        const mid = at(tall + 0.18 * tall * (1 - Math.abs(side)) + 0.05 * tall, 0.15 * tall + side * 0.55 * wide);
        list.push([top, mid], [mid, at(tall - drop * tall - 0.15 * tall, 0.15 * tall + side * 1.1 * wide)]);
      }
      return;
    }
    case "acacia": {
      //: A trunk and a flat, wide head: the umbrella of the savanna.
      const neck = 0.6 * tall;
      const brim = 0.78 * tall;
      list.push([at(0, 0), at(neck, 0)]);
      list.push([at(brim, -1.1 * wide), at(brim, 1.1 * wide)]);
      list.push([at(neck, 0), at(brim, -0.8 * wide)], [at(neck, 0), at(brim, 0.8 * wide)]);
      list.push([at(brim, -1.1 * wide), at(brim + 0.14 * tall, 0)], [at(brim + 0.14 * tall, 0), at(brim, 1.1 * wide)]);
      return;
    }
    case "bush": {
      //: A low round head on the ground.
      ring(list, at, 0.22 * tall, 0.2 * tall, 6);
      return;
    }
    case "grass": {
      //: Three blades fanning from one foot.
      list.push([at(0, 0), at(0.35 * tall, -0.5 * wide)]);
      list.push([at(0, 0), at(0.42 * tall, 0)]);
      list.push([at(0, 0), at(0.35 * tall, 0.5 * wide)]);
      return;
    }
    case "reed": {
      //: Two stems and a head: the cattail of the marsh.
      list.push([at(0, -0.25 * wide), at(0.5 * tall, -0.25 * wide)]);
      list.push([at(0, 0.3 * wide), at(0.42 * tall, 0.3 * wide)]);
      list.push([at(0.5 * tall, -0.25 * wide), at(0.68 * tall, -0.25 * wide)]);
      return;
    }
    case "moss": {
      //: Two short dashes: the lichen of the tundra, close to the ground.
      list.push([at(0.05 * tall, -0.6 * wide), at(0.05 * tall, 0.6 * wide)]);
      list.push([at(0.16 * tall, -0.35 * wide), at(0.16 * tall, 0.35 * wide)]);
      return;
    }
  }
}
