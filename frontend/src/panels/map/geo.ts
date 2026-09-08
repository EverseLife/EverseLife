// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Degrees onto the flat map where there is no globe to draw them on.
 *
 * The globe (`globe.ts`, D-319 wave 3) draws a surface scene whenever the
 * planet's radius is known. This is the fallback for a scene the book has
 * no radius for -- a planet the vault does not name -- flattened by an
 * equirectangular projection round the scene's own reference: the first
 * placed node by key, so every viewer flattens the same scene the same way,
 * scaled so that the scene fits the frame.
 *
 * Floors and rooms carry flat places already and pass through untouched,
 * globe or no globe: the inside window is flat by design (plan §2).
 */

import type { MapNode } from "../../api";
import { UNITS_PER_METRE } from "./globe";
import { H, W, type Point } from "./model";

/** Map units a scene may span before its scale is brought down to fit. */
const SPAN = Math.max(W, H) * 1.6;
/** Map units per degree at the equator when nothing forces a smaller scale:
 *  a degree of the meridian in metres at the globe's own density. */
const UNITS_PER_DEGREE = 111_000 * UNITS_PER_METRE;

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
/**
 * One row per key, the later winning.
 *
 * The map and `look` overlap on purpose: a hull is on the public map as a
 * point of the sky and arrives again from `look.ships` as a point of its
 * pier, and both rows are true of different scenes. Two rows of one key are
 * not: whatever counts nodes counts it twice -- a city grew a size for every
 * stranger's hull moored in it -- and whatever looks one up gets whichever
 * the spread happened to put last. The near sight is the later of the two and
 * the more particular, so it is the one that stands.
 */
export function oneEach(nodes: MapNode[]): MapNode[] {
  const held = new Map<string, MapNode>();
  for (const node of nodes) held.set(node.key, node);
  return [...held.values()];
}

export function withCityScene(nodes: MapNode[]): MapNode[] {
  const surface = new Set(nodes.filter((node) => node.layer === "planet").map((node) => node.key));
  return nodes.map((node) =>
    node.layer === "planet" && node.parent && surface.has(node.parent)
      ? { ...node, layer: "city" }
      : node,
  );
}
