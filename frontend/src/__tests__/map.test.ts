// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** The map's pure parts: what is shown around you, and where it is drawn (D-237). */

import { describe, expect, it } from "vitest";

import type { MapNode } from "../api";
import { settle } from "../panels/map/layout";
import { DEPTH, delegate, homeCity, nearby, offworld, type Link } from "../panels/map/model";
import { mooring, passage, term } from "../panels/map/orbits";
import { long, price, spread } from "../panels/map/words";

const chain = (n: number): Link[] =>
  Array.from({ length: n - 1 }, (_, i) => ({
    a: `n${i}`,
    b: `n${i + 1}`,
    surface: "road",
    seconds: 60,
  }));

const keys = (n: number) => Array.from({ length: n }, (_, i) => `n${i}`);

const node = (over: Partial<MapNode>): MapNode =>
  ({
    key: "x",
    name: "Узел",
    layer: "city",
    parent: null,
    ring: null,
    exit: false,
    port: false,
    planet: "terra",
    orbit: null,
    deferred: false,
    aboard: false,
    flight: null,
    ...over,
  }) as MapNode;

describe("nearby", () => {
  it("reaches exactly DEPTH steps and no further", () => {
    const near = nearby("n0", keys(6), chain(6));
    expect(DEPTH).toBe(2);
    expect([...near].sort()).toEqual(["n0", "n1", "n2"]);
  });

  it("measures from where you stand, not from the graph's beginning", () => {
    const near = nearby("n3", keys(6), chain(6));
    expect([...near].sort()).toEqual(["n1", "n2", "n3", "n4", "n5"]);
  });

  it("shows the whole group when there is no node of yours to measure from", () => {
    //: Somebody else's city opened from the outside: a window with no centre
    //: would be an empty screen.
    expect(nearby(null, keys(6), chain(6)).size).toBe(6);
    expect(nearby("elsewhere", keys(6), chain(6)).size).toBe(6);
  });

  it("does not reach what no edge leads to", () => {
    const near = nearby("n0", [...keys(3), "lone"], chain(3));
    expect(near.has("lone")).toBe(false);
  });
});

describe("delegate", () => {
  it("climbs the parents to the layer being drawn", () => {
    const byKey: Record<string, MapNode> = {
      terra: node({ key: "terra", layer: "space" }),
      city: node({ key: "city", layer: "planet", parent: "terra" }),
      gate: node({ key: "gate", layer: "city", parent: "city" }),
    };
    expect(delegate(byKey, "gate", "planet")).toBe("city");
    expect(delegate(byKey, "gate", "city")).toBe("gate");
    expect(delegate(byKey, "gate", "space")).toBe("terra");
    expect(delegate(byKey, "gate", "location")).toBe(null);
  });
});

describe("homeCity", () => {
  //: A city, its gate, and a ship moored at its pier: the hull hangs under the
  //: planet, so nothing above it is a city at all.
  const byKey: Record<string, MapNode> = {
    terra: node({ key: "terra", layer: "space" }),
    capital: node({ key: "capital", layer: "planet", parent: "terra" }),
    pier: node({ key: "pier", layer: "city", parent: "capital", port: true }),
    hull: node({ key: "hull", layer: "space", parent: "terra", aboard: true }),
    cabin: node({ key: "cabin", layer: "location", parent: "hull", aboard: true }),
    far: node({ key: "far", layer: "planet", parent: "terra" }),
    veyr: node({ key: "veyr", layer: "planet", parent: "aurora", planet: "aurora" }),
    dock: node({ key: "dock", layer: "city", parent: "veyr", planet: "aurora" }),
  };

  it("names the city above where you stand", () => {
    expect(homeCity(byKey, "pier", [])).toBe("capital");
  });

  it("follows the gangway when there is no city above the hull", () => {
    //: Without this the moored crew fell through to whatever city came first
    //: in the world by name, and could not walk off their own ship.
    expect(homeCity(byKey, "cabin", [])).toBe(null);
    expect(homeCity(byKey, "cabin", [{ key: "pier" }, { key: "cabin" }])).toBe("capital");
  });

  it("prefers what is underfoot to what an exit leads to", () => {
    expect(homeCity(byKey, "pier", [{ key: "dock" }])).toBe("capital");
  });

  it("answers null when neither underfoot nor any exit is in a city", () => {
    expect(homeCity(byKey, "far", [{ key: "capital" }])).toBe(null);
  });

  it("holds on a map that does not have the node yet", () => {
    //: The world is reread after the body moves, so for a frame the key is
    //: ahead of the map -- and that frame is exactly where this used to break.
    expect(homeCity(byKey, "nowhere", [])).toBe(null);
    expect(homeCity(byKey, "nowhere", [{ key: "nowhere-either" }])).toBe(null);
  });
});

describe("offworld", () => {
  const byKey: Record<string, MapNode> = {
    gate: node({ key: "gate", planet: "terra" }),
    field: node({ key: "field", layer: "planet", planet: "pyroxis" }),
    pier: node({ key: "pier", planet: "terra", port: true }),
    hull: node({ key: "hull", planet: "terra", aboard: true }),
  };

  it("marks a node of another planet: walked-to never, flown-to only", () => {
    expect(offworld(byKey, "gate", byKey.field)).toBe(true);
    expect(offworld(byKey, "gate", byKey.gate)).toBe(false);
  });

  it("lets a moored ship be boarded: the engine moves its planet to the port's", () => {
    //: `ship/flight.py` rewrites every aboard node's planet on arrival, so the
    //: hull at your pier compares equal and the gangway stays offered.
    expect(offworld(byKey, "pier", byKey.hull)).toBe(false);
  });

  it("answers false when either planet is unknown: absence must not lock the map", () => {
    expect(offworld(byKey, "unknown", byKey.field)).toBe(false);
    expect(offworld(byKey, "gate", node({ key: "bare", planet: "" }))).toBe(false);
  });
});

