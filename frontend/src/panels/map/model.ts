// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What the map is made of: layers, the frame, and the piece of the graph shown.
 *
 * The whole universe is one graph of locations, and one walks only on it.
 * Layers are a display abstraction so that the graph can be navigated:
 *
 * - **Space** -- planets and ships;
 * - **Planet** -- cities and large solitary locations;
 * - **City** -- the built-up area: rings around the bioprinter;
 * - **Location** -- sub-nodes: floors of a house, rooms of a complex.
 *
 * An upper-layer node is its group's delegate, and edges between groups are
 * projected onto delegates -- the graph stays one and the same (D-045, D-097).
 */

import type { MapNode, Transit } from "../../api";

/** The frame the map is drawn in. The camera (`viewBox`) moves over it. */
export const W = 880;
export const H = 540;

/**
 * The four layers of the wire (D-045). Once tabs of a switcher, they are
 * heights of one map now (D-319, wave 4: `bands.ts`) -- the ids stay, as the
 * server's word for where a node sits in the hierarchy.
 */
export const LAYER_IDS = ["space", "planet", "city", "location"] as const;

/** The radius of a planet's marker in the sky, map units: the corona round
 *  its orb. The approach (D-319, wave 5) opens the surface at the scale
 *  where the true disk is this size, so the marker becomes the globe. */
export const SPHERE_R = 11;
export type LayerId = (typeof LAYER_IDS)[number];

/**
 * How far from where you stand the map reaches, in steps of the graph.
 *
 * Two: the nodes you can walk to, and the ones you would see from there. A map
 * of the whole planet at once is a map of somebody else's business -- it says
 * nothing about the decision in front of you, and its labels overwrite the
 * three nodes that do. What lies further is reached by walking, and it opens as
 * you walk: the map moves with the body, not the body across the map.
 */
export const DEPTH = 2;

export type Point = { x: number; y: number };

/** An edge as the map draws it: two keys of **this** layer and what lies between. */
export type Link = { a: string; b: string; surface: string; seconds: number };

export const DASH: Record<string, string | undefined> = {
  wild: "1 7",
  trail: "4 6",
};

/**
 * The identity of a journey: where it ends, by key, or nothing when one
 * stands still (D-238).
 *
 * A walk of five nodes is **one** journey. The camera follows a journey and
 * lets the hand take the frame for the whole of it -- so what identifies it
 * must not change with every leg. `final_key` is the plan's last node and
 * comes on every leg but the last, where the leg's own end is the plan's end
 * anyway; by key, never by name, because a name is a label two places can
 * share.
 */
export function journeyOf(travel: Transit | null | undefined): string | null {
  if (!travel) return null;
  return travel.final_key ?? travel.to_key;
}

/**
 * The scene the frame stands in: a layer of one city on one planet.
 *
 * Two scenes share no coordinates, so a frame moving between them is cut,
 * never flown -- the flight would sweep across places that hold nothing.
 */
export function sceneKey(
  band: string,
  city: string | null,
  planet: string | null,
): string {
  return `${band}|${city ?? ""}|${planet ?? ""}`;
}

/** The layer in words: the player reads a place, not an enum. */
export const LAYER_NAME: Record<string, string> = {
  space: "ui-map-where-space",
  planet: "ui-map-where-planet",
  //: `city` is not here: its word is the planet's (`cityWord`).
  location: "ui-map-where-location",
};

/**
 * The node's delegate on the layer: climb the parents up to a node of this layer.
 *
 * A road "to the capital" actually leads to a specific gate inside it; on the
 * planet's map that whole city is one point, and this is the function that
 * says which.
 */
export function delegate(
  byKey: Record<string, MapNode>,
  key: string,
  layer: LayerId,
): string | null {
  let cursor: MapNode | undefined = byKey[key];
  while (cursor) {
    if (cursor.layer === layer) return cursor.key;
    cursor = cursor.parent ? byKey[cursor.parent] : undefined;
  }
  return null;
}

/**
 * The nodes as they are **drawn**: a city carries its bioprinter's place.
 *
 * A city is drawn on the node it grew from (D-319) -- the door a newcomer is
 * printed at (D-032). Its own coordinates are the anchor the layout hung the
 * city on, and standing the point there put it beside the printer, not on it.
 * Which node that is comes from the server (`core`): the map carries no
 * machines and could not tell (D-225).
 *
 * The swap happens **here**, on the way into the projection, and not in the
 * lookup that reads a drawn point afterwards. Looked up, a closed city asked
 * for the place of a node the scene was not drawing -- with the cities closed
 * the printer is not in the scene, only the city is -- and got nothing, so
 * the city vanished from the map instead of moving onto it.
 *
 * With the cities open a city's own point is not drawn at all
 * (`useScene.visibleOf`), so this changes nothing there.
 */
export function drawnAt(
  byKey: Record<string, MapNode>,
  nodes: readonly MapNode[],
): MapNode[] {
  return nodes.map((node) => {
    const core = node.core ? byKey[node.core] : undefined;
    return core?.place ? { ...node, place: core.place } : node;
  });
}

