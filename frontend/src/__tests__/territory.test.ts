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

  it("leaves an outlying node its own blot, and rounds the edges", () => {
    const members = [at("a", 0, 0), at("b", 0, 60 * DEG), at("far", 0, 600 * DEG)];
    const loops = outlineOf(members, R_M);
    expect(loops).toHaveLength(2);
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
