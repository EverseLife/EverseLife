// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The geometry of the bridge display (`panels/ship/scope`).
 *
 * All of it is arithmetic between the map units the server draws its lines in
 * and the pixels of one panel, and all of it is invisible when it goes wrong:
 * a projection off by a term still draws a plausible sky, only not this ship's.
 * So the promises the display is built on are pinned here rather than checked
 * by eye against a screenshot.
 */

import { describe, expect, it } from "vitest";
import {
  CENTER,
  FURTHEST,
  H,
  NEAREST,
  W,
  clampZoom,
  edgeOf,
  facing,
  heading,
  inFrame,
  labelAt,
  part,
  pinchZoom,
  project,
  span,
  spread,
  unitFor,
  zoomBy,
  type Scope,
} from "../panels/ship/scope";

const scope = (over: Partial<Scope> = {}): Scope => ({
  at: { x: 10, y: -4 },
  off: { x: 0, y: 0 },
  unit: 2,
  zoom: 1,
  ...over,
});

describe("project", () => {
  it("keeps the hull in the middle at every zoom", () => {
    //: The one promise of the whole module: the display is the ship, and the
    //: ship does not move on it. Berthed, the mark hangs off its planet's dot
    //: (D-245) -- and it is the **mark** that has to land in the middle, or a
    //: hull in port would sit sixteen pixels off its own display.
    const off = { x: 11, y: -11 };
    for (const zoom of [FURTHEST, 1, 7, NEAREST]) {
      const one = scope({ off, zoom });
      const where = project(one, one.at.x, one.at.y);
      expect(where.x + off.x).toBeCloseTo(CENTER.x);
      expect(where.y + off.y).toBeCloseTo(CENTER.y);
    }
  });

  it("opens the distances and nothing else", () => {
    //: Zooming is applied to places, never to the drawing: twice as near is
    //: twice as far apart, and a planet's dot is still a dot (`Chart` draws
    //: every mark in the display's own pixels, which this makes possible).
    const near = project(scope({ zoom: 4 }), 60, -4);
    const far = project(scope({ zoom: 1 }), 60, -4);
    expect(near.x - CENTER.x).toBeCloseTo((far.x - CENTER.x) * 4);
    expect(span(scope({ zoom: 4 }), 10)).toBe(span(scope(), 10) * 4);
  });

  it("puts the whole system across the frame at rest", () => {
    //: What "at rest" means: the outermost orbit's diameter inside the frame,
    //: so a hull anywhere in the system sees the rest of it without touching
    //: the zoom.
    const unit = unitFor(50);
    expect(span({ ...scope(), unit, zoom: 1 }, 100)).toBeLessThanOrEqual(H);
  });
});

describe("the zoom", () => {
  it("stays within what the display may show", () => {
    expect(zoomBy(NEAREST, 8)).toBe(NEAREST);
    expect(zoomBy(FURTHEST, -8)).toBe(FURTHEST);
    expect(clampZoom(1e6)).toBe(NEAREST);
  });

  it("goes back where it came from", () => {
    //: A notch in and a notch out is the zoom one started at: the loupes and
    //: the wheel must not drift the display over a minute of fiddling.
    expect(zoomBy(zoomBy(2, 1), -1)).toBeCloseTo(2);
  });

  it("measures a pinch from where the fingers began", () => {
    //: Against the start, never against the last move: a hundred rounded
    //: steps would otherwise walk the display away from the fingers.
    expect(pinchZoom(2, 100, 150)).toBeCloseTo(3);
    expect(pinchZoom(2, 100, 50)).toBeCloseTo(1);
    expect(pinchZoom(2, 0, 50)).toBe(2);
  });
});

describe("part", () => {
  const arc: [number, number][] = [
    [0, 0],
    [10, 0],
    [20, 0],
    [30, 0],
  ];

  it("cuts the arc exactly where the hull is", () => {
    //: The wake ends and the course begins at the same point, and that point
    //: is the ship: a cut to the nearest of the server's points would leave a
    //: gap of hours at one end and an overlap at the other.
    const wake = part(arc, 0, 0.5);
    const ahead = part(arc, 0.5, 1);
    expect(wake[wake.length - 1]).toEqual([15, 0]);
    expect(ahead[0]).toEqual([15, 0]);
  });

  it("has nothing behind a passage that has not begun", () => {
    expect(part(arc, 0, 0)).toEqual([]);
    expect(part(arc, 1, 1)).toEqual([]);
  });
});

