// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** Where the scout may aim (D-321 item 4): the ring, the land, the room, the way. */

import { describe, expect, it } from "vitest";

import { UNITS_PER_METRE } from "../panels/map/globe";
import {
  fieldBox,
  fieldOf,
  metresBetween,
  nearestCell,
  ringPath,
  rulesOf,
  scoutable,
  segmentsCross,
  snapTo,
  warmthOf,
  joinAim,
  nodeShadow,
  segmentGap,
  wayShadow,
  type Rules,
} from "../panels/map/scout";

const M = UNITS_PER_METRE;
const origin = { x: 0, y: 0 };
//: No lattice: the tap is judged where it landed. The pressing to the
//: lattice has tests of its own below.
const LOOSE: Rules = {
  room: Math.sqrt(60 / Math.PI) / 0.8,
  step: null,
  cell: 0,
};
const field = fieldOf(
  origin,
  { min: 5, max: 20 },
  [
    { key: "here", at: origin, area: 100 },
    { key: "hut", at: { x: 10 * M, y: 0 }, area: 100 },
  ],
  [
    [
      { x: 0, y: -8 * M },
      { x: 20 * M, y: -8 * M },
    ],
  ],
  LOOSE,
);

describe("the scout's field", () => {
  it("holds the ring of the reach and nothing nearer or farther", () => {
    expect(scoutable(field, { x: 0, y: 12 * M }, true)).toBe(true);
    expect(scoutable(field, { x: 0, y: 3 * M }, true)).toBe(false);
    expect(scoutable(field, { x: 0, y: 25 * M }, true)).toBe(false);
  });

  it("keeps off the land of a node, its own and the room a find needs added", () => {
    //: The hut's land: sqrt(100/pi) = 5.6 m, plus the room a find needs --
    //: its least radius over the share it fills, 4.4 / 0.8 = 5.5 m -- so
    //: eleven metres and a little.
    expect(scoutable(field, { x: 10 * M, y: 8 * M }, true)).toBe(false);
    expect(scoutable(field, { x: 10 * M, y: 12 * M }, true)).toBe(true);
    //: The node one stands in blocks too, and that is the whole of the fix:
    //: the server measures the free radius to the **nearest** node and does
    //: not spare the origin, so a wide node underfoot shuts the near half of
    //: the ring. Drawing the ring open there sent the hand to "слишком тесно"
    //: from inside the green (owner, 2026-09-09).
    expect(field.blocks).toHaveLength(2);
    expect(scoutable(field, { x: 0, y: 6 * M }, true)).toBe(false);
  });

  it("is not aimed across a way, nor onto water", () => {
    //: The way runs at eight metres north; a point at fifteen is behind it.
    expect(scoutable(field, { x: 10 * M, y: -15 * M }, true)).toBe(false);
    expect(scoutable(field, { x: -10 * M, y: -15 * M }, true)).toBe(true);
    expect(scoutable(field, { x: 0, y: 12 * M }, false)).toBe(false);
    expect(
      segmentsCross(
        { x: 0, y: 0 },
        { x: 2, y: 2 },
        { x: 0, y: 2 },
        { x: 2, y: 0 },
      ),
    ).toBe(true);
    expect(
      segmentsCross(
        { x: 0, y: 0 },
        { x: 1, y: 1 },
        { x: 2, y: 2 },
        { x: 3, y: 1 },
      ),
    ).toBe(false);
  });

  it("draws the ring even-odd and a way's shadow out past the reach", () => {
    expect(ringPath(field).split("M")).toHaveLength(3);
    const shadow = wayShadow(field, field.ways[0]);
    expect(shadow).not.toBe(null);
    expect(shadow!.startsWith("M0 -40")).toBe(true);
    //: A way far beyond the reach throws no shadow the field holds.
    expect(
      wayShadow(field, [
        { x: 100 * M, y: 0 },
        { x: 120 * M, y: 0 },
      ]),
    ).toBe(null);
  });
});

