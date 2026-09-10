// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The bands of scale (D-319, wave 4): what a height shows, and where one
 * band hands over to the next.
 *
 * The layers of the old map were tabs; the bands are heights. Far out the
 * map is the **sky** -- the system in its own units, planets as markers
 * (D-320). Close in it is the **surface** -- the globe of one planet, and
 * on it a city is a point until the eye comes near enough to see its
 * streets, when it opens into its nodes with its border drawn round them.
 * The **inside** -- floors, the rooms of a hull -- is not a height at all:
 * it has no north, and it is a window one opens from a node (plan §2, item 4).
 *
 * The two coordinate systems do not meet (plan §2, item 2): the sky draws
 * a planet as a marker of the glyph's size, the surface the true sphere. So
 * the hand-over is a cut between two disks of one size (`openScale`), and
 * the **approach** (wave 5) is what happens below it: the descent by scale
 * (`descentOf`) tilts the eye from over the pole down to where one stands
 * (`tilted`). Pure numbers and functions, so a test can zoom.
 */

import { LAST_LAT, UNITS_PER_METRE, type Eye } from "./globe";
import { H, SPHERE_R, W, type Point } from "./model";

export type Band = "sky" | "surface" | "inside";

/** How far in and out the hand may zoom in a band. */
export type Bounds = { nearest: number; furthest: number };
export const SKY_BOUNDS: Bounds = { nearest: 4, furthest: 0.4 };
export const INSIDE_BOUNDS: Bounds = { nearest: 4, furthest: 0.4 };
/** How near the surface may be zoomed: the city's own scale times this. */
export const SURFACE_NEAREST = 4;
/** The scale a city's streets are drawn at: the flat map's own. */
export const STREET_SCALE = 1;
/** The relief's cell, degrees: the terrain grid is two degrees. */
export const CELL_DEG = 2;
/** From this drawn cell and finer the ground is read off the tiles of the
 *  local relief (D-323): a sixteenth of a grid cell, the frame under some
 *  thirty kilometres on a small world. */
export const TILE_UNIT = 1 / 16;
/** The finest reading of the ground, a thirty-second of a grid cell: one
 *  halving under `TILE_UNIT`, so the tiles' features bend. */
export const FINEST_UNIT = 1 / 32;
/** The drawn cell is halved while the frame holds fewer than this many. */
const HALVE_BELOW = 24;
const RAD = Math.PI / 180;

/**
 * How the ground is drawn for a frame at `scale` over a planet of `radius`:
 * the drawn cell in cells of the grid -- one while the frame holds many,
 * halved as it holds fewer, down to `FINEST_UNIT` -- and whether it is
 * drawn at all: it is, wherever there is a radius. From the radius, not from a scale: a scale that meant
 * "several cells across" on a planet of Terra's old size meant "more than
 * the whole disk" on one a twentieth of it (D-322), and the ground went
 * flat long before the coast could be seen. Powers of two, so the fact
 * flips a few times on the way down and not at every frame.
 */
export function groundOf(
  scale: number,
  radius: number | null,
): { unit: number; shown: boolean } {
  if (!radius || !(scale > 0)) return { unit: 1, shown: false };
  const span = W / scale;
  const cell = radius * CELL_DEG * RAD;
  let unit = 1;
  while (unit > FINEST_UNIT && span < HALVE_BELOW * cell * unit) unit /= 2;
  //: Drawn at every frame, the streets' included: inside a cell the cut
  //: still says where the lake's shore and the mountain's foot run, and a
  //: frame inside one cell costs a few polygons.
  return { unit, shown: true };
}

/**
 * How far from the eye the ground is laid at this drawn cell, in map units:
 * half the widest frame the cell serves, so that the ground laid at one
 * frame still covers every frame up to the next halving. Not the frame's
 * own width -- the ground is redrawn when a fact flips, not at every notch
 * of the zoom, and a window cut to the frame of the last flip would leave
 * the land short of the edge on the way out. A whole cell has no limit:
 * the frame then holds the disk, or most of it.
 */
