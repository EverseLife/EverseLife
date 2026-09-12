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
const ON_BY_DEFAULT = new Set<string>(["provinces", "figures", "clouds"]);

export function useLayers(): {
  layer: Layer;
  setLayer: (layer: Layer) => void;
  /** The overlays as switched, whatever the layer: what the menu shows. */
  overlays: Overlays;
  setOverlays: (next: Overlays) => void;
  /** Whether the ground is the terrain, the one layer the overlays dress. */
  onGround: boolean;
  /** The overlays as drawn (D-336): on the terrain as switched, on a legend
   *  layer none of them -- a cloud's shadow or a province's line is a mark
   *  the legend never made -- but the relief keeps its own contours. */
  shown: Overlays;
} {
  const [layer, setLayer] = useKept<Layer>(GROUND, "terrain", oneOf(LAYERS));
  const [on, setOn] = useKept<Set<string>>(OVERLAYS, ON_BY_DEFAULT, KEYS);
  const overlays: Overlays = {
    provinces: on.has("provinces"),
    cities: on.has("cities"),
    contours: on.has("contours"),
    figures: on.has("figures"),
    clouds: on.has("clouds"),
  };
  const setOverlays = (next: Overlays) =>
    setOn(new Set(OVERLAY_NAMES.filter((name) => next[name])));
  const onGround = layer === "terrain";
  const shown: Overlays = {
    provinces: overlays.provinces && onGround,
    cities: overlays.cities && onGround,
    contours: (overlays.contours && onGround) || layer === "relief",
    figures: overlays.figures && onGround,
    clouds: overlays.clouds && onGround,
  };
  return { layer, setLayer, overlays, setOverlays, onGround, shown };
}
