// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The picture of the ship's scheme (D-288, D-340), computed and never stored.
 *
 * Lanes are the compartments in the order they were laid; in each, the
 * machines stand on the left and the vessels on the right, and a line runs
 * from a machine's port to a vessel -- across lanes as often as not, because
 * the hull is one building. Nothing here is a coordinate anybody saved: the
 * whole drawing follows from `line.view`, the trick of the factory floor
 * (D-253), and a hull re-laid or re-plumbed is simply drawn again.
 *
 * A pure module, so the arithmetic is tested without a page.
 */

import type { Feed, FeedMachine, FeedPort, FeedVessel } from "../../wire/lines";
import { curve } from "../curve";

/** The drawing's measures, px. */
export const MEASURE = {
  machineW: 220,
  vesselW: 200,
  /** Between the machines' column and the vessels': room for the curves. */
  gapX: 120,
  /** A card's head, where its name stands. */
  headH: 26,
  /** One port's row on a machine card. */
  portH: 22,
  /** The line saying why an automat stands. */
  stallH: 20,
  pad: 8,
  vesselH: 58,
  gapY: 8,
  /** A lane's head, where its compartment is named. */
  laneHeadH: 22,
  laneGap: 12,
} as const;

export type Spot = { x: number; y: number; w: number; h: number };

export type Lane = { node: string; name: string; y: number; h: number };

export type Line = {
  key: string;
  machine: string;
  port: string;
  vessel: string;
  rank: number;
  /** The tone the line is drawn in: the port's own name (`fuel`, `water`). */
  tone: string;
  d: string;
  /** Where it arrives, for the rank written beside its end. */
  end: { x: number; y: number };
};

export type Layout = {
  width: number;
  height: number;
  lanes: Lane[];
  machines: Map<string, Spot>;
  /** `${machine}:${port}` -> the port's dot, on the card's right edge. */
  ports: Map<string, { x: number; y: number }>;
  vessels: Map<string, Spot>;
  lines: Line[];
};

export function portKey(machine: string, port: string): string {
  return `${machine}:${port}`;
}

export function machineHeight(machine: FeedMachine): number {
  return (
    MEASURE.headH +
    machine.ports.length * MEASURE.portH +
    (machine.stall ? MEASURE.stallH : 0) +
    MEASURE.pad
  );
}

/**
 * The tone of a vessel: the name of a port that takes what it holds -- so a
 * tank of rocket fuel is drawn in the engines' tone -- or `none` for an empty
 * one and one holding what no port aboard takes. Port names are keys of the
 * schema, never goods names, so no liquid is named here.
 */
export function toneOfVessel(feed: Feed, vessel: FeedVessel): string {
  const held = vessel.holds[0]?.goods;
  if (held === undefined) return "none";
  for (const machine of feed.machines) {
    const port = machine.ports.find((one: FeedPort) => one.liquids.includes(held));
    if (port) return port.port;
  }
  return "none";
}

/** Lay the scheme out: lanes in laying order, machines left, vessels right. */
export function layout(feed: Feed): Layout {
  const vesselX = MEASURE.machineW + MEASURE.gapX;
  const width = vesselX + MEASURE.vesselW;
  //: The rooms as laid; a thing in a room the reading did not list (a room
  //: laid between two reads) gets a lane of its own at the end rather than
  //: vanishing from the picture.
  const order = feed.rooms.map((room) => ({ node: room.node, name: room.node_name }));
  const known = new Set(order.map((room) => room.node));
  for (const thing of [...feed.machines, ...feed.vessels]) {
    if (!known.has(thing.node)) {
      known.add(thing.node);
      order.push({ node: thing.node, name: thing.node_name });
    }
  }

  const lanes: Lane[] = [];
  const machines = new Map<string, Spot>();
  const vessels = new Map<string, Spot>();
  const ports = new Map<string, { x: number; y: number }>();
  let y = 0;
  for (const room of order) {
    const here = feed.machines.filter((one) => one.node === room.node);
    const stood = feed.vessels.filter((one) => one.node === room.node);
    //: A compartment with nothing to plumb draws no lane: the scheme is
    //: about the lines, and the plan already shows every room.
    if (here.length === 0 && stood.length === 0) continue;
    const top = y + MEASURE.laneHeadH;
    let left = top;
    for (const machine of here) {
      const h = machineHeight(machine);
      machines.set(machine.item, { x: 0, y: left, w: MEASURE.machineW, h });
      machine.ports.forEach((port, row) => {
        ports.set(portKey(machine.item, port.port), {
          x: MEASURE.machineW,
          y: left + MEASURE.headH + row * MEASURE.portH + MEASURE.portH / 2,
        });
      });
      left += h + MEASURE.gapY;
    }
    let right = top;
    for (const vessel of stood) {
      vessels.set(vessel.item, { x: vesselX, y: right, w: MEASURE.vesselW, h: MEASURE.vesselH });
      right += MEASURE.vesselH + MEASURE.gapY;
    }
    const h = Math.max(left, right) - y;
    lanes.push({ node: room.node, name: room.name, y, h });
    y += h + MEASURE.laneGap;
  }

  const lines: Line[] = [];
  for (const machine of feed.machines) {
    for (const port of machine.ports) {
      const from = ports.get(portKey(machine.item, port.port));
      if (!from) continue;
      port.lines.forEach((vessel, rank) => {
        const to = vessels.get(vessel);
        if (!to) return;
        const end = { x: to.x, y: to.y + to.h / 2 };
        lines.push({
          key: `${machine.item}:${port.port}:${vessel}`,
          machine: machine.item,
          port: port.port,
          vessel,
          rank,
          tone: port.port,
          d: curve(from.x, from.y, end.x, end.y),
          end,
        });
      });
    }
  }
  return { width, height: Math.max(0, y - MEASURE.laneGap), lanes, machines, ports, vessels, lines };
}

/** A port's line with one vessel added at its end; unchanged if it is on already. */
export function withVessel(port: FeedPort, vessel: string): string[] {
  return port.lines.includes(vessel) ? port.lines : [...port.lines, vessel];
}

/** A port's line with one vessel moved by `step` places, clamped to the line. */
export function moved(port: FeedPort, vessel: string, step: number): string[] {
  const at = port.lines.indexOf(vessel);
  const to = at + step;
  if (at < 0 || to < 0 || to >= port.lines.length) return port.lines;
  const next = [...port.lines];
  [next[at], next[to]] = [next[to], next[at]];
  return next;
}

/** A port's line without one vessel. */
export function without(port: FeedPort, vessel: string): string[] {
  return port.lines.filter((one) => one !== vessel);
}
