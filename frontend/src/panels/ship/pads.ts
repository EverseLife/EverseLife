// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The pads a hull in orbit may come down on, as the globe of the console
 * shows them (D-319 item 10, D-245): the rows the server offers -- the lit
 * spaceports, or on a planet one lands anywhere on (D-233) every node of
 * its surface -- stood where the public map places them, each with the
 * ground it has free. The choice is made over the planet, seeing where each
 * node stands and whether the hull fits; the engine says no to a node with
 * no room for it.
 *
 * Pure: what is drawn and how the first frame is aimed, apart from the
 * drawing, so that both can be pinned by a test.
 */

import type { MapNode, WorldMap } from "../../api";
import { arcDeg, type Geo } from "../map/globe";
import type { Pad } from "./model";

/** A pad as the globe stands it: placed, named, with its room. */
export type PadMark = {
  node: string;
  name: string;
  place: Geo;
  /** The pad's free ground in square metres, as the server said; null
   *  where it did not. */
  room: number | null;
};

/** The placed nodes of a planet's surface in the public map. */
export function surfaceOf(world: WorldMap | null, planet: string): MapNode[] {
  if (!world) return [];
  return world.nodes.filter(
    (node) => node.layer !== "space" && node.planet === planet && !!node.place && "lat" in node.place,
  );
}

/**
 * The marks on the globe: the offered pads by their place in the public
 * map. A pad the map cannot place -- flat, or younger than the snapshot --
 * is not drawn; the list beside the globe still names it, so nothing is
 * lost by being undrawable.
 */
export function padsOn(world: WorldMap | null, planet: string, offered: readonly Pad[]): PadMark[] {
  const placed = new Map(surfaceOf(world, planet).map((node) => [node.key, node]));
  return offered.flatMap((pad) => {
    const node = placed.get(pad.node);
    if (!node) return [];
    return [
      {
        node: pad.node,
        name: pad.name || node.name,
        place: node.place as Geo,
        room: typeof pad.room === "number" ? pad.room : null,
      },
    ];
  });
}

/**
 * How wide the first frame is, in degrees of arc across: the marks and a
 * margin round them, so a city's three piers do not open as one dot on a
 * whole disk, and one pier does not open as a field of bare ground.
 */
export function spreadOf(marks: readonly PadMark[], least: number, margin: number): number {
  let widest = 0;
  for (let i = 0; i < marks.length; i++) {
    for (let j = i + 1; j < marks.length; j++) {
      widest = Math.max(widest, arcDeg(marks[i].place, marks[j].place));
    }
  }
  return Math.max(least, widest * margin);
}

/** Where the first frame looks: the chosen mark, else the first. */
export function aimOf(marks: readonly PadMark[], chosen: string): Geo | null {
  return (marks.find((mark) => mark.node === chosen) ?? marks[0])?.place ?? null;
}

/**
 * How much larger than the least of them a mark is drawn: by the square
 * root of its room, an area being what it is, up to `most`; a mark with no
 * room said is drawn as the least.
 */
export function growthOf(marks: readonly PadMark[], most: number): (room: number | null) => number {
  const least = marks.reduce(
    (best, mark) => (mark.room !== null && mark.room > 0 ? Math.min(best, mark.room) : best),
    Infinity,
  );
  return (room) =>
    room !== null && room > 0 && Number.isFinite(least) ? Math.min(most, Math.sqrt(room / least)) : 1;
}