describe("what the survey panel says before the run", () => {
  it("measures the aim from where one stands, in metres of the planet", () => {
    //: A degree of latitude on a sphere of this radius, and the same arc
    //: back: the panel's number is the arc against the radius, nothing else.
    const radius = 1000 * UNITS_PER_METRE;
    const one = metresBetween({ lat: 0, lon: 0 }, { lat: 1, lon: 0 }, radius);
    expect(one).toBeCloseTo(1000 * (Math.PI / 180), 6);
    expect(
      metresBetween({ lat: 1, lon: 0 }, { lat: 0, lon: 0 }, radius),
    ).toBeCloseTo(one, 9);
    //: Standing on the point one aims at is no distance at all.
    expect(metresBetween({ lat: 5, lon: 5 }, { lat: 5, lon: 5 }, radius)).toBe(
      0,
    );
  });

  it("reads the relief's two thresholds off the zonal table, or judges no water at all", () => {
    const zonal = {
      frozen: { biome: "tundra", temp: [-60, -20], rain: [0, 100] },
      boreal: { biome: "taiga", temp: [-20, 4], rain: [0, 100] },
      mild: { biome: "forest", temp: [4, 60], rain: [0, 100] },
    };
    expect(warmthOf(zonal)).toEqual({ cold: -20, cool: 4 });
    //: Two tundras, a dry and a wet: the line is the warmer edge.
    expect(
      warmthOf({ ...zonal, dry: { biome: "tundra", temp: [-60, -15], rain: [0, 10] } }),
    ).toEqual({ cold: -15, cool: 4 });
    //: Nothing said, or half of it, or nonsense: no thresholds, and the
    //: caller then takes every point for land rather than guessing.
    expect(warmthOf(undefined)).toBe(null);
    expect(warmthOf({ frozen: zonal.frozen })).toBe(null);
    expect(warmthOf({ ...zonal, boreal: { biome: "taiga", temp: "тепло" } })).toBe(null);
  });
});

describe("fieldBox", () => {
  //: The mask's box, and giving it is not optional: with no
  //: `x`/`y`/`width`/`height` SVG takes -10%..110%, and under
  //: `maskUnits="userSpaceOnUse"` those are percentages of the **viewport**,
  //: not of the field. The box then rides with the camera: the field is
  //: clipped by its invisible edge on a zoom and, once past it, vanishes
  //: whole. That is how it was until 2026-09-08.
  const field = {
    origin: { x: 100, y: -40 },
    near: 20,
    far: 80,
    blocks: [],
    ways: [],
  };

  it("covers the whole ring wherever the field stands", () => {
    const box = fieldBox(field);
    expect(box.x).toBeLessThanOrEqual(field.origin.x - field.far);
    expect(box.y).toBeLessThanOrEqual(field.origin.y - field.far);
    expect(box.x + box.size).toBeGreaterThanOrEqual(field.origin.x + field.far);
    expect(box.y + box.size).toBeGreaterThanOrEqual(field.origin.y + field.far);
  });

  it("is measured in the field's own units and not the frame's", () => {
    //: The whole point: move the field and the box moves, by exactly as much.
    //: It does not depend on the camera at all, so a zoom does not touch it.
    const moved = fieldBox({ ...field, origin: { x: 1e6, y: -1e6 } });
    const box = fieldBox(field);
    expect(moved.size).toBe(box.size);
    expect(moved.x - box.x).toBe(1e6 - field.origin.x);
    //: And there is room on every side: otherwise the ring's edge would fall
    //: exactly on the box's, where rounding already cuts.
    expect(box.size).toBeGreaterThan(2 * field.far);
  });
});

describe("the lattice under the finger", () => {
  //: Terra after D-324: land a 4096th of Earth's, so a radius near 99.5 km.
  const book = {
    constants: {
      "map.lattice_m": 5,
      "explore.node_area": { min: 60, max: 240 },
      "explore.fill_share": 0.8,
    },
  };
  const RADIUS = 99_527 * UNITS_PER_METRE;

  it("reads the vault for the room a find needs and for the lattice", () => {
    const rules = rulesOf(book, RADIUS);
    //: sqrt(60/pi) / 0.8 -- the least radius over the share a find fills.
    //: The bare least radius was 1.1 m short, and the server answered the
    //: difference with "слишком тесно" from inside the green.
    expect(rules.room).toBeCloseTo(Math.sqrt(60 / Math.PI) / 0.8, 6);
    expect(rules.step).toBeCloseTo((5 / 99_527 / Math.PI) * 180, 6);
    expect(rules.cell).toBe(5);
  });

  it("says nothing where the book is silent, and guesses nothing", () => {
    const rules = rulesOf(null, RADIUS);
    expect(rules.step).toBe(null);
    //: A point given back unchanged: with no lattice there is nothing to
    //: press it to, and inventing one would move the cross off the truth.
    expect(snapTo(rules, { lat: 41.00007, lon: 24.00003 })).toEqual({
      lat: 41.00007,
      lon: 24.00003,
    });
  });

  it("presses a point to the centre of its cell, and a centre stays put", () => {
    const rules = rulesOf(book, RADIUS);
    const at = snapTo(rules, { lat: 41.00007, lon: 24.00003 });
    //: Twice pressed is once pressed: the centre of a cell is in its own cell.
    expect(snapTo(rules, at)).toEqual(at);
    //: And it moved by no more than half a cell's diagonal, which is the
    //: farthest a point can be from the centre of the cell it falls into.
    expect(
      metresBetween({ lat: 41.00007, lon: 24.00003 }, at, RADIUS),
    ).toBeLessThanOrEqual((5 * Math.SQRT2) / 2 + 1e-6);
  });

  it("keeps the ring the whole width of the reach", () => {
    //: The tap is pressed to the lattice and judged there, so the ring needs
    //: no margin: taking one would hide cells that are perfectly lawful, and
    //: the reach is tens of metres to begin with.
    const rules = rulesOf(book, RADIUS);
    const ring = fieldOf(origin, { min: 5, max: 20 }, [], [], rules);
    expect(ring.near).toBeCloseTo(5 * M, 6);
    expect(ring.far).toBeCloseTo(20 * M, 6);
  });

  it("finds the lawful cell nearest the one a finger landed on", () => {
    const rules = rulesOf(book, RADIUS);
    //: A judgement that refuses everything north of the origin's latitude:
    //: the search must step south rather than answer with nothing.
    const place = (geo: { lat: number; lon: number }) => ({
      x: (geo.lon - 24) * 1e5,
      y: (geo.lat - 41) * 1e5,
    });
    const found = nearestCell(
      rules,
      { lat: 41.00002, lon: 24 },
      place,
      (p) => p.y < 0,
    );
    expect(found).not.toBe(null);
    expect(found!.geo.lat).toBeLessThan(41);
    //: And nothing at all when nothing within the rings will do -- better a
    //: tap that does nothing than a cross a hundred metres from the finger.
    expect(nearestCell(rules, { lat: 41, lon: 24 }, place, () => false)).toBe(
      null,
    );
  });

  it("presses nothing where there is no lattice to press to", () => {
    expect(
      nearestCell(
        LOOSE,
        { lat: 41, lon: 24 },
        () => ({ x: 0, y: 0 }),
        () => true,
      ),
    ).toBe(null);
  });
});

