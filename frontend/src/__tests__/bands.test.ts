// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** The bands of scale (D-319, wave 4): what a height shows, where it hands over. */

import { describe, expect, it } from "vitest";

import type { MapNode } from "../api";
import {
  BORDER_MARGIN,
  CITY_SCALE,
  GLOBE_FILL,
  OPEN_REACH,
  SKY_BOUNDS,
  SURFACE_NEAREST,
  boundsOf,
  cityOpen,
  globeScale,
  leavesSurface,
  planetUnder,
  reachesSurface,
  ring,
  surfaceBounds,
  surfaceFloor,
} from "../panels/map/bands";
import { H } from "../panels/map/model";
import { edgesOf, visibleOf } from "../panels/map/useScene";

//: The vault's `map.approach_km` as of D-319.
const APPROACH_KM = 50000;
const SURFACE = surfaceBounds(APPROACH_KM);
import { STUB_M, UNITS_PER_METRE, ahead, arc, project, radiusUnits } from "../panels/map/globe";
import { delegateAmong } from "../panels/map/model";

const node = (over: Partial<MapNode>): MapNode =>
  ({
    key: "x",
    name: "Узел",
    layer: "city",
    parent: null,
    port: false,
    planet: "terra",
    orbit: null,
    ...over,
  }) as MapNode;

describe("the bands", () => {
  it("open a city at the city scale and close it a hair farther out", () => {
    expect(cityOpen(CITY_SCALE)).toBe(true);
    expect(cityOpen(1)).toBe(true);
    expect(cityOpen(CITY_SCALE - 1e-6)).toBe(false);
  });

  it("hand the surface to the sky only at the floor of the surface", () => {
    expect(leavesSurface(SURFACE.furthest, SURFACE.furthest)).toBe(true);
    expect(leavesSurface(SURFACE.furthest * 2, SURFACE.furthest)).toBe(false);
    //: The sky's own floor is far above the surface's: a hand zooming out
    //: through the sky must not fall back onto a planet.
    expect(leavesSurface(SKY_BOUNDS.furthest, SURFACE.furthest)).toBe(false);
  });

  it("read the floor from the vault's height: a right-angled view across the frame", () => {
    //: From `map.approach_km` up, the frame spans twice the height.
    expect(surfaceFloor(APPROACH_KM)).toBeCloseTo(H / (2 * APPROACH_KM * 1000 * UNITS_PER_METRE), 12);
    expect(surfaceFloor(APPROACH_KM)).toBeLessThan(globeScale(radiusUnits(6371)));
    //: No book, no height: the floor sits under the disk, not on it.
    expect(surfaceFloor(0)).toBeLessThan(globeScale(radiusUnits(6371 * 1.2)));
    expect(surfaceFloor(Number.NaN)).toBe(surfaceFloor(0));
  });

  it("ask the sky to open a planet only at its ceiling", () => {
    expect(reachesSurface(SKY_BOUNDS.nearest)).toBe(true);
    expect(reachesSurface(SKY_BOUNDS.nearest / 2)).toBe(false);
  });

  it("open the surface as a globe of the planet's own size, not falling back into the sky", () => {
    const terra = radiusUnits(6371);
    const scale = globeScale(terra);
    //: The disk is a little taller than the frame, whatever the planet.
    expect(2 * terra * scale).toBeCloseTo(H * GLOBE_FILL, 6);
    expect(2 * radiusUnits(1000) * globeScale(radiusUnits(1000))).toBeCloseTo(H * GLOBE_FILL, 6);
    expect(scale).toBeGreaterThan(SURFACE.furthest);
    expect(leavesSurface(scale, SURFACE.furthest)).toBe(false);
    expect(cityOpen(scale)).toBe(false);
    //: No radius, no globe: the streets, as before.
    expect(globeScale(null)).toBe(1);
  });

  it("give each band its own bounds, the surface reaching farthest out", () => {
    expect(boundsOf("sky", SURFACE)).toBe(SKY_BOUNDS);
    expect(boundsOf("surface", SURFACE)).toBe(SURFACE);
    expect(boundsOf("inside", SURFACE).furthest).toBeGreaterThan(SURFACE.furthest);
    expect(SURFACE.nearest).toBe(SURFACE_NEAREST);
    expect(SURFACE.furthest).toBeLessThan(CITY_SCALE);
  });
});

describe("planetUnder", () => {
  const spheres = [
    { key: "terra", planet: "terra", at: { x: 0, y: 0 } },
    { key: "aurora", planet: "aurora", at: { x: 200, y: 0 } },
  ];

  it("names the planet nearest the middle, within reach", () => {
    expect(planetUnder({ x: 10, y: 5 }, spheres)?.planet).toBe("terra");
    expect(planetUnder({ x: 190, y: 0 }, spheres)?.planet).toBe("aurora");
  });

  it("names nothing when the middle is over empty sky", () => {
    expect(planetUnder({ x: 100, y: 0 }, spheres)).toBe(null);
    expect(planetUnder({ x: 0, y: OPEN_REACH + 1 }, spheres)).toBe(null);
    expect(planetUnder({ x: 0, y: 0 }, [])).toBe(null);
  });
});

