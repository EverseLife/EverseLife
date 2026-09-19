// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What a node of the scene does under the hand: which exit a step towards it
 * takes, how large a closed city is drawn, and whether a step leads to it at
 * all -- the map's judgement, drawn by `Nodes` and spoken by the inspector and
 * the menu. Out of `GraphMap.tsx`, which is past the eight-hundred-line bar
 * already; pure, so what it promises can be pinned.
 */

import { useMemo } from "react";

import type { Exit, MapNode, Transit, WorldMap } from "../../api";
import { offworld } from "./model";

/** A step towards a drawn node: the exit it takes and how long it is. */
export type Step = { key: string; seconds: number };

/**
 * The step towards each node of the scene, by the node that stands for the
 * exit's far end: the quickest where several exits land on one delegate --
 * two streets into one closed city -- and none towards where one stands.
 */
export function walkTargetsOf(
  exits: readonly Exit[],
  reprScene: (key: string) => string | null,
  here: string,
): Record<string, Step> {
  const out: Record<string, Step> = {};
  for (const exit of exits) {
    const p = reprScene(exit.key);
    if (!p || p === reprScene(here)) continue;
    const known = out[p];
    if (!known || exit.seconds < known.seconds) {
      out[p] = { key: exit.key, seconds: exit.seconds };
    }
  }
  return out;
}

/** How many nodes hang under each: a closed city is drawn as large as it
 *  is, so a town and the capital are told apart from afar. */
export function sizesOf(nodes: readonly MapNode[]): Map<string, number> {
  const out = new Map<string, number>();
  for (const node of nodes) {
    if (node.parent) out.set(node.parent, (out.get(node.parent) ?? 0) + 1);
  }
  return out;
}

/**
 * Whether a step leads to the node -- the map's judgement, drawn by `Nodes`.
 *
 * To a planet one does not walk at all: it is reached by ship from a
 * spaceport (D-201) -- a step across the void is not a road the map may draw.
 * A button the server will refuse anyway is a promise the interface may not
 * make.
 */
export function reachableFrom({
  ongoing,
  standingAt,
  byKey,
  here,
  groups,
  walkTargets,
}: {
  ongoing: Transit | null;
  /** The node that wears the player (`useWalker`). */
  standingAt: string | null;
  byKey: Record<string, MapNode>;
  here: string;
  groups: ReadonlySet<string>;
  walkTargets: Record<string, Step>;
}): (node: MapNode) => boolean {
  return (node) =>
    !ongoing &&
    node.key !== standingAt &&
    !node.orbit &&
    //: Another planet's surface is looked at, not walked to (D-201): its nodes
    //: must not light up as reachable.
    !offworld(byKey, here, node) &&
    (groups.has(node.key) ? Boolean(walkTargets[node.key]) : true);
}

export function useNodeBehaviour({
  map,
  byKey,
  exits,
  reprScene,
  here,
  ongoing,
  standingAt,
  groups,
}: {
  map: WorldMap | null;
  byKey: Record<string, MapNode>;
  exits: readonly Exit[] | undefined;
  /** The node's delegate in the scene shown (`useScene`). */
  reprScene: (key: string) => string | null;
  /** Where the body stands. */
  here: string;
  ongoing: Transit | null;
  standingAt: string | null;
  /** The nodes that open into a layer of their own. */
  groups: ReadonlySet<string>;
}) {
  const walkTargets = useMemo(
    () => walkTargetsOf(exits ?? [], reprScene, here),
    [exits, reprScene, here],
  );
  const sizes = useMemo(() => sizesOf(map?.nodes ?? []), [map]);
  return {
    walkTargets,
    sizes,
    reachable: reachableFrom({
      ongoing,
      standingAt,
      byKey,
      here,
      groups,
      walkTargets,
    }),
  };
}
