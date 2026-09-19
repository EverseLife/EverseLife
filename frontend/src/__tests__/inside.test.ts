// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The inside of a house (D-247): the ground floor is the plot itself, and the
 * window of the inside draws it with the floors above, at the origin of their
 * plan -- from any floor, not only when one stands on it.
 *
 * Owner, 2026-09-19: from the second floor the first could not be picked.
 * It was not in the scene at all; the second floor stood on its spot, and the
 * mark of the floor underfoot was drawn at a closed map's size over both.
 */

import { describe, expect, it } from "vitest";

import type { MapNode } from "../api";
import {
  GROUND_FLOOR_AT,
  citiesDrawnOpen,
  delegateIn,
  edgesOf,
  groundFloorOf,
  groupsOf,
  visibleOf,
} from "../panels/map/useScene";

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

const byKey: Record<string, MapNode> = {
  terra: node({ key: "terra", layer: "space" }),
  city: node({ key: "city", layer: "planet", parent: "terra", place: { lat: 40, lon: 20 } }),
  street: node({ key: "street", layer: "city", parent: "city", place: { lat: 40.001, lon: 20 } }),
  //: A plot in a city wears the client's `city` layer (`geo.withCityScene`).
  plot: node({ key: "plot", layer: "city", parent: "city", place: { lat: 40.002, lon: 20 } }),
  second: node({ key: "second", layer: "location", parent: "plot", place: { x: 150, y: 0 } }),
  third: node({ key: "third", layer: "location", parent: "plot", place: { x: 150, y: 150 } }),
  hull: node({ key: "hull", layer: "space", parent: "terra", aboard: true } as Partial<MapNode>),
  bridge: node({ key: "bridge", layer: "location", parent: "hull", place: { x: 0, y: 0 } }),
  hold: node({ key: "hold", layer: "location", parent: "hull", place: { x: 150, y: 0 } }),
  //: A hull at a pier comes to its crew wearing the pier's layer, under the
  //: pier's city (`ship.view.sight`), and at a point beside the pier.
  moored: node({
    key: "moored",
    layer: "city",
    parent: "city",
    aboard: true,
    place: { lat: 40.003, lon: 20 },
  } as Partial<MapNode>),
  base: node({ key: "base", layer: "location", parent: "moored", place: { x: 0, y: 0 } }),
};
const nodes = Object.values(byKey);
const edges = [
  { a: "street", b: "plot", surface: "road", seconds: 30 },
  { a: "plot", b: "second", surface: "paved", seconds: 20 },
  { a: "second", b: "third", surface: "paved", seconds: 20 },
  { a: "bridge", b: "hold", surface: "paved", seconds: 1 },
];
const INSIDE = ["location"];

describe("the inside of a house", () => {
  it("has the plot for its ground floor, and a hull has none", () => {
    expect(groundFloorOf(byKey, "inside", "plot")).toBe("plot");
    //: A hull's rooms hang under a ship, not under a floor -- in the sky,
    //: and at a pier too, where it wears the pier's layer: drawn at the
    //: origin it would cover the first room, which stands there.
    expect(groundFloorOf(byKey, "inside", "hull")).toBe(null);
    expect(groundFloorOf(byKey, "inside", "moored")).toBe(null);
    //: Outside, the plot is an ordinary node of the surface.
    expect(groundFloorOf(byKey, "surface", "plot")).toBe(null);
  });

  it("draws the ground floor from upstairs, at the origin of the plan", () => {
    const shown = visibleOf(nodes, INSIDE, "plot", "terra", "second", "plot");
    expect(shown.map((n) => n.key)).toEqual(["plot", "second", "third"]);
    const at = Object.fromEntries(shown.map((n) => [n.key, n.place]));
    expect(at.plot).toEqual(GROUND_FLOOR_AT);
    //: The floors stand where the server put them.
    expect(at.second).toEqual(byKey.second.place);
    expect(at.third).toEqual(byKey.third.place);
    //: And the map's own copy of the plot keeps its degrees.
    expect(byKey.plot.place).toEqual({ lat: 40.002, lon: 20 });
  });

  it("draws the stair down and marks the ground floor as itself", () => {
    const repr = delegateIn(byKey, INSIDE, "plot");
    expect(repr("plot")).toBe("plot");
    expect(repr("second")).toBe("second");
    //: The street outside is no part of the inside.
    expect(repr("street")).toBe(null);
    const shown = new Set(["plot", "second", "third"]);
    expect(edgesOf(edges, shown, repr)).toEqual([
      { a: "plot", b: "second", surface: "paved", seconds: 20 },
      { a: "second", b: "third", surface: "paved", seconds: 20 },
    ]);
  });

  it("offers the way down from any floor: the ground floor opens nothing", () => {
    //: A group is walked to only by a step straight into it, and from the
    //: third floor the plot is no exit: as a group it had no way down.
    expect(groupsOf(nodes, "plot").has("plot")).toBe(false);
    //: Outside, the plot still opens into its floors.
    expect(groupsOf(nodes, null).has("plot")).toBe(true);
    expect(groupsOf(nodes, "plot").has("city")).toBe(true);
  });

  it("keeps a hull's inside to its rooms", () => {
    const shown = visibleOf(nodes, INSIDE, "hull", "terra", "hold", null);
    expect(shown.map((n) => n.key)).toEqual(["bridge", "hold"]);
    expect(delegateIn(byKey, INSIDE, null)("hull")).toBe(null);
    //: At a pier as well: the first room alone at the origin.
    const ground = groundFloorOf(byKey, "inside", "moored");
    const aboard = visibleOf(nodes, INSIDE, "moored", "terra", "base", ground);
    expect(aboard.map((n) => [n.key, n.place])).toEqual([["base", { x: 0, y: 0 }]]);
  });

  it("closes no city inside: a house is not drawn as one", () => {
    //: Closed, the ground floor -- a group -- wore a city's halo, and the
    //: floor underfoot a mark sized for the globe.
    expect(citiesDrawnOpen("inside", false)).toBe(true);
    expect(citiesDrawnOpen("surface", true)).toBe(true);
    expect(citiesDrawnOpen("surface", false)).toBe(false);
    //: The sky keeps its planets closed: a click opens their surface.
    expect(citiesDrawnOpen("sky", true)).toBe(false);
  });
});
