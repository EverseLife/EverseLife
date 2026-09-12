// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What the map's ground is coloured by and what lies over it (D-331), kept
 * across sessions in the browser: one word for the ground and one set of
 * the overlays that are on. Out of `GraphMap.tsx`, which is past the
 * eight-hundred-line bar already.
 */

import { KEYS, oneOf, useKept } from "../../kept";
import { LAYERS, type Layer } from "./shade";
import { OVERLAY_NAMES, type Overlays } from "./Switcher";

/** The ground's colouring. `ground`, not `layer`: the map has a layer
 *  already -- the band one looks from -- and `kept.ts` says why that one is
 *  never stored. */
const GROUND = "everselife.map.ground";
/** The overlays that are on, as a set of their names. */
const OVERLAYS = "everselife.map.overlays";
/** What a fresh map shows: the provinces (the far frames' names, as they
 *  always were) and the growth; the city lands and the contours off (owner,
 *  2026-09-12) -- the relief layer draws its contours whatever this says. */
const ON_BY_DEFAULT = new Set<string>(["provinces", "figures"]);

export function useLayers(): {
  layer: Layer;
  setLayer: (layer: Layer) => void;
  overlays: Overlays;
  setOverlays: (next: Overlays) => void;
} {
  const [layer, setLayer] = useKept<Layer>(GROUND, "terrain", oneOf(LAYERS));
  const [on, setOn] = useKept<Set<string>>(OVERLAYS, ON_BY_DEFAULT, KEYS);
  const overlays: Overlays = {
    provinces: on.has("provinces"),
    cities: on.has("cities"),
    contours: on.has("contours"),
    figures: on.has("figures"),
  };
  const setOverlays = (next: Overlays) =>
    setOn(new Set(OVERLAY_NAMES.filter((name) => next[name])));
  return { layer, setLayer, overlays, setOverlays };
}