export function groundReach(
  unit: number,
  radius: number | null,
): number | undefined {
  if (!radius || unit >= 1) return undefined;
  return HALVE_BELOW * radius * CELL_DEG * RAD * unit;
}
/** How much of the frame's height the globe takes when the sky opens a
 *  surface: a little more than all of it, so the marker becomes the disk. */
export const GLOBE_FILL = 1.2;

/** The scale the surface opens at from the sky, from the planet's radius in
 *  map units: the disk `GLOBE_FILL` of the frame high. Without a radius the
 *  scene is flat, and it opens at the streets' scale as it always did. */
export function globeScale(radius: number | null): number {
  if (!radius) return STREET_SCALE;
  return (H * GLOBE_FILL) / (2 * radius);
}

/**
 * The floor of the surface band from the vault's height (`map.approach_km`,
 * D-319): the camera has no height of its own yet -- it has a scale -- so
 * the height is read as what a right-angled view from it takes in, twice
 * the height across the frame. Below the floor, the sky. Until the book has
 * come there is no height to read, and the floor is set under any planet's
 * disk so that a surface opened from the sky does not at once fall back.
 */
export function surfaceFloor(approachKm: number): number {
  if (!(approachKm > 0)) return UNBOOKED_FLOOR;
  return H / (2 * approachKm * 1000 * UNITS_PER_METRE);
}
/** The floor before the book has come: under any planet's disk. */
const UNBOOKED_FLOOR = 1e-6;

export function surfaceBounds(approachKm: number): Bounds {
  return { nearest: SURFACE_NEAREST, furthest: surfaceFloor(approachKm) };
}

/** How far out the map tab zooms, as a share of the scale the globe fills
 *  the frame at: the disk a little over half the frame's height, and no
 *  farther -- the map has no sky (owner, 2026-09-06). */
export const MAP_FURTHEST = 0.5;

/**
 * The surface's bounds on the map tab: the planet is the whole of it. The
 * sky is the ship's console's alone, for finding one's way between the
 * worlds; from the ground the map stops at the disk, so that the stitch
 * between two drawings of one planet is never seen. Without a radius the
 * scene is flat, and it keeps the old floor.
 */
export function mapBounds(globe: number, radius: number | null): Bounds {
  if (!radius) return { nearest: SURFACE_NEAREST, furthest: UNBOOKED_FLOOR };
  return { nearest: SURFACE_NEAREST, furthest: globe * MAP_FURTHEST };
}

/**
 * The approach (D-319, wave 5, plan §2 "Камера"): between the floor of the
 * surface and the scale the globe fills the frame at, the descent -- 1 at
 * the floor, 0 at the globe and nearer, by the log of the scale, so that
 * every octave of the zoom tilts as much. Told in steps, because it is a
 * fact of the frame and React draws only when a fact flips.
 */
export const DESCENT_STEPS = 24;
export function descentOf(scale: number, floor: number, globe: number): number {
  if (!(globe > floor) || scale >= globe) return 0;
  if (scale <= floor) return 1;
  const share = Math.log(globe / scale) / Math.log(globe / floor);
  return Math.round(share * DESCENT_STEPS) / DESCENT_STEPS;
}

/**
 * The eye as the descent shows it: high up, from over the pole -- the sky
 * looks at the ecliptic from its north, and the planet's axis stands
 * perpendicular to it -- and, coming down, tilting to where the eye stands,
 * north up all the way. Smooth at both ends, so the tilt neither jerks off
 * the marker nor lands with a bump.
 */
export function tilted(eye: Eye, descent: number): Eye {
  if (descent <= 0) return eye;
  const t = Math.min(1, descent);
  const ease = t * t * (3 - 2 * t);
  return { lat: eye.lat + (LAST_LAT - eye.lat) * ease, lon: eye.lon };
}

/** The scale the surface opens at from the sky: where the true disk is the
 *  size of the marker at the sky's ceiling, so that the one becomes the
 *  other -- but never on the floor itself, or it would fall straight back:
 *  a planet whose disk would be the marker's size only below the floor
 *  (the larger ones, at the vault's approach height) opens a notch above
 *  it, the disk a little larger than the marker. */