describe("ring", () => {
  it("is nothing round nothing", () => {
    expect(ring([])).toBe(null);
  });

  it("circles the nodes about their middle, with room to spare", () => {
    const border = ring([
      { x: -10, y: 0 },
      { x: 10, y: 0 },
      { x: 0, y: 30 },
    ]);
    expect(border).not.toBe(null);
    expect(border!.cx).toBeCloseTo(0);
    expect(border!.cy).toBeCloseTo(10);
    expect(border!.r).toBeCloseTo(20 + BORDER_MARGIN);
  });

  it("gives a lone node a circle of the margin alone", () => {
    expect(ring([{ x: 3, y: 4 }])).toEqual({ cx: 3, cy: 4, r: BORDER_MARGIN });
  });
});

describe("delegateAmong", () => {
  const byKey: Record<string, MapNode> = {
    terra: node({ key: "terra", layer: "space" }),
    city: node({ key: "city", layer: "planet", parent: "terra" }),
    gate: node({ key: "gate", layer: "city", parent: "city" }),
    floor: node({ key: "floor", layer: "location", parent: "gate" }),
  };

  it("stands a node for itself when its layer is drawn, else for its nearest drawn parent", () => {
    expect(delegateAmong(byKey, "gate", ["city", "planet"])).toBe("gate");
    expect(delegateAmong(byKey, "gate", ["planet"])).toBe("city");
    expect(delegateAmong(byKey, "floor", ["city", "planet"])).toBe("gate");
    expect(delegateAmong(byKey, "floor", ["space"])).toBe("terra");
  });

  it("answers null when nothing above the node is drawn, or the node is unknown", () => {
    expect(delegateAmong(byKey, "terra", ["planet"])).toBe(null);
    expect(delegateAmong(byKey, "nowhere", ["planet"])).toBe(null);
  });
});

describe("a stub into the fog", () => {
  const R = radiusUnits(6371);
  const home = { lat: 41, lon: 24 };

  it("sets out on its bearing: north is up the frame, east is to the right", () => {
    const eye = home;
    const north = project(eye, R, ahead(R, home, 0, STUB_M));
    const east = project(eye, R, ahead(R, home, 90, STUB_M));
    expect(north.y).toBeLessThan(0);
    expect(Math.abs(north.x)).toBeLessThan(1e-6);
    expect(east.x).toBeGreaterThan(0);
    expect(Math.abs(east.y)).toBeLessThan(1e-3);
  });

  it("is as long as the stub and no longer, at any bearing", () => {
    for (const bearing of [0, 45, 135, 200, 300]) {
      const end = project(home, R, ahead(R, home, bearing, STUB_M));
      expect(Math.hypot(end.x, end.y)).toBeCloseTo(STUB_M * 5, 3);
    }
  });

  it("is drawn as an arc from the seen end, like any edge", () => {
    const run = arc(home, R, home, ahead(R, home, 270, STUB_M));
    expect(run).not.toBe(null);
    expect(run![0].x).toBeCloseTo(0);
    expect(run![0].y).toBeCloseTo(0);
    expect(run![run!.length - 1].x).toBeLessThan(0);
  });
});

describe("the scene", () => {
  const byKey: Record<string, MapNode> = {
    terra: node({ key: "terra", layer: "space" }),
    aurora: node({ key: "aurora", layer: "space", planet: "aurora" }),
    city: node({ key: "city", layer: "planet", parent: "terra" }),
    gate: node({ key: "gate", layer: "city", parent: "city" }),
    field: node({ key: "field", layer: "planet", parent: "terra" }),
    floor: node({ key: "floor", layer: "location", parent: "gate" }),
    cellar: node({ key: "cellar", layer: "location", parent: "field" }),
    outpost: node({ key: "outpost", layer: "planet", parent: "aurora", planet: "aurora" }),
  };
  const nodes = Object.values(byKey);
  const edges = [
    { a: "gate", b: "field", surface: "road", seconds: 60 },
    { a: "city", b: "field", surface: "wild", seconds: 300 },
    { a: "gate", b: "floor", surface: "paved", seconds: 5 },
  ];
  const repr = (layers: string[]) => (key: string) => delegateAmong(byKey, key, layers);

  it("draws a surface's own planet, and an inside only its base's", () => {
    expect(visibleOf(nodes, ["planet"], "gate", "terra").map((n) => n.key)).toEqual([
      "city",
      "field",
    ]);
    expect(visibleOf(nodes, ["city", "planet"], "gate", "terra").map((n) => n.key)).toEqual([
      "city",
      "gate",
      "field",
    ]);
    expect(visibleOf(nodes, ["location"], "gate", "terra").map((n) => n.key)).toEqual(["floor"]);
    expect(visibleOf(nodes, ["space"], "gate", "terra").map((n) => n.key)).toEqual([
      "terra",
      "aurora",
    ]);
  });

  it("joins the delegates of an edge's ends: the city and the field when the city is closed", () => {
    const closed = edgesOf(edges, new Set(["city", "field"]), repr(["planet"]));
    //: The road from the gate and the wild way from the city both become
    //: city--field; the shorter one is kept.
    expect(closed).toEqual([{ a: "city", b: "field", surface: "road", seconds: 60 }]);
    const open = edgesOf(edges, new Set(["city", "gate", "field"]), repr(["city", "planet"]));
    expect(open.map((e) => [e.a, e.b])).toEqual([
      ["gate", "field"],
      ["city", "field"],
    ]);
  });

  it("draws no edge to what the scene does not show", () => {
    //: The floor is not on the surface; the sky shows no ways at all.
    expect(edgesOf(edges, new Set(["city", "gate", "field"]), repr(["city", "planet"]))).toHaveLength(2);
    expect(edgesOf(edges, new Set(["terra", "aurora"]), repr(["space"]))).toEqual([]);
  });
});