describe("heading", () => {
  it("reads the way ahead off the line the server drew", () => {
    //: No heading is asked of the server (D-225): where the nose points is
    //: already in the arc it is flying.
    const way = heading(
      [
        [0, 0],
        [0, 5],
      ],
      0,
    );
    expect(way).toEqual({ x: 0, y: 1 });
    expect(facing(way!)).toBeCloseTo(90);
  });

  it("says nothing where there is no direction to read", () => {
    expect(heading([[1, 1]], 0)).toBeNull();
    expect(
      heading(
        [
          [1, 1],
          [1, 1],
        ],
        0,
      ),
    ).toBeNull();
  });
});

describe("the edge of the glass", () => {
  it("leaves a bearing where a world falls off it", () => {
    //: Zoomed in, most of the system is outside the frame. A display that
    //: simply loses the other worlds is worse than one that never zoomed.
    const mark = edgeOf({ x: CENTER.x + 4000, y: CENTER.y }, 16);
    expect(mark).not.toBeNull();
    expect(mark!.x).toBeCloseTo(W - 16);
    expect(mark!.y).toBeCloseTo(CENTER.y);
    expect(inFrame(mark!, 15)).toBe(true);
  });

  it("marks nothing that is on the glass already", () => {
    expect(edgeOf({ x: CENTER.x + 40, y: CENTER.y }, 16)).toBeNull();
    expect(edgeOf(CENTER, 16)).toBeNull();
  });
});

describe("labelAt", () => {
  it("hangs a world's readout under it", () => {
    const label = labelAt({ x: 300, y: 200 }, false);
    expect(label.anchor).toBe("middle");
    expect(label.name).toBeLessThan(200);
    expect(label.cheap).toBeGreaterThan(200);
    expect(label.fast).toBeGreaterThan(label.cheap);
    expect(label.away).toBe(1);
  });

  it("turns the readout over rather than let the frame cut it off", () => {
    //: The defect this exists for: a world a hair inside the bottom edge is
    //: still "on the glass", and three lines written downwards from it are
    //: simply gone -- the price of the passage, which is what the chart is for.
    const label = labelAt({ x: 300, y: H - 20 }, false);
    expect(label.away).toBe(-1);
    for (const y of [label.name, label.cheap, label.fast]) {
      expect(y).toBeGreaterThan(0);
      expect(y).toBeLessThan(H);
    }
    //: And it still reads in its own order, top to bottom.
    expect(label.name).toBeLessThan(label.cheap);
    expect(label.cheap).toBeLessThan(label.fast);
  });

  it("hangs it below a mark on the top edge, where nothing fits above", () => {
    const label = labelAt({ x: 300, y: 16 }, true);
    expect(label.name).toBeGreaterThan(16);
    expect(label.away).toBe(1);
  });

  it("reads inwards at the sides instead of over the edge", () => {
    expect(labelAt({ x: 16, y: 200 }, true).anchor).toBe("start");
    expect(labelAt({ x: W - 16, y: 200 }, true).anchor).toBe("end");
    //: And steps clear of the chevron it belongs to rather than sitting on it.
    expect(labelAt({ x: 16, y: 200 }, true).x).toBeGreaterThan(16);
    expect(labelAt({ x: 16, y: 200 }, false).x).toBe(16);
  });
});

describe("spread", () => {
  it("leaves readouts that do not meet exactly where they are", () => {
    const drops = spread(
      [
        { x: 100, y: 100, away: 1 },
        { x: 400, y: 104, away: 1 },
      ],
      26,
      132,
    );
    expect(drops).toEqual([0, 0]);
  });

  it("steps a clashing one to just clear of what it ran into", () => {
    //: Two worlds share a bearing for a week at a time, and then two blocks of
    //: hours and fuel are drawn one over the other. The second steps away --
    //: to just below the first, not by a blind notch that could land it in the
    //: same place again and walk it far off its own world.
    const drops = spread(
      [
        { x: 100, y: 120, away: 1 },
        { x: 140, y: 110, away: 1 },
      ],
      26,
      132,
    );
    expect(drops[0]).toBe(0);
    expect(110 + drops[1]).toBe(146);
  });

  it("steps the way the block grows, never towards the edge it fled", () => {
    //: A block laid upwards from the bottom of the glass, pushed down, walks
    //: straight off it -- which is the edge it was turned about to avoid.
    const drops = spread(
      [
        { x: 100, y: 380, away: -1 },
        { x: 140, y: 390, away: -1 },
      ],
      26,
      132,
    );
    expect(drops[0]).toBe(0);
    expect(390 + drops[1]).toBe(354);
  });

  it("gives the same sky the same layout twice", () => {
    const spots = [
      { x: 100, y: 100, away: 1 as const },
      { x: 120, y: 108, away: 1 as const },
      { x: 130, y: 112, away: 1 as const },
    ];
    expect(spread(spots, 26, 132)).toEqual(spread(spots, 26, 132));
  });
});
