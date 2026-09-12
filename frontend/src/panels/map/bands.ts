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
 *  two and a half kilometres on a world this small. */
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
/** The frame's width in half-octaves of map units (`Facts.near`): what is
 *  cut to the frame itself -- the figures of the growth -- is cut again
 *  when this steps, and React hears of the frame only through it. Not the
 *  scale: a fact that changed at every notch would re-render the map at
 *  every notch, and a half-octave is the step the far frames already take
 *  (`farOf`). */
export function nearOf(scale: number): number {
  return Math.round(2 * Math.log2(W / Math.max(scale, 1e-9)));
}
/** The frame's width at that step, metres of ground. */
export function nearFrameM(near: number): number {
  return 2 ** (near / 2) / UNITS_PER_METRE;
}
/** How much of the frame's height the globe takes when the sky opens a
 *  surface: a little more than all of it, so the marker becomes the disk. */
export const GLOBE_FILL = 1.2;

/** The scale the surface opens at from the sky, from the planet's radius in
 *  map units: the disk `GLOBE_FILL` of the frame high. Without a radius the
 *  scene is flat, and it opens at the streets' scale as it always did. */
export function globeScale(radius: number | null, tall = H): number {
  if (!radius) return STREET_SCALE;
  return (tall * GLOBE_FILL) / (2 * radius);
}

/**
 * The floor of the surface band from the vault's height (`map.approach_km`,
 * D-319): the camera has no height of its own yet -- it has a scale -- so
 * the height is read as what a right-angled view from it takes in, twice
 * the height across the frame. Below the floor, the sky. Until the book has
 * come there is no height to read, and the floor is set under any planet's
 * disk so that a surface opened from the sky does not at once fall back.
 */
export function surfaceFloor(approachKm: number, tall = H): number {
  if (!(approachKm > 0)) return UNBOOKED_FLOOR;
  return tall / (2 * approachKm * 1000 * UNITS_PER_METRE);
}
/** The floor before the book has come: under any planet's disk. */
const UNBOOKED_FLOOR = 1e-6;

export function surfaceBounds(approachKm: number, tall = H): Bounds {
  return { nearest: SURFACE_NEAREST, furthest: surfaceFloor(approachKm, tall) };
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
 *  city's: a province is the ground a city stands on, not a place to go --
 *  and smaller again since 2026-09-11 (owner: «сделай так чтобы текст
 *  биомов меньше»), because a dozen of them crowded the globe. */
export const PROVINCE_LABEL_PX = 8;
export function provinceLabelEm(far: number): number {
  return PROVINCE_LABEL_PX / scaleAt(far);
}

/** The smallest share of its size a name is drawn at when it lies on the
 *  limb of the globe rather than under the eye. */
export const LIMB_LABEL_SHARE = 0.45;

/**
 * How large a name at this point of the sphere is drawn, as a share of its
 * full size: full under the eye, `LIMB_LABEL_SHARE` at the limb.
 *
 * The land itself is foreshortened towards the edge -- a province a
 * thousand kilometres across is a sliver there -- while its name was drawn
 * the same size wherever it fell, so the names at the rim were the loudest
 * thing on the globe and belonged to the least of it (owner, 2026-09-11:
 * «чтобы он скейлился от того в центре экрана они или с края»).
 *
 * The share is the foreshortening itself, `sqrt(1 - (r/R)^2)` -- the cosine
 * of the angle off the eye -- softened by a floor so a rim name stays
 * legible instead of vanishing. Near frames are untouched by arithmetic
 * rather than by a branch: there the whole visible patch sits under the eye,
 * `r/R` is a rounding error, and the share is one.
 */
export function limbShare(x: number, y: number, radius: number): number {
  if (!(radius > 0)) return 1;
  const off = Math.min(1, Math.hypot(x, y) / radius);
  return LIMB_LABEL_SHARE + (1 - LIMB_LABEL_SHARE) * Math.sqrt(1 - off * off);
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
 * What a city is drawn at is `cityRadius`, and nothing takes over from it:
 * the share of the planet's radius that used to cap the mark is gone
 * (D-329 addendum, 2026-09-11) -- see the note above `cityRadius` for why
 * the two rules turned out to be one.
 */
export const CITY_R_MIN = 7;
//: And the largest a city's **base** is, whatever its count of nodes: past
//: this a great city says only that it is great, and twenty pixels is
//: already two of the gaps its own nodes stand at.
export const CITY_BASE_MAX = 20;
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
export function cityRadius(size: number, far: number, scale: number): number {
  //: Divided by the frame's **own** scale, not the band's. Held to the
  //: band's, the mark swelled by half between one notch of the zoom and the
  //: next and snapped back at the notch: it breathed (owner, 2026-09-11:
  //: make it stop scaling when zoomed out). `far` still decides the growth,
  //: because how much a great city outgrows a hamlet should not flicker.
  return cityPixels(size, far) / (scale > 0 ? scale : scaleAt(far));
}

//: There is no share of the globe any more, and its going is worth a word.
//:
//: The mark used to be capped at a fraction of the planet's radius, because
//: held to a constant size in **units** it grew over half the world from
//: afar. Held to a constant size in **pixels**, as it is now, it cannot: the
//: frames of the map are the planet's own radius (`groundReach`), so every
//: planet fills the same part of the glass at the same `far`, and a mark of
//: seven pixels is the same small share of every world at every frame. Two
//: rules that argued -- a size on the glass against a size on the world --
//: turned out to be one once the first was measured on the glass for real.

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

/**
 * Whether the node underfoot needs a mark of its own on a closed map.
 *
 * Only where it stands for itself. Inside a city it does not: the city's own
 * mark is drawn at that very point and already wears `me`, so a second mark
 * put the dark dot of the node over the light dot of the city -- ten pixels
 * of market on eleven of capital -- and from orbit one's own city read as a
 * black hole (seen in the running game, 2026-09-11).
 *
 * `home` is what the node underfoot delegates to in this scene: itself out
 * in the wild, its city inside one.
 */
export function standsAlone(here: string | null, home: string | null): boolean {
  return home === null || home === here;
}

/** How tall a closed city's name is drawn, map units: the same pixels at
 *  every distance, for the same reason as the circle. A name that shrinks
 *  with the zoom is a name nobody can read from where cities are all there
 *  is. */
export const CITY_LABEL_PX = 11;
export function cityLabelEm(far: number, scale: number): number {
  return CITY_LABEL_PX / (scale > 0 ? scale : scaleAt(far));
}

/**
 * Whether a closed city of `size` nodes is still drawn this far out: the
 * frame may hold one node of it per `KM_PER_NODE` of its span, so a hamlet
 * fades a few hundred kilometres out and the capital never does.
 *
 * Divided by four when the planets were shrunk sixteenfold by area
 * (2026-09-10), and by two again when they were shrunk fourfold more
 * (D-329). The frames of the map are the planet's radius (`groundReach`),
 * and this rule is the only one on them written in absolute kilometres -- so
 * it is the only one that has to be moved by hand every time the radius
 * changes. It found itself here again on the very next change, which is the
 * comment above working as intended. Twenty-five keeps what fifty and two
 * hundred kept: two nodes on the planet's own frame, fifteen on the farthest
 * the map goes.
 */
export const KM_PER_NODE = 25;
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
