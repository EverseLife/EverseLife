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
import { delegateAmong, settlementsOf, type LayerId, type Link } from "./model";

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
 */
export function visibleOf(
  nodes: readonly MapNode[],
  layers: readonly string[],
  locationBase: string,
  sphereShown: string | null,
  here = "",
): MapNode[] {
  const open = layers.includes("city");
  //: A city is a node with others hanging under it (`model.settlementsOf`).
  //: This used to read the `"city"` layer, which since D-319 means nothing:
  //: the world has three layers and no `"city"` node comes over the wire at
  //: all. The set came out empty always, and with it the map: with the cities
  //: closed it drew **no** city -- only the node underfoot -- and with them
  //: open it drew an extra abstract city node over the city's own streets.
  const settlements = settlementsOf(nodes);
  return nodes.filter((node) => {
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
    const settlement = settlements.has(node.key);
    return open ? !settlement : settlement;
  });
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

  /** The node's delegate in this scene: itself, its city, or its hull. */
  const reprScene = useMemo(
    () =>
      (key: string): string | null =>
        delegateAmong(
          byKey,
          key,
          layerKey.split("|"),
          settlementsOf(Object.values(byKey)),
        ),
    [byKey, layerKey],
  );

  const visible = useMemo(
    () =>
      visibleOf(
        map?.nodes ?? [],
        layerKey.split("|"),
        locationBase,
        sphereShown,
        here,
      ),
    [map, layerKey, locationBase, sphereShown, here],
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

  return {
    orbiting,
    inside,
    citiesOpen: open,
    currentLayer,
    reprScene,
    /** Where you stand, as this scene draws it. Null when you are not on it at all. */
    myRepr: reprScene(here),
    visible,
    shownEdges,
  };
}
