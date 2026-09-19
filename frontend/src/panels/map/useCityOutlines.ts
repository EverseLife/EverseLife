// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The outlines of the shown planet's cities, in degrees: once per map,
 * projected as the globe turns (D-323 addendum, D-356).
 *
 * The shown planet's cities and no others. The public map carries every
 * planet's surface, and an outline is projected by the eye and radius of the
 * one under it -- so Aurora's towns, drawn by Terra's globe, landed as blots
 * on Terra. The field's numbers are the vault's, off the book's constants:
 * without them nothing is drawn rather than a line the engine does not keep.
 */

import { useMemo } from "react";
import type { WorldMap } from "../../api";
import type { Geo } from "./globe";
import { UNITS_PER_METRE } from "./globe";
import { cityOutlines, outlineLaw } from "./territory";

export function useCityOutlines(
  map: WorldMap | null,
  radius: number | null | undefined,
  planet: string | null,
  constants: Record<string, unknown> | undefined,
): Map<string, Geo[][]> {
  const law = useMemo(() => outlineLaw(constants), [constants]);
  return useMemo(
    () =>
      radius && planet && law
        ? cityOutlines(
            (map?.nodes ?? []).filter((node) => node.planet === planet),
            radius / UNITS_PER_METRE,
            law,
            map?.edges ?? [],
          )
        : new Map(),
    [map, radius, planet, law],
  );
}
