// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** Where a city ends (D-323 addendum): one blot round its nodes, no holes. */

import { describe, expect, it } from "vitest";

import type { MapNode } from "../api";
import { cityOutlines, outlineOf } from "../panels/map/territory";

const R_M = 319_000;
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
    const loops = outlineOf(members, R_M);
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
    const loops = outlineOf(members, R_M);
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
    const loops = outlineOf(members, R_M);
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
    const outlines = cityOutlines(nodes, R_M);
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
    const outlines = cityOutlines(nodes, R_M);
    expect([...outlines.keys()]).toEqual(["city"]);
    expect(outlines.get("city")).toHaveLength(1);
  });
});
