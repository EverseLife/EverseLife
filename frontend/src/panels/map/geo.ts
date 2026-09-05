// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Degrees onto the flat map, until the globe (D-319, wave 3 of the plan).
 *
 * A surface node stands on its planet's sphere in degrees; the map still
 * draws a flat frame in map units. Until the globe replaces the frame, a scene
 * is flattened here: an equirectangular projection round the scene's own
 * reference -- the first placed node by key, so every viewer flattens the
 * same scene the same way -- scaled so that the scene fits the frame. The
 * scale is per scene, never per node: a city keeps its streets, a planet its
 * cities, and neither is the other's business.
 *
 * Floors and rooms carry flat places already and pass through untouched.
 */

import type { MapNode } from "../../api";
import { H, W, type Point } from "./model";

/** Map units a scene may span before its scale is brought down to fit. */
const SPAN = Math.max(W, H) * 1.6;
/** Map units per degree at the equator when nothing forces a smaller scale:
 *  about five units a metre, the flat map's own density at a city step. */
const UNITS_PER_DEGREE = 111_000 * 5;

function onSphere(place: MapNode["place"]): place is { lat: number; lon: number } {
  return place != null && "lat" in place;
}

/** The flat places of a scene: rooms as given, surface nodes projected. */
export function flatten(nodes: readonly MapNode[]): Map<string, Point> {
  const out = new Map<string, Point>();
  const sphere: { key: string; lat: number; lon: number }[] = [];
  for (const node of nodes) {
    if (!node.place) continue;
    if (onSphere(node.place)) sphere.push({ key: node.key, ...node.place });
    else out.set(node.key, { x: node.place.x, y: node.place.y });
  }
  if (sphere.length === 0) return out;
  sphere.sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
  const origin = sphere[0];
  const stretch = Math.cos((origin.lat * Math.PI) / 180);
  let east = 0;
  let north = 0;
  for (const node of sphere) {
    east = Math.max(east, Math.abs(node.lon - origin.lon) * stretch);
    north = Math.max(north, Math.abs(node.lat - origin.lat));
  }
  const reach = Math.max(east, north);
  const scale = reach > 0 ? Math.min(UNITS_PER_DEGREE, SPAN / reach) : UNITS_PER_DEGREE;
  for (const node of sphere) {
    out.set(node.key, {
      x: (node.lon - origin.lon) * stretch * scale,
      y: -(node.lat - origin.lat) * scale,
    });
  }
  return out;
}

/**
 * The city as a scene of its own, read off the parents (D-319).
 *
 * The server has one surface level: a plot and a vein are both `planet`, and
 * what says "inside a city" is that the node's parent is itself a surface
 * node -- the city's own. The map still draws a city as its own scene, so
 * those nodes are given the `city` scene here, on the client's copy, and the
 * rest of the panel never learns that the layer stopped being the server's.
 */
export function withCityScene(nodes: MapNode[]): MapNode[] {
  const surface = new Set(nodes.filter((node) => node.layer === "planet").map((node) => node.key));
  return nodes.map((node) =>
    node.layer === "planet" && node.parent && surface.has(node.parent)
      ? { ...node, layer: "city" }
      : node,
  );
}