describe("settle", () => {
  const given = new Map([
    ["n0", { x: 0, y: 0 }],
    ["n1", { x: 150, y: 0 }],
  ]);

  it("leaves the places the server gave exactly where they are", () => {
    const out = settle(keys(4), chain(4), given);
    expect(out.get("n0")).toEqual({ x: 0, y: 0 });
    expect(out.get("n1")).toEqual({ x: 150, y: 0 });
  });

  it("gives the same answer every time: one map for every player", () => {
    const once = settle(keys(5), chain(5), given);
    const twice = settle(keys(5), chain(5), given);
    for (const key of keys(5)) expect(once.get(key)).toEqual(twice.get(key));
  });

  it("does not depend on the order the nodes arrive in", () => {
    const forward = settle(keys(5), chain(5), given);
    const backward = settle([...keys(5)].reverse(), [...chain(5)].reverse(), given);
    for (const key of keys(5)) {
      expect(backward.get(key)!.x).toBeCloseTo(forward.get(key)!.x, 6);
      expect(backward.get(key)!.y).toBeCloseTo(forward.get(key)!.y, 6);
    }
  });

  it("leaves a node with no edges where it started instead of flinging it away", () => {
    //: Soft walls used to push whatever was unconnected to the frame's edge --
    //: as far from everybody as the frame allowed -- and the map lied about the
    //: shape of the world.
    const alone = settle(["lone"], [], new Map());
    const point = alone.get("lone")!;
    expect(Math.hypot(point.x, point.y)).toBeLessThan(700);
  });

  it("settles before it returns: the free nodes have actually spread", () => {
    //: The map appears laid out rather than crawling into place, so by the time
    //: settle returns the chain must already be a chain -- not six nodes still
    //: sitting where they were seeded.
    const out = settle(keys(6), chain(6), given);
    for (let i = 1; i < 6; i++) {
      const gap = Math.hypot(
        out.get(`n${i}`)!.x - out.get(`n${i - 1}`)!.x,
        out.get(`n${i}`)!.y - out.get(`n${i - 1}`)!.y,
      );
      expect(gap).toBeGreaterThan(60);
      expect(gap).toBeLessThan(400);
    }
  });
});

describe("words", () => {
  //: The hour is the border between two units and both sides of it want their
  //: own: "60 мин" reads worse than "1 ч", and "0.7 ч" worse than "40 мин".
  it("keeps minutes up to the hour and goes over to hours after it", () => {
    expect(long(40)).toBe("40 мин");
    expect(long(59)).toBe("59 мин");
    expect(long(60)).toBe("1 ч");
    expect(long(90)).toBe("1.5 ч");
  });

  it("names the unit once while there is only one of them", () => {
    expect(spread(10, 40)).toBe("10–40 мин");
    expect(spread(60, 120)).toBe("1–2 ч");
    //: Across the border both ends are spelled whole, or it reads "40–2 ч".
    expect(spread(40, 120)).toBe("40 мин – 2 ч");
  });

  it("prints an empty spread as the term it is", () => {
    expect(spread(30, 30)).toBe("30–30 мин");
  });

  //: A step across town costs a fraction of a unit, and "0.0" would lie about
  //: it: there is a price.
  it("does not round the road's price down to nothing", () => {
    expect(price(0)).toBe("0");
    expect(price(0.02)).toBe("<0.1");
    expect(price(0.34)).toBe("0.3");
  });
});

describe("orbits", () => {
  it("counts a term in hours up to a day and in days after it", () => {
    expect(term(3.25)).toBe("3.3 ч");
    expect(term(12)).toBe("12 ч");
    //: Rounded before the unit is chosen: the other way round 23.9 gave "24 ч"
    //: and 24 gave "1.0 сут", the earlier term reading as the longer one.
    expect(term(23.9)).toBe("1.0 сут");
    expect(term(23.4)).toBe("23 ч");
    expect(term(24)).toBe("1.0 сут");
    expect(term(36)).toBe("1.5 сут");
  });

  //: A passage costs the vault's two ends and the share of the distance
  //: between them -- the same formula the server settles a flight by (D-037).
  it("prices a passage between the window and opposition by distance", () => {
    const route = { window_hours: 10, apart_hours: 30 } as never;
    expect(passage(route, 100, 100, 300)).toBe(10);
    expect(passage(route, 300, 100, 300)).toBe(30);
    expect(passage(route, 200, 100, 300)).toBe(20);
  });

  it("keeps the price inside the vault's ends whatever the distance", () => {
    const route = { window_hours: 10, apart_hours: 30 } as never;
    expect(passage(route, 0, 100, 300)).toBe(10);
    expect(passage(route, 1000, 100, 300)).toBe(30);
    //: The ends have met: nothing to divide, and the price is the near one.
    expect(passage(route, 150, 200, 200)).toBe(10);
  });

  //: A ship's mooring is its own and does not move: otherwise the hull would
  //: jump around its planet on every reread of the map.
  it("gives a ship a steady mooring somewhere on the circle", () => {
    expect(mooring("ship.node.abc")).toBe(mooring("ship.node.abc"));
    expect(mooring("ship.node.abc")).not.toBe(mooring("ship.node.abd"));
    for (const key of ["a", "ship.node.abc", "", "длинный ключ"]) {
      expect(mooring(key)).toBeGreaterThanOrEqual(0);
      expect(mooring(key)).toBeLessThan(Math.PI * 2);
    }
  });
});
