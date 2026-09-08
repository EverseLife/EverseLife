// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The geometry of the bridge display (D-240): where the sky is drawn on it and
 * how near it is looked at.
 *
 * The chart used to be a picture of the system: the star in the middle of the
 * frame, the outermost orbit fitted to the edges, everything at one size for
 * ever. That is a poster, and a poster answers one question -- "what does the
 * system look like" -- which nobody at a console is asking.
 *
 * A display answers "where am I and what is around me", so it is built the
 * other way round:
 *
 * * **the hull is the middle**, always. There is no panning and there must not
 *   be: a frame that can be dragged off the ship is a frame one has to drag
 *   back, and the one thing an instrument may never do is lose its own vessel;
 * * **the near is chosen**, by wheel, pinch or the two loupes. Zooming is the
 *   whole of what the hand may do here -- the world map's camera allows exactly
 *   this much while it is tethered (D-238), and this camera is tethered always;
 * * **the marks do not grow with it**. The zoom is applied to *places*, not to
 *   the drawing: a planet's dot, its name and every hairline stay the size the
 *   eye needs, and only the distances between them open up. Drawn the usual way
 *   -- one `viewBox` over a scaled scene -- a dot at the near end of the zoom
 *   would be a saucer and the names would not fit the frame.
 *
 * Hence a projection rather than a `viewBox`: everything here turns map units,
 * which is what the server draws its lines in, into the display's pixels.
 */

import { along } from "../map/orbits";

export type Point = { x: number; y: number };

/** The display's frame. Not the map's: this is an instrument, not a scene. */
export const W = 640;
export const H = 400;

/** Where the hull's own mark stands. It does not move: the display is it. */
export const CENTER: Point = { x: W / 2, y: H / 2 };

/** How much of the frame is left round the system at rest. */
const MARGIN = 40;

/**
 * How near and how far the sky may be looked at, and what one notch changes.
 *
 * At rest the frame holds the outermost orbit's **diameter** -- which is the
 * hull's own half of the system, not all of it: the middle is the ship, and
 * from a hull out on that orbit the far side is twice as far again and stands
 * on the edge as a bearing.
 *
 * The near end is set by the smallest thing on this display that matters, and
 * that is not a planet: hulls see each other across `orbit.sight_radius`, and
 * they dock inside `orbit.dock_radius` -- five map units and a fifth of one,
 * against a system eight hundred across. At rest the sight radius is two
 * pixels. So the zoom runs to three hundred, where it is most of the frame and
 * a rendezvous can actually be flown; anything less and the near end of this
 * instrument stops before the work it exists for begins.
 *
 * The map's own limits (`map/hand`) are far narrower because the map may also
 * be dragged: there, going far in is a way of getting lost, and here there is
 * nothing to get lost from -- the hull is the middle whatever happens.
 */
export const FURTHEST = 0.4;
export const NEAREST = 300;
//: The same notch the world map turns by (`map/hand`): one click of a wheel
//: must mean the same thing on both drawings of one client.
const NOTCH = 1.15;

/**
 * Where a zoom stands on the slider, from 0 to 1, and the zoom at a place on
 * it. The scale is multiplicative, so the slider is **logarithmic**: a notch
 * of the wheel moves it the same distance wherever it stands, and the thumb
 * spends as much of its travel on the near end as on the far one. Linear, the
 * first tenth of the slider would hold everything from the whole system to a
 * planet and the other nine tenths would hold nothing at all.
 */
export function partOf(zoom: number): number {
  return Math.log(clampZoom(zoom) / FURTHEST) / Math.log(NEAREST / FURTHEST);
}

export function zoomAt(part: number): number {
  return clampZoom(FURTHEST * (NEAREST / FURTHEST) ** Math.min(1, Math.max(0, part)));
}

/** A zoom kept within what the display may show. */
export function clampZoom(zoom: number): number {
  return Math.min(NEAREST, Math.max(FURTHEST, zoom));
}

/** A notch of the wheel, or a press of a loupe: `steps` in, negative out. */
export function zoomBy(zoom: number, steps: number): number {
  return clampZoom(zoom * NOTCH ** steps);
}

/**
 * The zoom a pinch asks for: the fingers' spread now against their spread when
 * the pinch began. Measured from the start rather than from the last move, so
 * a hundred rounded steps do not drift away from the fingers.
 */
export function pinchZoom(zoom0: number, spread0: number, spread: number): number {
  if (spread0 <= 0) return clampZoom(zoom0);
  return clampZoom(zoom0 * (spread / spread0));
}

