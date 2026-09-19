// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** Where a city ends (D-323 addendum): one blot round its nodes, no holes. */

import { describe, expect, it } from "vitest";

import type { MapNode } from "../api";
import { cityOutlines, marchingSquares, outlineLaw, outlineOf, type OutlineLaw } from "../panels/map/territory";

const R_M = 319_000;
//: The vault's numbers (`city.outline_*`, D-356), as `/public/constants` carries them.
const CONSTANTS = {
  "city.outline_power": 4,
  "city.outline_reach_share": 0.5,
  "city.outline_lone_reach_m": 6,
  "city.outline_cells_per_step": 8,
  "city.outline_max_cells": 160,
  "city.outline_bridge_cells": 1.5,
};
const LAW = outlineLaw(CONSTANTS) as OutlineLaw;
const node = (over: Partial<MapNode>): MapNode =>
  ({ key: "n", name: "", layer: "planet", parent: "city", planet: "terra", ...over }) as MapNode;
const at = (key: string, lat: number, lon: number, area = 100) =>
  node({ key, place: { lat, lon }, area });
//: Degrees per metre on this world, at the equator.
const DEG = 180 / Math.PI / R_M;

/** Whether a point lies inside a loop (even-odd). */
function inside(loop: { lat: number; lon: number }[], p: { lat: number; lon: number }): boolean {
  let hit = false;
  for (let i = 0, j = loop.length - 1; i < loop.length; j = i++) {
    const a = loop[i];
    const b = loop[j];
    if (a.lat > p.lat !== b.lat > p.lat && p.lon < ((b.lon - a.lon) * (p.lat - a.lat)) / (b.lat - a.lat) + a.lon) {
      hit = !hit;
    }
  }
  return hit;
}

