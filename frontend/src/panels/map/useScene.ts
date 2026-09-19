// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The scene a band draws (D-319, wave 4): which nodes, which edges, and who
 * stands for whom.
 *
 * The sky draws the space layer. The surface draws every node of one
 * planet's surface -- and, once the eye is near enough, the nodes of its
 * cities too; farther out a city is its own node, a point with a name. The
 * inside draws the floors or rooms of where one stands. An edge of the world
 * is drawn between the delegates of its ends in this scene: a road from a
 * city gate to a field joins, on a closed city, the city and the field.
 *
 * What is drawn is what the server gave (wave 2): the map used to window
 * itself two steps round the body (D-240); the server now answers with
 * sight, memory and the public, and windowing it again would hide the very
 * cities it sends so that a newcomer finds the door.
 */

import { useMemo } from "react";

import type { MapNode, WorldMap } from "../../api";
import type { Band } from "./bands";
import {
  delegateAmong,
  settlementsOf,
  type LayerId,
  type Link,
  type Point,
} from "./model";

/**
 * Where a house's ground floor stands on its floors' plan: the origin, the
 * seat the engine keeps free for it (`places._flat_neighbourhood`).
 */
export const GROUND_FLOOR_AT: Point = { x: 0, y: 0 };

/**
 * The ground floor of the inside shown, if the inside is a house's (D-247).
 *
 * The first floor of a house is the plot itself -- the door, the yard, the way
 * in -- and only the floors above it are nodes of the `location` layer. Drawn
 * by the layer alone, the inside lost it the moment one went upstairs: from
 * the second floor the first was not on the map at all, and the stair down
 * led nowhere one could pick (owner, 2026-09-19). A hull's rooms hang under
 * the ship, which is no floor one walks to: its inside has none. Asked by
 * `aboard`, not by the layer -- a hull moored at a pier comes to its crew
 * wearing the pier's layer (`ship.view.sight`), and read as a ground floor it
 * would be drawn at the origin, over the first room, which stands there.
 */
export function groundFloorOf(
  byKey: Record<string, MapNode>,
  band: Band,
  base: string,
): string | null {
  if (band !== "inside") return null;
  const node = byKey[base];
  if (!node || node.aboard) return null;
  return node.layer === "planet" || node.layer === "city" ? base : null;
}

/**
 * The nodes that open into a layer of their own: those others hang under.
 *
 * Not the ground floor of the inside shown: one is already in it. As a group
 * it was offered only for a step straight into it
 * (`useNodeBehaviour.reachableFrom`), so from the third floor up the plot
 * stood on the map with no way down to it -- the stair from the second floor
 * is not an exit of the third.
 */
export function groupsOf(
  nodes: readonly MapNode[],
  ground: string | null,
): Set<string> {
  const out = new Set<string>();
  for (const node of nodes) if (node.parent) out.add(node.parent);
  if (ground !== null) out.delete(ground);
  return out;
}

/**
 * The nodes a scene draws: those of its layers, an inside only its own
 * base's, a surface only the shown planet's, the sky everybody's -- except
 * a hull that is not under way: a ship at its parking or a pier is not a
 * point of the map (D-319 item 10), it is reached through the port's
 * shipyard window.
 *
 * On the surface every node is the planet's and a city is the node others
 * hang under (`parent`; the client's `city` scene is that reading). With
 * the cities open the members stand for the city and the city's own point
 * is not drawn -- an abstract node among the streets was a thing to walk
 * to that was nowhere. With the cities closed a city is one point and the
 * wild ground between cities -- a mine, a floodplain, a field -- is not
 * drawn at all, so that from afar the map is the cities (owner,
 * 2026-09-06); only the node under one's own feet is always there.
 *
 * With them open the members stand for the city, and a living city's own row
 * stands among them: since D-330 it is the node its bioprinter is on. Only a
 * group that is **not** a city is left out there -- the Forerunners' ruins,
 * whose node is still an empty mark over its own rooms.
 *
 * A house's inside draws its ground floor too (`groundFloorOf`), at the
 * origin of the plan: on the sphere it stands in degrees, and on the plan it
 * has no place of its own but the one the engine keeps for it.
 */
export function visibleOf(
  nodes: readonly MapNode[],
  layers: readonly string[],
  locationBase: string,
  sphereShown: string | null,
  here = "",
  ground: string | null = null,
): MapNode[] {
  const open = layers.includes("city");
  //: A city is a node with others hanging under it (`model.settlementsOf`).
  //: This used to read the `"city"` layer, which since D-319 means nothing:
  //: the world has three layers and no `"city"` node comes over the wire at
  //: all. The set came out empty always, and with it the map: with the cities
  //: closed it drew **no** city -- only the node underfoot -- and with them
  //: open it drew an extra abstract city node over the city's own streets.
  const settlements = settlementsOf(nodes);
  const shown = nodes.filter((node) => {
    if (node.key === ground) return true;
    //: The node underfoot always, and **before** the layer. With the cities
    //: closed only `planet` is drawn, and a member of a city wears the `city`
    //: layer (`geo.withCityScene`), so somebody standing in a city was
    //: filtered out before "this is me" was ever asked: the map from afar
    //: stayed empty and one lost oneself on it.
    if (node.key === here) return true;
    if (!layers.includes(node.layer)) return false;
    if (node.layer === "location") return node.parent === locationBase;
    if (node.layer === "space") return !(node.aboard && !node.flight);
    if (sphereShown && node.planet !== sphereShown) return false;
    if (node.layer !== "planet") return true;
    //: With the cities open a **living** city's own row is drawn among its
    //: streets: since D-330 that row is a place -- the node the bioprinter
    //: stands on -- and hiding it left the middle of the city empty, with
    //: roads running into a gap. A group that is not a city stays hidden, as
    //: every group used to be: the Forerunners' dead cities are still an
    //: empty node with their rooms under it, and the first of those rooms is
    //: pinned to the group's own point -- drawn together they would stand one
    //: on top of the other. Closed, only the groups are drawn: the wild
    //: ground between them says nothing from up there.
    const settlement = settlements.has(node.key);
    return open ? !settlement || Boolean(node.city) : settlement;
  });
  return ground === null
    ? shown
    : shown.map((node) =>
        node.key === ground ? { ...node, place: GROUND_FLOOR_AT } : node,
      );
}