export const ABOVE_FLOOR = 1.2;
export function openScale(radius: number | null, floor: number): number {
  if (!radius) return STREET_SCALE;
  return Math.max(
    floor * ABOVE_FLOOR,
    (SPHERE_R * SKY_BOUNDS.nearest) / radius,
  );
}

/** At this scale and nearer a city opens into its nodes; farther, it is a
 *  point with a name -- the printer's (plan §2). */
export const CITY_SCALE = 0.25;

/**
 * How far apart two nodes stand, counted in the radius of the circle each is
 * drawn as: **five**, and the circle is sized from that rather than the other
 * way round (owner, 2026-09-08).
 *
 * The engine seats a node no nearer than `map.min_gap_m` to anybody else, and
 * that gap is the map's floor: get the circle bigger than a fifth of it and
 * neighbours start covering one another. The two numbers used to be chosen
 * apart -- six units against six metres -- and held only by arithmetic
 * nobody checked: `6 m x UNITS_PER_METRE / 6 units` came to exactly five, and
 * a hand lowering the gap or widening the circle would have crowded the map
 * with nothing to say so. Derived, the promise cannot come untrue: a tighter
 * gap draws smaller circles.
 */
export const GAP_RADII = 5;
//: What the circle may not shrink below or grow past whatever the vault says:
//: three units is still a dot one can aim at, nine is the widest that reads
//: as a node rather than as a city.
export const NODE_R_MIN = 3;
export const NODE_R_MAX = 9;
//: Until the book arrives -- the value today's gap comes to, so the first
//: frame is not drawn in some other size and then re-drawn.
export const NODE_R = 6;

/**
 * The radius a node is drawn with, map units: the seating gap over
 * `GAP_RADII`. `map.min_gap_m` comes with the constants the client already
 * reads (D-209), so nothing new travels for this.
 */
export function nodeRadius(
  book: { constants?: Record<string, unknown> | null } | null,
): number {
  const gap = Number(book?.constants?.["map.min_gap_m"]);
  if (!Number.isFinite(gap) || gap <= 0) return NODE_R;
  return Math.min(
    NODE_R_MAX,
    Math.max(NODE_R_MIN, (gap * UNITS_PER_METRE) / GAP_RADII),
  );
}

/**
 * The scale the frame stands at this far out, near enough: the closing scale
 * halved every octave, and `far` counts half-octaves.
 *
 * Not the frame's own scale on purpose. What is drawn about a closed city --
 * its circle, its name -- is wanted at a size in **pixels**, and the map's
 * own units are pixels only at scale one; dividing by the true scale would
 * redraw every city at every notch of the zoom. `far` flips a few times on
 * the way out, and this is the scale it flipped at.
 */
export function scaleAt(far: number): number {
  return CITY_SCALE * 2 ** (-far / 2);
}

/** How tall a province's name is drawn, map units: the same pixels at every
 *  distance, for the same reason a closed city's name is (`cityLabelEm`).
 *  A name of a land that shrank with the zoom would go unreadable exactly
 *  on the frame where lands are all there is (plan §9.7). Smaller than a
 *  city's: a province is the ground a city stands on, not a place to go. */
export const PROVINCE_LABEL_PX = 11;
export function provinceLabelEm(far: number): number {
  return PROVINCE_LABEL_PX / scaleAt(far);
}

/**
 * How far out the frame is past the cities' closing, in half-octaves: 0
 * where the cities close, 2 at twice that span, 4 at four times. A fact of
 * the frame rather than the scale itself, so the map redraws its cities a
 * few times on the way out and not at every notch.
 */
export function farOf(scale: number): number {
  const closing = W / CITY_SCALE;
  const span = W / Math.max(scale, 1e-12);
  return Math.max(0, Math.round(2 * Math.log2(span / closing)));
}