describe("a city's outline", () => {
  it("joins nodes a street apart into one blot that holds them all, with no hole", () => {
    //: A ring of six nodes sixty metres apart about an empty middle.
    const members = [0, 1, 2, 3, 4, 5].map((k) =>
      at(`n${k}`, 60 * DEG * Math.sin((k * Math.PI) / 3), 60 * DEG * Math.cos((k * Math.PI) / 3)),
    );
    const loops = outlineOf(members, R_M, LAW);
    expect(loops).toHaveLength(1);
    for (const m of members) expect(inside(loops[0], m.place as { lat: number; lon: number })).toBe(true);
    //: The middle of the ring is the city's too: no hole where no node is.
    expect(inside(loops[0], { lat: 0, lon: 0 })).toBe(true);
    //: A hundred metres out is not.
    expect(inside(loops[0], { lat: 0, lon: 200 * DEG })).toBe(false);
  });

  it("reaches an outlying node with an isthmus instead of leaving it a blot of its own", () => {
    //: A city is one place: a mine half a kilometre out is a part of it, not
    //: a second city with a circle round it. Its own land does not reach that
    //: far, so the shortest tree that joins the nodes is bridged.
    const members = [at("a", 0, 0), at("b", 0, 60 * DEG), at("far", 0, 600 * DEG)];
    const loops = outlineOf(members, R_M, LAW);
    expect(loops).toHaveLength(1);
    for (const m of members) {
      expect(inside(loops[0], m.place as { lat: number; lon: number })).toBe(true);
    }
    //: The bridge is a neck, not a swelling: a hundred metres off the line
    //: between them is outside.
    expect(inside(loops[0], { lat: 100 * DEG, lon: 300 * DEG })).toBe(false);
    //: Rounded: no two neighbouring edges meet at a right angle or sharper.
    for (const loop of loops) {
      for (let i = 0; i < loop.length; i++) {
        const a = loop[(i + loop.length - 1) % loop.length];
        const b = loop[i];
        const c = loop[(i + 1) % loop.length];
        const u = { x: a.lon - b.lon, y: a.lat - b.lat };
        const v = { x: c.lon - b.lon, y: c.lat - b.lat };
        const cos = (u.x * v.x + u.y * v.y) / (Math.hypot(u.x, u.y) * Math.hypot(v.x, v.y));
        expect(cos).toBeLessThan(0.2);
      }
    }
  });

  it("joins whatever the city is spread into, in one loop", () => {
    //: Two clusters and a lone node between them: nothing here merges by its
    //: own land, and the answer is still one blot with all of them inside.
    const members = [
      at("a1", 0, 0),
      at("a2", 0, 50 * DEG),
      at("mid", 400 * DEG, 900 * DEG),
      at("b1", 0, 1800 * DEG),
      at("b2", 50 * DEG, 1850 * DEG),
    ];
    const loops = outlineOf(members, R_M, LAW);
    expect(loops).toHaveLength(1);
    for (const m of members) {
      expect(inside(loops[0], m.place as { lat: number; lon: number })).toBe(true);
    }
  });

  it("gives each city its own blot, and bridges neither into the other", () => {
    //: One blot per city, not one per planet: the rule is that a city is
    //: whole, not that everything near is one town. The two are listed
    //: alternating, so a tree laid over the wrong set would show at once.
    const town = (key: string, city: string, lon: number) =>
      node({ key, parent: city, place: { lat: 0, lon: lon * DEG }, area: 100 });
    const nodes = [
      node({ key: "terra", layer: "space", parent: null }),
      node({ key: "a", parent: "terra", place: { lat: 0, lon: 0 } }),
      node({ key: "b", parent: "terra", place: { lat: 0, lon: 600 * DEG } }),
      town("a1", "a", 0),
      town("b1", "b", 600),
      town("a2", "a", 60),
      town("b2", "b", 660),
      town("a3", "a", 120),
      town("b3", "b", 720),
    ];
    const outlines = cityOutlines(nodes, R_M, LAW);
    expect([...outlines.keys()].sort()).toEqual(["a", "b"]);
    expect(outlines.get("a")).toHaveLength(1);
    expect(outlines.get("b")).toHaveLength(1);
    //: Each holds its own and neither holds the other's: half a kilometre of
    //: ground between them belongs to nobody.
    expect(inside(outlines.get("a")![0], { lat: 0, lon: 60 * DEG })).toBe(true);
    expect(inside(outlines.get("b")![0], { lat: 0, lon: 60 * DEG })).toBe(false);
    expect(inside(outlines.get("b")![0], { lat: 0, lon: 660 * DEG })).toBe(true);
    expect(inside(outlines.get("a")![0], { lat: 0, lon: 660 * DEG })).toBe(false);
  });

  it("outlines the cities among a map's nodes and nothing else", () => {
    const nodes = [
      node({ key: "terra", layer: "space", parent: null }),
      node({ key: "city", parent: "terra", place: { lat: 0, lon: 0 } }),
      at("a", 0, 0),
      at("b", 0, 40 * DEG),
      node({ key: "wild", parent: "terra", place: { lat: 1, lon: 1 } }),
    ];
    const outlines = cityOutlines(nodes, R_M, LAW);
    expect([...outlines.keys()]).toEqual(["city"]);
    expect(outlines.get("city")).toHaveLength(1);
  });

  //: A ring of five nodes six hundred metres out, four of them forty
  //: degrees apart and the fifth across the ring from them: the near ones
  //: merge by their own land, the two long chords do not, and the tree
  //: that joins them bridges one long chord and leaves the other open.
  const ring = [0, 40, 80, 120, 240].map((deg, k) =>
    at(`n${k}`, 600 * DEG * Math.sin((deg * Math.PI) / 180), 600 * DEG * Math.cos((deg * Math.PI) / 180)),
  );
  const chords: [string, string][] = [
    ["n0", "n1"],
    ["n1", "n2"],
    ["n2", "n3"],
    ["n3", "n4"],
    ["n4", "n0"],
  ];

  it("closes the land inside a ring of ways, and leaves it open without them (D-332)", () => {
    //: Without the ways the blot is a horseshoe: the middle of the ring is
    //: outside, reached through the open chord.
    const open = outlineOf(ring, R_M, LAW);
    expect(open).toHaveLength(1);
    expect(inside(open[0], { lat: 0, lon: 0 })).toBe(false);
    //: With every chord a way the ring closes and the middle is the city's:
    //: a hole in the blot, and the city has none.
    const closed = outlineOf(ring, R_M, LAW, chords);
    expect(closed).toHaveLength(1);
    expect(inside(closed[0], { lat: 0, lon: 0 })).toBe(true);
    for (const m of ring) expect(inside(closed[0], m.place as { lat: number; lon: number })).toBe(true);
    //: And no wider than the ring: a kilometre and a half out is not the city's.
    expect(inside(closed[0], { lat: 1500 * DEG, lon: 0 })).toBe(false);
    //: A way to a node that is not a member is nobody's street.
    const astray = outlineOf(ring, R_M, LAW, [["n3", "elsewhere"]]);
    expect(inside(astray[0], { lat: 0, lon: 0 })).toBe(false);
  });

  it("counts the city's own node once, whatever its row says of its land", () => {
    //: The delegate owns itself from founding; a wire that said so on its
    //: row would group it under itself as well as head the city.
    const plain = [
      node({ key: "terra", layer: "space", parent: null }),
      node({ key: "city", parent: "terra", place: { lat: 0, lon: 0 }, area: 100 }),
      at("a", 0, 60 * DEG),
    ];
    const selfOwned = plain.map((one) => (one.key === "city" ? { ...one, territory: "city" } : one));
    expect(cityOutlines(selfOwned, R_M, LAW)).toEqual(cityOutlines(plain, R_M, LAW));
  });

  it("groups a node by the land the wire says it is on, and lays the city's streets (D-332)", () => {
    //: A find taken in by a highway hangs under the planet and says whose
    //: land it is; the map's edges among the city's nodes are its streets.
    const nodes = [
      node({ key: "terra", layer: "space", parent: null }),
      node({ key: "city", parent: "terra", place: { lat: 0, lon: 0 }, area: 100 }),
      ...ring.map((member) => ({ ...member, parent: "terra", territory: "city" })),
      node({ key: "wild", parent: "terra", place: { lat: 0, lon: 3000 * DEG } }),
    ];
    const ways = chords.map(([a, b]) => ({ a, b, surface: "paved" as const, seconds: 1 }));
    const outlines = cityOutlines(nodes, R_M, LAW, ways);
    expect([...outlines.keys()]).toEqual(["city"]);
    const loops = outlines.get("city")!;
    expect(loops).toHaveLength(1);
    for (const m of ring) expect(inside(loops[0], m.place as { lat: number; lon: number })).toBe(true);
    expect(inside(loops[0], { lat: 0, lon: 3000 * DEG })).toBe(false);
    //: Without the ways the same nodes are grouped the same and the ring
    //: stays open: the grouping is the wire's, the closing is the streets'.
    //: The ground west of the ring's open chord: outside the horseshoe,
    //: under the street once it is laid.
    const bare = cityOutlines(nodes, R_M, LAW);
    expect(inside(bare.get("city")![0], { lat: 0, lon: -400 * DEG })).toBe(false);
    expect(inside(loops[0], { lat: 0, lon: -400 * DEG })).toBe(true);
  });

  it("draws the same line whatever order the map's rows come in (D-356)", () => {
    //: Four nodes at the corners of a square: every gap to a neighbour is
    //: the same, and which one the tree takes first is decided by the order.
    const square = [at("d", 0, 0), at("a", 0, 90 * DEG), at("c", 90 * DEG, 0), at("b", 90 * DEG, 90 * DEG)];
    expect(outlineOf([...square].reverse(), R_M, LAW)).toEqual(outlineOf(square, R_M, LAW));
  });

  it("splits a saddle by its middle: the middle goes with the corners on its side (D-356)", () => {
    //: One cell, corners 0..3 counter-clockwise from (0,0): 0 and 2 outside,
    //: 1 and 3 inside. The middle is the mean.
    const cell = (low: number, high: number) =>
      marchingSquares(new Float64Array([low, high, high, low]), 2, 2, 1, (i, j) => ({ x: i, y: j }));
    //: Rows of the raster run in x: (0,0), (1,0) / (0,1), (1,1) -- so the
    //: corners 0 (0,0) and 2 (1,1) hold `low`, 1 (1,0) and 3 (0,1) `high`.
    //: A segment cuts round a corner when both its ends lie on the two
    //: edges that meet there.
    const cutsAround = (segments: ReturnType<typeof cell>, cx: number, cy: number) =>
      segments.some(([a, b]) => [a, b].every((p) => p.x === cx || p.y === cy));
    //: Middle below the level: the outside corners are one, the cuts go
    //: round the inside corners (1,0) and (0,1).
    const open = cell(0, 1.5);
    expect(cutsAround(open, 1, 0) && cutsAround(open, 0, 1)).toBe(true);
    //: Middle at or above it: the inside corners are one, the cuts go round
    //: the outside corners (0,0) and (1,1).
    const joined = cell(0, 2.5);
    expect(cutsAround(joined, 0, 0) && cutsAround(joined, 1, 1)).toBe(true);
  });

  it("reads its numbers off the vault, and draws nothing without them (D-356)", () => {
    expect(LAW).toEqual({
      power: 4,
      reachShare: 0.5,
      loneReachM: 6,
      cellsPerStep: 8,
      maxCells: 160,
      bridgeCells: 1.5,
    });
    expect(outlineLaw({})).toBeNull();
    expect(outlineLaw({ ...CONSTANTS, "city.outline_max_cells": 0 })).toBeNull();
    expect(outlineLaw(null)).toBeNull();
  });
});