/**
 * Whether the scene draws the cities open, as their own nodes -- on the
 * surface, once the eye is near enough (`near`).
 *
 * Inside a building no city is drawn at all, and the answer there is yes:
 * nothing inside is a closed city. Read as closed, a house's ground floor --
 * a group, its floors hang under it -- wore a closed city's halo, and the
 * floor underfoot the mark a closed map gives the node one stands in, sized
 * in pixels for the globe and several floors wide on the plan, over the floor
 * below it (owner, 2026-09-19). The sky keeps its planets closed: a click
 * opens their surface.
 */
export function citiesDrawnOpen(band: Band, near: boolean): boolean {
  return band === "inside" || (band === "surface" && near);
}

/**
 * The node's delegate in a scene showing these layers: itself, its city, or
 * its hull -- and a house's ground floor stands for itself in its inside
 * (`groundFloorOf`). A node of the surface climbs to no `location` node, so
 * without that the stair down joined nothing drawn, and the body on the
 * ground floor wore no mark.
 */
export function delegateIn(
  byKey: Record<string, MapNode>,
  layers: readonly string[],
  ground: string | null,
): (key: string) => string | null {
  const settlements = settlementsOf(Object.values(byKey));
  return (key) =>
    key === ground ? ground : delegateAmong(byKey, key, layers, settlements);
}

/** The edges a scene draws, between the delegates of their ends: the
 *  shortest of several between two delegates, because two nodes joined
 *  twice are drawn once; nothing where an end stands for nothing shown, or
 *  both ends for the same thing. */
export function edgesOf(
  edges: readonly Link[],
  shown: ReadonlySet<string>,
  repr: (key: string) => string | null,
): Link[] {
  const seen = new Map<string, Link>();
  for (const edge of edges) {
    const pa = repr(edge.a);
    const pb = repr(edge.b);
    if (!pa || !pb || pa === pb) continue;
    if (!shown.has(pa) || !shown.has(pb)) continue;
    const id = [pa, pb].sort().join("|");
    const known = seen.get(id);
    if (!known || edge.seconds < known.seconds) {
      seen.set(id, {
        a: pa,
        b: pb,
        surface: edge.surface,
        seconds: edge.seconds,
      });
    }
  }
  return [...seen.values()];
}

export function useScene({
  map,
  byKey,
  band,
  citiesOpen,
  locationBase,
  sphereShown,
  here,
}: {
  map: WorldMap | null;
  byKey: Record<string, MapNode>;
  band: Band;
  /** Whether the eye is near enough for cities to open into their nodes. */
  citiesOpen: boolean;
  /** The node whose inside is shown: the one stood in, or its house. */
  locationBase: string;
  /** Whose surface is shown. */
  sphereShown: string | null;
  /** Where the body stands. */
  here: string;
}) {
  const orbiting = band === "sky";
  const inside = band === "inside";
  const open = !orbiting && !inside && citiesOpen;
  const shownLayers: readonly LayerId[] = orbiting
    ? ["space"]
    : inside
      ? ["location"]
      : open
        ? ["city", "planet"]
        : ["planet"];
  const layerKey = shownLayers.join("|");
  //: The scene's word where the old code asked for a layer: the sky, the
  //: inside, or the surface with or without its cities.
  const currentLayer: LayerId = orbiting
    ? "space"
    : inside
      ? "location"
      : open
        ? "city"
        : "planet";

  const ground = groundFloorOf(byKey, band, locationBase);

  /** The node's delegate in this scene: itself, its city, or its hull. */
  const reprScene = useMemo(
    () => delegateIn(byKey, layerKey.split("|"), ground),
    [byKey, layerKey, ground],
  );

  const visible = useMemo(
    () =>
      visibleOf(
        map?.nodes ?? [],
        layerKey.split("|"),
        locationBase,
        sphereShown,
        here,
        ground,
      ),
    [map, layerKey, locationBase, sphereShown, here, ground],
  );

  const shownEdges = useMemo(
    () =>
      edgesOf(
        map?.edges ?? [],
        new Set(visible.map((node) => node.key)),
        reprScene,
      ),
    [map, visible, reprScene],
  );

  const groups = useMemo(
    () => groupsOf(map?.nodes ?? [], ground),
    [map, ground],
  );

  return {
    orbiting,
    inside,
    citiesOpen: citiesDrawnOpen(band, citiesOpen),
    currentLayer,
    reprScene,
    /** Where you stand, as this scene draws it. Null when you are not on it at all. */
    myRepr: reprScene(here),
    visible,
    shownEdges,
    /** The nodes that open into a layer of their own (`groupsOf`). */
    groups,
  };
}
