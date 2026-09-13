// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** The ship's scheme, computed (D-288, D-340): lanes, cards, lines, and the
 *  three edits a port's line takes. */

import { describe, expect, it } from "vitest";

import {
  MEASURE,
  layout,
  machineHeight,
  moved,
  portKey,
  toneOfVessel,
  withVessel,
  without,
} from "../panels/ship/lanes";
import { suits, type Feed, type FeedPort } from "../wire/lines";

const water: FeedPort = { port: "water", liquids: ["water"], way: "in", lines: ["tank"] };
const air: FeedPort = { port: "oxygen", liquids: ["oxygen"], way: "out", lines: ["b1", "b2"] };
const vent: FeedPort = { port: "hydrogen", liquids: ["hydrogen"], way: "vent", lines: [] };

const feed: Feed = {
  ship: "s",
  yours: true,
  rooms: [
    { node: "bridge", node_name: "Рубка" },
    { node: "empty", node_name: "Пустой" },
    { node: "hold", node_name: "Трюм" },
  ],
  machines: [
    {
      item: "el",
      goods: "electrolyzer",
      node: "bridge",
      node_name: "Рубка",
      ports: [water, air, vent],
      stall: "oxygen",
    },
  ],
  vessels: [
    { item: "tank", goods: "fuel_tank", node: "hold", node_name: "Трюм", holds: [{ goods: "water", amount: 10 }] },
    { item: "b1", goods: "oxygen_tank", node: "hold", node_name: "Трюм", holds: [] },
    { item: "b2", goods: "oxygen_tank", node: "bridge", node_name: "Рубка", name: "Левый борт", holds: [{ goods: "oxygen", amount: 6 }] },
    { item: "odd", goods: "canister", node: "attic", node_name: "Чердак", holds: [{ goods: "alcohol", amount: 2 }] },
  ],
};

describe("the scheme's picture", () => {
  const picture = layout(feed);

  it("draws a lane per compartment with something to plumb, in laying order", () => {
    expect(picture.lanes.map((lane) => lane.node)).toEqual(["bridge", "hold", "attic"]);
    const [bridge, hold, attic] = picture.lanes;
    expect(hold.y).toBeGreaterThanOrEqual(bridge.y + bridge.h);
    expect(attic.y).toBeGreaterThanOrEqual(hold.y + hold.h);
  });

  it("stands machines left and vessels right, in their own lanes", () => {
    const machine = picture.machines.get("el")!;
    const tank = picture.vessels.get("tank")!;
    expect(machine.x).toBe(0);
    expect(tank.x).toBe(MEASURE.machineW + MEASURE.gapX);
    expect(machine.h).toBe(machineHeight(feed.machines[0]));
    expect(tank.y).toBeGreaterThan(picture.lanes[1].y);
  });

  it("puts a port's dot on the card's right edge, row by row", () => {
    const machine = picture.machines.get("el")!;
    const first = picture.ports.get(portKey("el", "water"))!;
    const second = picture.ports.get(portKey("el", "oxygen"))!;
    expect(first.x).toBe(MEASURE.machineW);
    expect(first.y).toBe(machine.y + MEASURE.headH + MEASURE.portH / 2);
    expect(second.y - first.y).toBe(MEASURE.portH);
  });

  it("runs one line per vessel on a port, ranked, in the port's tone", () => {
    const lines = picture.lines.map((line) => [line.port, line.vessel, line.rank, line.tone]);
    expect(lines).toEqual([
      ["water", "tank", 0, "water"],
      ["oxygen", "b1", 0, "oxygen"],
      ["oxygen", "b2", 1, "oxygen"],
    ]);
    expect(picture.lines[0].d.startsWith(`M ${MEASURE.machineW} `)).toBe(true);
  });

  it("tones a vessel by a port that takes what it holds, never by a name", () => {
    expect(toneOfVessel(feed, feed.vessels[0])).toBe("water");
    expect(toneOfVessel(feed, feed.vessels[1])).toBe("none");
    expect(toneOfVessel(feed, feed.vessels[3])).toBe("none");
  });
});

describe("a port's line, edited", () => {
  it("adds a vessel at the end once", () => {
    expect(withVessel(air, "b3")).toEqual(["b1", "b2", "b3"]);
    expect(withVessel(air, "b1")).toEqual(["b1", "b2"]);
  });

  it("moves a vessel within the line and no further", () => {
    expect(moved(air, "b2", -1)).toEqual(["b2", "b1"]);
    expect(moved(air, "b1", -1)).toEqual(["b1", "b2"]);
    expect(moved(air, "b2", 1)).toEqual(["b1", "b2"]);
  });

  it("takes a vessel off", () => {
    expect(without(air, "b1")).toEqual(["b2"]);
  });

  it("lets on a vessel that is empty or holds the port's liquid, and no other", () => {
    expect(suits(air, feed.vessels[1])).toBe(true);
    expect(suits(air, feed.vessels[2])).toBe(true);
    expect(suits(air, feed.vessels[0])).toBe(false);
  });
});