/**
 * The line the engine reads (D-356): the same members, the same ways and the
 * same points as `backend/tests/test_outline.py`, which asks `outline.covers`
 * what this asks the drawn loops. The two trees cannot import each other; a
 * change to the field on one side fails the other's copy of these numbers.
 * The capital is the vault's (`world.yaml`), laid at its own latitude, and
 * the ring is D-332's.
 */
describe("the line the engine reads", () => {
  const capital = [
    { key: "terra.capital.core", lat: 32.9035, lon: -105.884431, area: 120 },
    { key: "terra.capital.library", lat: 32.904936885, lon: -105.888709548, area: 200 },
    { key: "terra.capital.market", lat: 32.9035, lon: -105.882077799, area: 200 },
    { key: "terra.capital.hall", lat: 32.906194159, lon: -105.884431, area: 180 },
    { key: "terra.capital.forge", lat: 32.901344673, lon: -105.875659977, area: 260 },
    { key: "terra.capital.pit", lat: 32.897213629, lon: -105.889779185, area: 300 },
    { key: "terra.capital.gate", lat: 32.901165062, lon: -105.886356347, area: 80 },
    { key: "terra.capital.port", lat: 32.907810654, lon: -105.881863871, area: 240 },
    { key: "terra.capital.jail", lat: 32.899907788, lon: -105.89427166, area: 120 },
  ];
  const streets: [string, string][] = [
    ["terra.capital.core", "terra.capital.library"],
    ["terra.capital.core", "terra.capital.market"],
    ["terra.capital.core", "terra.capital.hall"],
    ["terra.capital.library", "terra.capital.market"],
    ["terra.capital.library", "terra.capital.hall"],
    ["terra.capital.market", "terra.capital.forge"],
    ["terra.capital.core", "terra.capital.gate"],
    ["terra.capital.core", "terra.capital.port"],
    ["terra.capital.gate", "terra.capital.jail"],
    ["terra.capital.pit", "terra.capital.gate"],
  ];
  //: The oil field lies inside, three metres in; the coal pit and the
  //: floodplain -- where a newcomer makes the first axe (D-196) -- outside.
  const probes: [string, number, number, boolean][] = [
    ["oilfield", 32.899548567, -105.885714564, true],
    ["coal", 32.895417523, -105.894913442, false],
    ["floodplain", 32.892184533, -105.892346314, false],
    ["between core and market", 32.9035, -105.883361363, true],
    ["far east", 32.9035, -105.84164552, false],
    ["under the port", 32.908888318, -105.881863871, true],
    ["north of the hall", 32.914276636, -105.884431, false],
  ];
  const ring = [
    { key: "n0", lat: 0, lon: 0.107766356, area: 100 },
    { key: "n1", lat: 0.069270879, lon: 0.082553819, area: 100 },
    { key: "n2", lat: 0.106129143, lon: 0.018713431, area: 100 },
    { key: "n3", lat: 0.093328402, lon: -0.053883178, area: 100 },
    { key: "n4", lat: -0.093328402, lon: -0.053883178, area: 100 },
  ];
  const chords: [string, string][] = [
    ["n0", "n1"],
    ["n1", "n2"],
    ["n2", "n3"],
    ["n3", "n4"],
    ["n4", "n0"],
  ];
  //: The middle of the ring, the ground west of its open chord, and a point
  //: well beyond it: open without the ways, closed with them.
  const ringProbes: [number, number, boolean, boolean][] = [
    [0, 0, false, true],
    [0, -0.071844238, false, true],
    [0.269415891, 0, false, false],
  ];
  const rows = (members: { key: string; lat: number; lon: number; area: number }[]) =>
    members.map((m) => node({ key: m.key, place: { lat: m.lat, lon: m.lon }, area: m.area }));
  const within = (loops: { lat: number; lon: number }[][], lat: number, lon: number) =>
    loops.some((loop) => inside(loop, { lat, lon }));

  it("covers the capital's own ground and not the fields beyond it", () => {
    const loops = outlineOf(rows(capital), R_M, LAW, streets);
    for (const [name, lat, lon, expected] of probes) expect([name, within(loops, lat, lon)]).toEqual([name, expected]);
  });

  it("covers the middle of a ring its ways close, and not of one they leave open", () => {
    const open = outlineOf(rows(ring), R_M, LAW);
    const closed = outlineOf(rows(ring), R_M, LAW, chords);
    for (const [lat, lon, whenOpen, whenClosed] of ringProbes) {
      expect(within(open, lat, lon)).toBe(whenOpen);
      expect(within(closed, lat, lon)).toBe(whenClosed);
    }
  });
});