/**
 * What the display is looking at: the hull's own place, in map units, and how
 * near it is being looked at.
 *
 * There is no offset here, and there used to be: the hull was drawn a fixed
 * sixteen or twenty-six **pixels** off its planet's dot so that the two could
 * be told apart (D-245). A fixed pixel gap is a lie the zoom cannot correct --
 * one looks nearer and nearer at a moored hull and it never leaves its planet,
 * because the gap was never a distance. It is a distance now
 * (`orbit.park_radii` of the world's own body, D-324), in the same map units
 * as everything else, and it opens up with the zoom like everything else.
 */
export type Scope = {
  at: Point;
  /** Map units to pixels at rest. */
  unit: number;
  zoom: number;
};

/** Pixels per map unit at rest: the outermost orbit's diameter across the frame. */
export function unitFor(reach: number): number {
  return reach > 0 ? (H - 2 * MARGIN) / (2 * reach) : 1;
}

/** A place in map units, where the display draws it. */
export function project(scope: Scope, x: number, y: number): Point {
  const k = scope.unit * scope.zoom;
  return {
    x: CENTER.x + (x - scope.at.x) * k,
    y: CENTER.y + (y - scope.at.y) * k,
  };
}

/**
 * How far apart the graticule's lines stand, in map units.
 *
 * The ruling is of the sky, not of the glass: it slides under the hull and
 * opens up with the zoom, which is the whole of what makes movement and scale
 * visible on a display that never pans. A step fixed in map units would be a
 * wall of lines at one end of the zoom and none at the other, so it climbs the
 * 1-2-5 ladder: the rung whose spacing on the glass is nearest above `WANT`.
 */
const WANT = 48;

export function gridStep(scope: Scope): number {
  const raw = WANT / (scope.unit * scope.zoom);
  const rung = 10 ** Math.floor(Math.log10(raw));
  for (const times of [1, 2, 5]) {
    if (rung * times >= raw) return rung * times;
  }
  return rung * 10;
}

/**
 * Whether a circle drawn about `at` crosses the glass at all.
 *
 * At the near end of the zoom an orbit is a circle forty thousand pixels
 * across whose middle is far off the frame; most of them cannot be seen and
 * none of them should be handed to the renderer to find that out.
 */
export function ringSeen(at: Point, radius: number): boolean {
  const away = Math.hypot(CENTER.x - at.x, CENTER.y - at.y);
  return Math.abs(away - radius) <= Math.hypot(W, H) / 2;
}

/** How long a map-unit length is on the display right now. */
export function span(scope: Scope, length: number): number {
  return length * scope.unit * scope.zoom;
}

/** A line the server drew, in map units, as the display's points. */
export function drawn(trace: [number, number][], scope: Scope): string {
  return trace
    .map(([x, y]) => {
      const p = project(scope, x, y);
      return `${p.x.toFixed(1)},${p.y.toFixed(1)}`;
    })
    .join(" ");
}

/**
 * A stretch of a line, from one share of its length to another.
 *
 * The arc the server draws is the whole passage, and the display wants the
 * half of it still to be flown: the road already travelled is not a thing one
 * steers by. The ends are interpolated, so the line begins exactly at the hull
 * rather than at the nearest of the server's points.
 */
export function part(trace: [number, number][], from: number, to: number): [number, number][] {
  const last = trace.length - 1;
  if (last <= 0) return trace.slice();
  const a = Math.min(1, Math.max(0, from));
  const b = Math.min(1, Math.max(0, to));
  if (b <= a) return [];
  const out: [number, number][] = [];
  //: A cut that lands exactly on one of the server's own points would add it
  //: twice -- harmless in a `polyline`, and still a line with a length of zero
  //: in the middle of it.
  const add = (point: [number, number]) => {
    const last_ = out[out.length - 1];
    if (!last_ || last_[0] !== point[0] || last_[1] !== point[1]) out.push(point);
  };
  add(along(trace, a));
  for (let i = Math.ceil(a * last); i <= Math.floor(b * last); i++) add(trace[i]);
  add(along(trace, b));
  return out;
}

/**
 * Which way a line goes at a share of its length: a unit vector, or nothing
 * for a line too short to have a direction.
 *
 * This is where the hull's nose points, and it is read off a line the server
 * has already drawn rather than asked for as a heading of its own (D-225):
 * under way from the arc of the passage, adrift from the coast ahead.
 */
