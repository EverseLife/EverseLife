// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** What a node of the scene does under the hand (`panels/map/useNodeBehaviour.ts`). */

import { describe, expect, it } from "vitest";

import type { Exit, MapNode, Transit } from "../api";
import { reachableFrom, sizesOf, walkTargetsOf } from "../panels/map/useNodeBehaviour";

const node = (over: Partial<MapNode>): MapNode =>
  ({
    key: "x",
    name: "Node",
    layer: "planet",
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

const exit = (key: string, seconds: number): Exit => ({
  key,
  name: key,
  surface: "road",
  seconds,
  stamina: 0,
});

//: A closed city stands for its streets; everything else for itself.
const CITY: Record<string, string> = { gate: "city", market: "city" };
const reprScene = (key: string) => CITY[key] ?? key;

describe("where a step towards a node goes", () => {
  it("takes the quickest exit among those one delegate stands for", () => {
    //: Two streets into one closed city are one step on the map, and the
    //: shorter of them is the one offered.
    const steps = walkTargetsOf(
      [exit("gate", 90), exit("market", 40), exit("field", 60)],
      reprScene,
      "home",
    );
    expect(steps).toEqual({
      city: { key: "market", seconds: 40 },
      field: { key: "field", seconds: 60 },
    });
  });

  it("offers no step towards where one stands", () => {
    //: Standing in the market, the gate is a street of the same closed city:
    //: the city is not somewhere to walk to.
    expect(walkTargetsOf([exit("gate", 30)], reprScene, "market")).toEqual({});
    //: And an exit the scene draws nothing for is no step on the map.
    expect(walkTargetsOf([exit("hidden", 30)], () => null, "home")).toEqual({});
  });
});

describe("how large a closed city is drawn", () => {
  it("counts the nodes hanging under each", () => {
    const sizes = sizesOf([
      node({ key: "city" }),
      node({ key: "gate", parent: "city" }),
      node({ key: "market", parent: "city" }),
      node({ key: "flat", parent: "market" }),
    ]);
    expect(sizes.get("city")).toBe(2);
    expect(sizes.get("market")).toBe(1);
    expect(sizes.has("gate")).toBe(false);
  });
});

describe("whether a step leads to a node", () => {
  const byKey: Record<string, MapNode> = {
    home: node({ key: "home" }),
    field: node({ key: "field" }),
    city: node({ key: "city" }),
    aurora: node({ key: "aurora", planet: "aurora" }),
  };
  const judge = (over: Partial<Parameters<typeof reachableFrom>[0]> = {}) =>
    reachableFrom({
      ongoing: null,
      standingAt: "home",
      byKey,
      here: "home",
      groups: new Set(["city"]),
      walkTargets: {},
      ...over,
    });

  it("leads anywhere of one's own surface but where one stands", () => {
    expect(judge()(byKey.field)).toBe(true);
    expect(judge()(byKey.home)).toBe(false);
  });

  it("leads nowhere while walking", () => {
    const ongoing = { to_key: "field", from_key: "home" } as Transit;
    expect(judge({ ongoing, standingAt: null })(byKey.field)).toBe(false);
  });

  it("does not cross the void or light up another planet (D-201)", () => {
    expect(judge()(node({ key: "terra", orbit: { radius: 1, period_days: 1, phase: 0 } }))).toBe(
      false,
    );
    expect(judge()(byKey.aurora)).toBe(false);
  });

  it("leads into a group only by a step the scene offers", () => {
    expect(judge()(byKey.city)).toBe(false);
    const walkTargets = { city: { key: "gate", seconds: 30 } };
    expect(judge({ walkTargets })(byKey.city)).toBe(true);
  });
});