describe("the way against the nodes beside it (D-321 addendum, 2026-09-12)", () => {
  //: A small hut eight metres east -- its land three metres round, its
  //: exclusion eight -- and the ring reaching twenty: the ground straight
  //: behind the hut lies outside its exclusion and still in its shadow.
  const small = fieldOf(
    origin,
    { min: 5, max: 20 },
    [
      { key: "here", at: origin, area: 100 },
      { key: "hut", at: { x: 8 * M, y: 0 }, area: 25 },
    ],
    [],
    LOOSE,
  );
  const behind = { x: 18 * M, y: 0 };
  const beside = { x: 16 * M, y: 8 * M };
  it("passes through no node's land", () => {
    expect(scoutable(small, behind, true)).toBe(false);
    expect(scoutable(small, beside, true)).toBe(true);
    expect(segmentGap(origin, behind, { x: 8 * M, y: 0 })).toBe(0);
    expect(segmentGap(origin, beside, { x: 8 * M, y: 0 })).toBeGreaterThan(small.blocks[1].core);
  });
  it("casts the hut's shadow over the field, and none for the node underfoot", () => {
    const [here, hut] = field.blocks;
    expect(nodeShadow(field, here)).toBeNull();
    const shadow = nodeShadow(field, hut);
    expect(shadow).not.toBeNull();
    //: The shadow's near edge is the hut's own width, at the hut.
    expect(shadow).toContain(`M${10 * M} ${hut.core}L${10 * M} ${-hut.core}`);
  });
  it("is not cast by a node whose land covers the origin", () => {
    const covered = fieldOf(
      origin,
      { min: 5, max: 20 },
      [
        { key: "here", at: origin, area: 100 },
        { key: "wide", at: { x: 2 * M, y: 0 }, area: 400 },
      ],
      [],
      LOOSE,
    );
    expect(nodeShadow(covered, covered.blocks[1])).toBeNull();
    //: Aimed away from the wide node, past its exclusion, the way starts
    //: inside its land and is not refused for passing through it -- as
    //: `aim.check` reads it.
    expect(scoutable(covered, { x: -18 * M, y: 0 }, true)).toBe(true);
  });
});

describe("the way's aim (D-321 addendum, 2026-09-12)", () => {
  const where = { here: "camp", planet: "terra" };
  it("names a known node of this ground by its place", () => {
    expect(joinAim({ key: "hut", planet: "terra", place: { lat: 1, lon: 2 } }, where)).toEqual({ lat: 1, lon: 2 });
  });
  it("names nothing for the node underfoot, another ground, a hull or a body of the sky", () => {
    expect(joinAim({ key: "camp", planet: "terra", place: { lat: 1, lon: 2 } }, where)).toBeNull();
    expect(joinAim({ key: "hut", planet: "aurora", place: { lat: 1, lon: 2 } }, where)).toBeNull();
    expect(joinAim({ key: "hull", planet: "terra", place: { lat: 1, lon: 2 }, aboard: true }, where)).toBeNull();
    expect(joinAim({ key: "moon", planet: "terra", place: { lat: 1, lon: 2 }, orbit: { radius: 1 } }, where)).toBeNull();
    expect(joinAim({ key: "room", planet: "terra", place: { x: 1, y: 2 } }, where)).toBeNull();
  });
});