/**
 * A closed city's radius **in pixels**: its count of nodes at the closing,
 * held between what is still a city and what fits beside its neighbours,
 * and a little larger the farther out -- from afar the cities are the map
 * (owner, 2026-09-06: as a web map shows the great cities first).
 *
 * The growth **saturates**, and that is the whole of this arithmetic. Linear
 * in half-octaves it multiplied the mark by five between the city's closing
 * and a frame eighty kilometres wide -- measured on the running map: eleven
 * pixels at the closing, forty-seven at eighty kilometres -- and over the
 * ground the shader draws now (landscape plan wave 5) that is a blot, not a
 * mark, with a name of thirteen pixels beside it (owner, 2026-09-09:
 * «абстрактный узел города становится очень большим при зуме»). It grows
 * towards `CITY_GROWTH` of its own size, half the way there at
 * `CITY_GROWTH_HALF` half-octaves out.
 *
 * Saturating was not enough, and the owner said so again 2026-09-10:
 * «абстрактный узел столицы скейлится очень большим, надо чтобы скейлился
 * слабее». Measured on the running map at that word: the capital counts
 * twenty-six nodes, so its mark stood at twenty-six pixels where the cities
 * close and thirty-seven and a half on the planet -- and a tenth of the
 * planet's own radius. `CITY_GROWTH` went from three fifths to one fifth,
 * so the mark grows by a seventh from the streets to a frame of ninety
 * kilometres and by a fifth in the limit.
 *
 * The growth is only half of what the eye sees, and the smaller half. What
 * a city is drawn at on the far frames is `cityRadius`, where the share of
 * the globe takes over and brings the mark down to its floor: 26 pixels at
 * the closing, 30 at ninety kilometres, 14 on the planet.
 */
export const CITY_R_MIN = 14;
//: And the largest a city's **base** is, whatever its count of nodes: past
//: this a great city says only that it is great, and forty pixels is already
//: three of the gaps its own nodes stand at.
export const CITY_BASE_MAX = 40;
export const CITY_GROWTH = 0.2;
export const CITY_GROWTH_HALF = 6;
//: The largest a city is drawn whatever its size and however far out. Not a
//: number of its own: the biggest base grown by the whole of `CITY_GROWTH`,
//: which is the limit the growth walks towards and never reaches. It was a
//: literal 48 and a real clip -- at a growth of three fifths the biggest city
//: ran into it and every city past 30 nodes was drawn the same -- and with
//: the growth at a fifth the two meet, so the ceiling stopped clipping and
//: became what it always said it was.
export const CITY_R_MAX = CITY_BASE_MAX * (1 + CITY_GROWTH);
export function cityPixels(size: number, far: number): number {
  const base = Math.min(CITY_BASE_MAX, Math.max(CITY_R_MIN, size));
  const growth = 1 + (CITY_GROWTH * far) / (far + CITY_GROWTH_HALF);
  return Math.min(CITY_R_MAX, base * growth);
}

/**
 * A closed city's radius in **map units**, which is what the drawing takes.
 *
 * The units of this map are pixels only at scale one, and a closed city is
 * looked at from a long way outside it: drawn in units the circle shrank with
 * the zoom until the capital was a speck of a pixel, growing by `CITY_GROWTH`
 * against a scale halving every octave (owner, 2026-09-08: «абстрактный узел
 * города слишком маленький»). Divided by the scale of its band, it holds its
 * size on the glass and the growth means what it says.
 */
export function cityRadius(
  size: number,
  far: number,
  globe: number | null = null,
): number {
  const units = cityPixels(size, far) / scaleAt(far);
  //: And no larger than a share of the planet itself. The pixel ceiling
  //: `CITY_R_MAX` keeps the circle within reason on a screen, but on the globe
  //: a screen is a whole world: from afar the capital grew over half the
  //: planet (owner, 2026-09-08). A city is a place on a planet, and it must
  //: look like one.
  if (!globe || globe <= 0) return units;
  //: **And never smaller than a mark.** The share of the globe shrinks with
  //: the frame while the mark holds its pixels, so past some frame the share
  //: is the smaller of the two and goes on halving every octave -- which is
  //: exactly the shrinking-to-a-speck this function was written to stop
  //: (owner, 2026-09-08: the city node is too small). Without this floor a
  //: share of a twenty-fifth drew the capital at seven pixels on the map's
  //: farthest frame. The floor is `CITY_R_MIN`, the same pixels that say
  //: what still reads as a city, so the two rules meet instead of arguing:
  //: the share brings the mark down as the world comes into the frame, and
  //: sets it down on the floor rather than through it.
  return Math.max(CITY_R_MIN / scaleAt(far), Math.min(units, globe * CITY_OF_GLOBE));
}