export function heading(trace: [number, number][], share: number): Point | null {
  const last = trace.length - 1;
  if (last <= 0) return null;
  const held = Math.min(1, Math.max(0, share));
  const at = Math.min(last - 1, Math.floor(held * last));
  const dx = trace[at + 1][0] - trace[at][0];
  const dy = trace[at + 1][1] - trace[at][1];
  const length = Math.hypot(dx, dy);
  if (length === 0) return null;
  return { x: dx / length, y: dy / length };
}

/** Where a heading points, in degrees, for a mark that has to face it. */
export function facing(way: Point): number {
  return (Math.atan2(way.y, way.x) * 180) / Math.PI;
}

/** Whether a place is on the display at all, with `pad` to spare. */
export function inFrame(p: Point, pad = 0): boolean {
  return p.x >= pad && p.x <= W - pad && p.y >= pad && p.y <= H - pad;
}

/**
 * Where the ray from the hull to an off-screen place crosses the display's
 * inner edge -- and nothing for a place that is on the display already.
 *
 * Zoomed in far enough, most of the system is outside the frame, and an
 * instrument that simply loses the other worlds is worse than one that never
 * zoomed at all. So what falls off the edge leaves a mark on the edge, at its
 * bearing: the display always says which way everything else is.
 */
export function edgeOf(p: Point, inset: number): Point | null {
  const dx = p.x - CENTER.x;
  const dy = p.y - CENTER.y;
  if (Math.hypot(dx, dy) < 1) return null;
  if (inFrame(p, inset)) return null;
  const steps: number[] = [];
  if (dx !== 0) steps.push(((dx > 0 ? W - inset : inset) - CENTER.x) / dx);
  if (dy !== 0) steps.push(((dy > 0 ? H - inset : inset) - CENTER.y) / dy);
  const step = Math.min(...steps.filter((one) => one > 0));
  if (!Number.isFinite(step)) return null;
  return { x: CENTER.x + dx * step, y: CENTER.y + dy * step };
}

/**
 * How much room a world's readout needs, and how far it stands off its mark.
 *
 * Pixels of the display, because that is what the readout is drawn in: nothing
 * on this instrument grows with the zoom.
 */
const LABEL = { line: 12, over: 14, under: 24, aside: 13, wide: 72, edge: 15 };

/** Where a world's name and prices stand, and which way the block grows. */
export type Label = {
  x: number;
  anchor: "start" | "middle" | "end";
  name: number;
  cheap: number;
  fast: number;
  /** Down from the mark, or up from it: which way a clash pushes the block. */
  away: 1 | -1;
};

/**
 * Where the readout of a world goes: under its dot by default, and turned
 * about wherever the frame would cut it off.
 *
 * Three lines of text are 36 pixels tall and 130 wide, and a world may stand
 * anywhere -- including a hair inside the bottom edge, where the block written
 * downwards is simply gone, and against a side, where a centred line runs off
 * the glass. Both were happening: an instrument that hides the price of the
 * passage at the edges is worse than one that never zoomed. So the block turns
 * over near the bottom, reads inwards at the sides, and hangs below the mark at
 * the top, where there is nothing above it.
 *
 * `onEdge` says the mark is a chevron on the frame rather than the world's own
 * dot -- then the text steps aside so as not to sit on top of it.
 */
export function labelAt(spot: Point, onEdge: boolean): Label {
  const aside = onEdge ? LABEL.aside : 0;
  const anchor =
    spot.x < LABEL.wide ? "start" : spot.x > W - LABEL.wide ? "end" : ("middle" as const);
  const x = spot.x + (anchor === "start" ? aside : anchor === "end" ? -aside : 0);
  //: Against the top there is nothing above the mark: name and figures hang
  //: below it. Against the bottom it is the other way round, and the block
  //: keeps its reading order -- name, cheap, fast, and then the world.
  if (spot.y < LABEL.over + LABEL.edge) {
    const top = spot.y + LABEL.edge;
    return { x, anchor, name: top, cheap: top + LABEL.line, fast: top + 2 * LABEL.line, away: 1 };
  }
  if (spot.y + LABEL.under + LABEL.line > H) {
    const foot = spot.y - LABEL.edge;
    return {
      x,
      anchor,
      name: foot - 2 * LABEL.line,
      cheap: foot - LABEL.line,
      fast: foot,
      away: -1,
    };
  }
  return {
    x,
    anchor,
    name: spot.y - LABEL.over,
    cheap: spot.y + LABEL.under,
    fast: spot.y + LABEL.under + LABEL.line,
    away: 1,
  };
}
