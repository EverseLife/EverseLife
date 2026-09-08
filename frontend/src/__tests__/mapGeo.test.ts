// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** The map's rows and scenes, flattened and deduplicated
 *  (`panels/map/geo.ts`). Cut out of `map.test.ts` on 2026-09-08, with the
 *  words and the orbits, to bring that file back under the 800-line bar. */

import { describe, expect, it } from "vitest";

import type { MapNode } from "../api";
import { flatten, oneEach, withCityScene } from "../panels/map/geo";

const node = (over: Partial<MapNode>): MapNode =>
  ({
    key: "x",
    name: "Узел",
    layer: "city",
    parent: null,
    exit: false,
    port: false,
    planet: "terra",
    orbit: null,
    deferred: false,
    aboard: false,
    flight: null,
    ...over,
  }) as MapNode;

describe("flatten", () => {
  //: The globe is wave 3; until then a scene of degrees is flattened round
  //: its first placed node by key, the same way for every viewer (D-319).
  it("projects degrees round the first node by key, and passes rooms through", () => {
    const nodes = [
      node({ key: "b", layer: "planet", place: { lat: 41, lon: 25 } }),
      node({ key: "a", layer: "planet", place: { lat: 41, lon: 24 } }),
      node({ key: "room", layer: "location", place: { x: 7, y: -3 } }),
      node({ key: "sky", layer: "space", place: null }),
    ];
    const out = flatten(nodes);
    expect(out.get("a")).toEqual({ x: 0, y: -0 });
    expect(out.get("room")).toEqual({ x: 7, y: -3 });
    expect(out.has("sky")).toBe(false);
    const east = out.get("b")!;
    expect(east.x).toBeGreaterThan(0);
    expect(east.y).toBe(-0);
  });

  it("is the same map for everybody: order of the input changes nothing", () => {
    const a = node({ key: "a", layer: "planet", place: { lat: 10, lon: 10 } });
    const b = node({ key: "b", layer: "planet", place: { lat: 12, lon: 11 } });
    expect(flatten([a, b])).toEqual(flatten([b, a]));
  });

  it("scales a scene down to fit the frame, and never up past the city step", () => {
    const near = flatten([
      node({ key: "a", layer: "planet", place: { lat: 0, lon: 0 } }),
      node({ key: "b", layer: "planet", place: { lat: 0, lon: 0.001 } }),
    ]);
    const far = flatten([
      node({ key: "a", layer: "planet", place: { lat: 0, lon: 0 } }),
      node({ key: "b", layer: "planet", place: { lat: 0, lon: 90 } }),
    ]);
    //: A thousandth of a degree is a hundred metres: about five hundred units.
    expect(near.get("b")!.x).toBeCloseTo(555, 0);
    //: A quarter of the globe is brought down to the frame, not drawn at scale.
    expect(far.get("b")!.x).toBeLessThan(5000);
  });
});

describe("withCityScene", () => {
  //: The server has one surface level; the client gives a node whose parent
  //: is itself a surface node the city scene (D-319).
  it("marks a surface node under a surface node as the city's", () => {
    const [town, plot, wild, room] = withCityScene([
      node({ key: "town", layer: "planet", parent: "terra" }),
      node({ key: "plot", layer: "planet", parent: "town" }),
      node({ key: "wild", layer: "planet", parent: "terra" }),
      node({ key: "room", layer: "location", parent: "plot" }),
    ]);
    expect(town.layer).toBe("planet");
    expect(plot.layer).toBe("city");
    expect(wild.layer).toBe("planet");
    expect(room.layer).toBe("location");
  });
});

describe("oneEach", () => {
  //: The map and `look` overlap on purpose -- a hull is a point of the sky on
  //: one and a point of its pier on the other -- and two rows of one key are
  //: counted twice by whatever counts nodes: a city grew a size for every
  //: stranger's hull moored in it.
  it("keeps one row per key, the later one", () => {
    const rows = oneEach([
      node({ key: "hull", layer: "space", name: "map" }),
      node({ key: "town", layer: "planet" }),
      node({ key: "hull", layer: "planet", name: "sight" }),
    ]);
    expect(rows.map((one) => one.key)).toEqual(["hull", "town"]);
    //: The near sight is the later of the two and the more particular.
    expect(rows[0].layer).toBe("planet");
    expect(rows[0].name).toBe("sight");
  });

  it("leaves a list with nothing repeated as it found it", () => {
    const rows = oneEach([node({ key: "a" }), node({ key: "b" })]);
    expect(rows.map((one) => one.key)).toEqual(["a", "b"]);
  });
});
