// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** Where the scout may aim (D-321 item 4): the ring, the land, the room, the way. */

import { describe, expect, it } from "vitest";

import { UNITS_PER_METRE } from "../panels/map/globe";
import { fieldOf, ringPath, scoutable, segmentsCross, wayShadow } from "../panels/map/scout";

const M = UNITS_PER_METRE;
const origin = { x: 0, y: 0 };
const field = fieldOf(
  origin,
  { min: 5, max: 20 },
  [
    { key: "here", at: origin, area: 100 },
    { key: "hut", at: { x: 10 * M, y: 0 }, area: 100 },
  ],
  [[{ x: 0, y: -8 * M }, { x: 20 * M, y: -8 * M }]],
  "here",
);

describe("the scout's field", () => {
  it("holds the ring of the reach and nothing nearer or farther", () => {
    expect(scoutable(field, { x: 0, y: 12 * M }, true)).toBe(true);
    expect(scoutable(field, { x: 0, y: 3 * M }, true)).toBe(false);
    expect(scoutable(field, { x: 0, y: 25 * M }, true)).toBe(false);
  });

  it("keeps off the land of a node, its own and a find's least added", () => {
    //: The hut's land: sqrt(100/pi) = 5.6 m, plus a find's 4.4 m -- ten metres.
    expect(scoutable(field, { x: 10 * M, y: 8 * M }, true)).toBe(false);
    expect(scoutable(field, { x: 10 * M, y: 12 * M }, true)).toBe(true);
    //: The node one stands in blocks nothing: the ring's near does that.
    expect(field.blocks).toHaveLength(1);
  });

  it("is not aimed across a way, nor onto water", () => {
    //: The way runs at eight metres north; a point at fifteen is behind it.
    expect(scoutable(field, { x: 10 * M, y: -15 * M }, true)).toBe(false);
    expect(scoutable(field, { x: -10 * M, y: -15 * M }, true)).toBe(true);
    expect(scoutable(field, { x: 0, y: 12 * M }, false)).toBe(false);
    expect(segmentsCross({ x: 0, y: 0 }, { x: 2, y: 2 }, { x: 0, y: 2 }, { x: 2, y: 0 })).toBe(true);
    expect(segmentsCross({ x: 0, y: 0 }, { x: 1, y: 1 }, { x: 2, y: 2 }, { x: 3, y: 1 })).toBe(false);
  });

  it("draws the ring even-odd and a way's shadow out past the reach", () => {
    expect(ringPath(field).split("M")).toHaveLength(3);
    const shadow = wayShadow(field, field.ways[0]);
    expect(shadow).not.toBe(null);
    expect(shadow!.startsWith("M0 -40")).toBe(true);
    //: A way far beyond the reach throws no shadow the field holds.
    expect(wayShadow(field, [{ x: 100 * M, y: 0 }, { x: 120 * M, y: 0 }])).toBe(null);
  });
});
