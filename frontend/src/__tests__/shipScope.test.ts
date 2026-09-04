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
  gridStep,
  partOf,
  ringSeen,
  span,
  unitFor,
  zoomAt,
  zoomBy,
  type Scope,
} from "../panels/ship/scope";

const scope = (over: Partial<Scope> = {}): Scope => ({
  at: { x: 10, y: -4 },
  unit: 2,
  zoom: 1,
  ...over,
});

describe("project", () => {
  it("keeps the hull in the middle at every zoom", () => {
    //: The one promise of the whole module: the display is the ship, and the
    //: ship does not move on it -- whatever the hull is doing and however near
    //: it is being looked at.
    for (const zoom of [FURTHEST, 1, 7, NEAREST]) {
      const one = scope({ zoom });
      const where = project(one, one.at.x, one.at.y);
      expect(where.x).toBeCloseTo(CENTER.x);
      expect(where.y).toBeCloseTo(CENTER.y);
    }
  });

  it("opens up the gap between a moored hull and its planet", () => {
    //: What the fixed pixel offset could never do. A hull on the circle stands
    //: `orbit.park_radius` off its planet, in map units like everything else,
    //: so looking nearer walks the two apart -- and at rest they are the same
    //: point, which is the truth about a parking orbit seen from the system.
    const planet = { x: 10, y: -4 };
    const moored = { x: planet.x + 1.5, y: planet.y };
    const gap = (zoom: number) => {
      const one = scope({ at: moored, zoom });
      return project(one, planet.x, planet.y).x - CENTER.x;
    };
    expect(Math.abs(gap(1))).toBeLessThan(4);
    expect(Math.abs(gap(100))).toBeCloseTo(Math.abs(gap(1)) * 100);
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

describe("the slider", () => {
  it("stands at the ends where the zoom does", () => {
    expect(partOf(FURTHEST)).toBeCloseTo(0);
    expect(partOf(NEAREST)).toBeCloseTo(1);
    expect(zoomAt(0)).toBeCloseTo(FURTHEST);
    expect(zoomAt(1)).toBeCloseTo(NEAREST);
  });

  it("reads back the zoom it was set to", () => {
    for (const zoom of [FURTHEST, 1, 7, 60, NEAREST]) {
      expect(zoomAt(partOf(zoom))).toBeCloseTo(zoom);
    }
  });

  it("spends the same travel on every notch of the wheel", () => {
    //: The scale is multiplicative, so the slider is logarithmic: a notch near
    //: the system's own scale must move the thumb as far as a notch at the
    //: docking end, or the near half of the range would be a pixel wide.
    const near = partOf(zoomBy(2, 1)) - partOf(2);
    const far = partOf(zoomBy(80, 1)) - partOf(80);
    expect(near).toBeCloseTo(far);
  });
});

describe("gridStep", () => {
  const at = (zoom: number) => ({ at: { x: 0, y: 0 }, off: { x: 0, y: 0 }, unit: 0.42, zoom });

  it("keeps the ruling readable at every zoom", () => {
    //: Ruled on the sky, a step fixed in map units is a wall of lines at one
    //: end of the zoom and none at the other. It climbs a ladder instead, and
    //: what the eye sees stays within a factor of a few.
    for (const zoom of [FURTHEST, 1, 12, 90, NEAREST]) {
      const scope = at(zoom);
      const onGlass = span(scope, gridStep(scope));
      expect(onGlass).toBeGreaterThanOrEqual(40);
      expect(onGlass).toBeLessThan(160);
    }
  });

  it("climbs the 1-2-5 ladder and nothing else", () => {
    for (const zoom of [0.5, 1, 3, 9, 40, 200]) {
      const step = gridStep(at(zoom));
      const rung = step / 10 ** Math.floor(Math.log10(step));
      expect([1, 2, 5]).toContain(Math.round(rung));
    }
  });
});

describe("ringSeen", () => {
  it("drops a circle that cannot cross the glass", () => {
    //: At the near end an orbit is forty thousand pixels across with its
    //: middle far off the frame: handing that to the renderer to find out it
    //: shows nothing is work for nothing.
    expect(ringSeen(CENTER, 40_000)).toBe(false);
    expect(ringSeen({ x: -50_000, y: 0 }, 120)).toBe(false);
  });

  it("keeps the one whose arc runs through it", () => {
    expect(ringSeen(CENTER, 100)).toBe(true);
    expect(ringSeen({ x: -40_000, y: CENTER.y }, 40_000 + CENTER.x)).toBe(true);
  });
});
