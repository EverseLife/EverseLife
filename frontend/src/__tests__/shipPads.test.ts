// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The pads on the console's globe (D-319 item 10, D-245): which nodes stand
 * on it, where the first frame looks, how wide it opens and how the marks
 * grow with their room. Pure, so pinned here, apart from the drawing.
 */

import { describe, expect, it } from "vitest";

import type { MapNode, WorldMap } from "../api";
import { arcDeg } from "../panels/map/globe";
import { aimOf, growthOf, padsOn, spreadOf } from "../panels/ship/pads";

const node = (key: string, over: Partial<MapNode> = {}): MapNode =>
  ({
    key,
    name: key,
    layer: "planet",
    parent: null,
    port: false,
    planet: "terra",
    place: { lat: 10, lon: 20 },
    ...over,
  }) as MapNode;

const world: WorldMap = {
  nodes: [
    node("port_a", { area: 240, port: true }),
    node("port_b", { area: 60, port: true, place: { lat: 10, lon: 20.001 } }),
    node("flat", { place: { x: 1, y: 2 } }),
    node("field", { planet: "pyroxis", name: "", area: 120, place: { lat: -5, lon: 3 } }),
    node("plateau", { planet: "pyroxis", area: 300, place: { lat: -5, lon: 4 } }),
    node("terra", { layer: "space", place: null }),
  ],
  edges: [],
  stubs: [],
  routes: [],
} as unknown as WorldMap;

describe("the pads on the globe", () => {
  it("stands the offered pads where the public map places them, with their room", () => {
    const marks = padsOn(world, "terra", [
      { node: "port_a", name: "A", room: 100 },
      { node: "port_b", name: "B" },
      //: A pad the map cannot place is not drawn; the list still names it.
      { node: "flat", name: "Flat", room: 5 },
      { node: "gone", name: "Gone", room: 5 },
    ]);
    expect(marks.map((mark) => [mark.node, mark.room])).toEqual([
      ["port_a", 100],
      ["port_b", null],
    ]);
    //: The server's name, not the snapshot's: the row is live, the map is old.
    expect(marks[0].name).toBe("A");
  });

  it("names a nameless node by nothing, so the console words it by its room", () => {
    //: D-233: every node of Pyroxis is a row, and a found node has no name
    //: (D-321) -- the server sends it empty and the snapshot has it empty.
    const marks = padsOn(world, "pyroxis", [
      { node: "field", name: "", anywhere: true, room: 120 },
      { node: "plateau", name: "Плато", anywhere: true, room: 20 },
    ]);
    expect(marks.map((mark) => mark.name)).toEqual(["", "Плато"]);
  });

  it("draws nothing before the map has come", () => {
    expect(padsOn(null, "terra", [{ node: "port_a", name: "A" }])).toEqual([]);
  });

  it("aims the first frame at the chosen mark, else the first", () => {
    const marks = padsOn(world, "pyroxis", [
      { node: "field", name: "", anywhere: true },
      { node: "plateau", name: "Плато", anywhere: true },
    ]);
    expect(aimOf(marks, "plateau")).toEqual({ lat: -5, lon: 4 });
    expect(aimOf(marks, "nowhere")).toEqual({ lat: -5, lon: 3 });
    expect(aimOf([], "plateau")).toBeNull();
  });

  it("opens the frame round every mark from where it looks, never narrower than the least arc", () => {
    const marks = padsOn(world, "pyroxis", [
      { node: "field", name: "", anywhere: true },
      { node: "plateau", name: "Плато", anywhere: true },
    ]);
    //: A degree of longitude at 5 degrees of latitude, reaching to both sides
    //: of the aim -- hence the doubling -- and the margin.
    const at = aimOf(marks, "field");
    expect(spreadOf(marks, at, 0.05, 1.6)).toBeCloseTo(2 * Math.cos((5 * Math.PI) / 180) * 1.6, 3);
    //: Aimed at one end -- and the aim is the chosen pad, which is the first
    //: of them -- every other mark is still inside the frame. Measured from
    //: the middle, because that is where the eye is: the width used to be the
    //: widest arc between two marks, and the far one fell off the picture.
    for (const chosen of ["field", "plateau"]) {
      const eye = aimOf(marks, chosen);
      const half = spreadOf(marks, eye, 0.05, 1.6) / 2;
      for (const mark of marks) expect(arcDeg(eye!, mark.place)).toBeLessThan(half);
    }
    //: Two piers metres apart open at the least arc, not as one dot.
    const piers = padsOn(world, "terra", [
      { node: "port_a", name: "A" },
      { node: "port_b", name: "B" },
    ]);
    expect(spreadOf(piers, aimOf(piers, "port_a"), 0.05, 1.6)).toBe(0.05);
    //: Nothing to look at: the least arc, and no arithmetic on a missing eye.
    expect(spreadOf([], null, 0.05, 1.6)).toBe(0.05);
  });

  it("grows a mark by the root of its room over the least, up to a ceiling", () => {
    const marks = padsOn(world, "terra", [
      { node: "port_a", name: "A", room: 400 },
      { node: "port_b", name: "B", room: 100 },
    ]);
    const growth = growthOf(marks, 1.5);
    expect(growth(100)).toBe(1);
    expect(growth(400)).toBe(1.5);
    expect(growth(225)).toBeCloseTo(1.5, 9);
    //: No room said, or none at all: the least size, not nothing.
    expect(growth(null)).toBe(1);
    expect(growth(0)).toBe(1);
    //: Nothing to measure against: every mark the least size.
    expect(growthOf([], 2)(50)).toBe(1);
  });
});