/**
 * The cities of a map: the nodes others hang under.
 *
 * One reading for the scene and for the delegates. The wire has no city layer
 * -- the world's are the sky, the surface and the inside (D-319) -- and the
 * client puts one on for itself in `geo.withCityScene`; this reads that.
 */
export function settlementsOf(nodes: readonly MapNode[]): Set<string> {
  //: What is read here is already worked out: the `"city"` layer is put on
  //: by the client itself (`geo.withCityScene`), on a surface node whose
  //: parent stands on the surface too. Repeating that rule here would be a
  //: second reading of one thing, and the two would part at the first edit.
  const out = new Set<string>();
  for (const node of nodes)
    if (node.layer === "city" && node.parent) out.add(node.parent);
  return out;
}

/**
 * The node's delegate among several layers: the nearest ancestor, itself
 * included, that is drawn in a scene showing these layers. On the surface
 * with the cities open that is the node itself or its city; with the cities
 * closed, the city alone -- and a wild node, which has no city, is its own.
 *
 * `settlements` is what a closed city is climbed to. Without it a member of
 * a city delegated to **itself** once the `"city"` layer went (D-319), so
 * with the cities closed the roads were drawn between nodes the scene was no
 * longer drawing -- and vanished.
 */
export function delegateAmong(
  byKey: Record<string, MapNode>,
  key: string,
  layers: readonly string[],
  settlements: ReadonlySet<string> = new Set(),
): string | null {
  const closed = !layers.includes("city");
  let cursor: MapNode | undefined = byKey[key];
  while (cursor) {
    if (layers.includes(cursor.layer)) {
      const up: MapNode | undefined = cursor.parent
        ? byKey[cursor.parent]
        : undefined;
      //: A closed city answers for its own: the climb goes on while the
      //: parent is a city. A planet never is one -- it is in the sky, not on
      //: the surface.
      if (closed && up && up.layer === "planet" && settlements.has(up.key)) {
        cursor = up;
        continue;
      }
      return cursor.key;
    }
    cursor = cursor.parent ? byKey[cursor.parent] : undefined;
  }
  return null;
}

/**
 * Which built-up area is the viewer's own -- the one the city tab opens.
 *
 * Normally it is the city above where they stand. Above the hull of a ship
 * there is no city at all: a ship hangs under its planet (D-201). A moored
 * crew therefore fell through to whatever city came first in the world by
 * name -- on a four-planet map, somebody's abandoned town on Aurora -- and
 * the gangway underfoot was not on the map at all: one could not walk off
 * one's own ship.
 *
 * The way ashore is the way back. A gangway is an exit like any other, so the
 * first exit that leads into a city names the city to open. "First" is enough
 * for what this is for: a ship has exactly one gangway. A node with exits into
 * two different cities -- wild ground between them -- gets the one the server
 * listed first, and either is as true as the other.
 *
 * Read off `exits`, which the client already has: no new key on the socket (D-225).
 */
export function homeCity(
  byKey: Record<string, MapNode>,
  here: string,
  exits: readonly { key: string }[],
): string | null {
  const above = byKey[delegate(byKey, here, "city") ?? ""]?.parent;
  if (above) return above;
  for (const path of exits) {
    const ashore = byKey[delegate(byKey, path.key, "city") ?? ""]?.parent;
    if (ashore) return ashore;
  }
  return null;
}

/**
 * Whether the node stands on another planet than the body: walked-to never,
 * flown-to only (D-201). Every node carries its planet, and a moored ship's
 * nodes are rewritten to the port's planet by the engine on arrival, so this
 * one comparison answers for places and hulls alike.
 */
export function offworld(
  byKey: Record<string, MapNode>,
  here: string,
  node: MapNode,
): boolean {
  const own = byKey[here]?.planet;
  return Boolean(own && node.planet && node.planet !== own);
}

/**
 * The piece of the graph within `depth` steps of where you stand.
 *
 * A plain breadth-first walk over the edges of this layer. Given no starting
 * node -- somebody else's city opened from the outside, a planet nobody of
 * yours is on -- there is no centre to measure from, and the whole group is
 * shown instead of nothing.
 */
export function nearby(
  from: string | null,
  keys: Iterable<string>,
  links: Link[],
  depth = DEPTH,
): Set<string> {
  const all = new Set(keys);
  if (from === null || !all.has(from)) return all;
  const near = new Map<string, string[]>();
  for (const link of links) {
    if (!all.has(link.a) || !all.has(link.b)) continue;
    near.set(link.a, [...(near.get(link.a) ?? []), link.b]);
    near.set(link.b, [...(near.get(link.b) ?? []), link.a]);
  }
  const seen = new Set([from]);
  let edge = [from];
  for (let step = 0; step < depth && edge.length; step++) {
    const next: string[] = [];
    for (const key of edge) {
      for (const other of near.get(key) ?? []) {
        if (seen.has(other)) continue;
        seen.add(other);
        next.push(other);
      }
    }
    edge = next;
  }
  return seen;
}
