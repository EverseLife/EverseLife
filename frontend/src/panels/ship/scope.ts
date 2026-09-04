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
 * on the edge as a bearing. Far out is the whole system and then some; the near
 * end is close quarters -- a hull, its parking circle and the planet under it.
 * The map's own limits (`map/hand`) are narrower because the map may also be
 * dragged: there, going far in is a way of getting lost, and here there is
 * nothing to get lost from.
 */
export const FURTHEST = 0.4;
export const NEAREST = 24;
//: The same notch the world map turns by (`map/hand`): one click of a wheel
//: must mean the same thing on both drawings of one client.
const NOTCH = 1.15;

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
 * What the display is looking at.
 *
 * `at` is the hull's own place in map units, and `off` is where its mark sits
 * off that place in pixels -- berthed and in orbit the hull is drawn clear of
 * its planet's dot rather than on top of it (D-245). The projection carries
 * that offset, so that the **mark**, not the place, lands in the middle:
 * otherwise a hull in port would sit sixteen pixels off its own display.
 */
export type Scope = {
  at: Point;
  off: Point;
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
    x: CENTER.x - scope.off.x + (x - scope.at.x) * k,
    y: CENTER.y - scope.off.y + (y - scope.at.y) * k,
  };
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
 * What the hull has already flown is not what it is going to fly, and the two
 * halves of an arc under way are drawn as two different things (D-289): the
 * wake behind, the course ahead. The ends are interpolated, so the cut lands
 * exactly on the hull rather than at the nearest of the server's points.
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

/**
 * How far each readout has to move so that two of them do not pile up.
 *
 * Four worlds on four orbits stand where the clock puts them, and nothing stops
 * two of them sharing a bearing for a week -- and then two blocks of hours and
 * fuel are drawn one over the other and neither can be read. The one that comes
 * second steps out of the way and stays under its own mark: a readout a little
 * further from its world beats two in one place.
 *
 * It steps the way its block already grows (`away`), never towards the edge the
 * block was turned about to avoid -- pushed down, a block laid upwards from the
 * bottom of the glass would walk straight off it.
 *
 * Order in, order out: the shift is a function of the list, so the display does
 * not reshuffle itself between two draws of the same sky.
 */
export function spread(
  spots: (Point & { away: number })[],
  apart: number,
  wide: number,
): number[] {
  const placed: Point[] = [];
  return spots.map((spot) => {
    let shift = 0;
    //: Each block already placed can push this one once; more steps than that
    //: means a loop, not a layout.
    for (let step = 0; step <= placed.length; step++) {
      const clash = placed.find(
        (one) => Math.abs(one.x - spot.x) < wide && Math.abs(one.y - (spot.y + shift)) < apart,
      );
      if (!clash) break;
      //: Just clear of the block it ran into -- a blind notch can land it in
      //: the same place a second time and walk it far off its world.
      shift = clash.y + apart * spot.away - spot.y;
    }
    placed.push({ x: spot.x, y: spot.y + shift });
    return shift;
  });
}