//: What share of the planet's radius a city's circle never passes.
//:
//: An eighth was a continent, and it never bound at all: on the frame that
//: shows the planet whole the capital came out a tenth of the world's own
//: radius, which is the shape of a sea and not of a town (owner, 2026-09-10:
//: the capital's mark scales too big, make it scale weaker). A twenty-fifth
//: is a third of that. It starts to bind two notches inside the planet's
//: frame, so the mark is not clipped at one frame and whole at the next --
//: it comes down over three half-octaves, 30 pixels to 27 to 19, as the
//: world comes into the frame, and lands on the floor of `cityRadius` at 14.
//:
//: On Earth's radius a twenty-fifth would be two hundred and fifty
//: kilometres: a great city with its country around it.
export const CITY_OF_GLOBE = 0.04;

/**
 * The mark of the node one stands in, **map units**, when the cities are
 * closed.
 *
 * Everything else on a closed map is a city, drawn at a size in pixels; the
 * node underfoot is an ordinary node and its six units shrank to nothing a
 * notch out (owner, 2026-09-08: «при зуме издали мы продолжаем показывать
 * узел, в котором находится игрок»). Keeping it is only half the promise —
 * it has to be **seen**.
 */
export const HERE_R_PX = 7;
export function hereRadius(far: number): number {
  return HERE_R_PX / scaleAt(far);
}

/** How tall a closed city's name is drawn, map units: the same pixels at
 *  every distance, for the same reason as the circle. A name that shrinks
 *  with the zoom is a name nobody can read from where cities are all there
 *  is. */
export const CITY_LABEL_PX = 13;
export function cityLabelEm(far: number): number {
  return CITY_LABEL_PX / scaleAt(far);
}

/**
 * Whether a closed city of `size` nodes is still drawn this far out: the
 * frame may hold one node of it per `KM_PER_NODE` of its span, so a hamlet
 * fades a few hundred kilometres out and the capital never does.
 */
export const KM_PER_NODE = 200;
export function citySeen(size: number, far: number): boolean {
  const spanKm = (W / CITY_SCALE / UNITS_PER_METRE / 1000) * 2 ** (far / 2);
  return size >= Math.ceil(spanKm / KM_PER_NODE);
}
/** In the sky, zoomed all the way in on a planet, the surface opens: the
 *  marker within this many map units of the frame's middle. */
export const OPEN_REACH = 60;

export function boundsOf(band: Band, surface: Bounds): Bounds {
  if (band === "sky") return SKY_BOUNDS;
  if (band === "inside") return INSIDE_BOUNDS;
  return surface;
}

/** Whether cities are open at this scale. */
export function cityOpen(scale: number): boolean {
  return scale >= CITY_SCALE;
}

/** Whether the surface, zoomed out this far, hands over to the sky. */
export function leavesSurface(scale: number, floor: number): boolean {
  return scale <= floor;
}

/** Whether the sky, zoomed in this close, is asking to open a planet. */
export function reachesSurface(scale: number): boolean {
  return scale >= SKY_BOUNDS.nearest;
}

/** The planet under the middle of the frame, if one is close enough to open. */
export function planetUnder(
  middle: Point,
  spheres: readonly { key: string; planet: string; at: Point }[],
): { key: string; planet: string } | null {
  let best: { key: string; planet: string } | null = null;
  let nearest = OPEN_REACH;
  for (const sphere of spheres) {
    const away = Math.hypot(sphere.at.x - middle.x, sphere.at.y - middle.y);
    if (away <= nearest) {
      nearest = away;
      best = { key: sphere.key, planet: sphere.planet };
    }
  }
  return best;
}
