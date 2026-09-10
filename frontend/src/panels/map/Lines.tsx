// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The lines of the relief on the globe (landscape plan wave 6): contours,
 * the coast by the form of its land, the hachures of the cliffs and the
 * rivers -- read off the rasters (`contours.ts`) and projected by the eye.
 *
 * The rivers left this layer 2026-09-11 and went into the shader: as a line
 * a river was a thread laid over the ground rather than water in it, and
 * the owner asked for the raster instead. `contours.rivers` is still cut --
 * the near frames measure a river's width by it -- but nothing draws it.
 * Drawn over the ground and under the nodes, thin at any zoom.
 *
 * Nothing at all from a frame wider than the city's (`closeFrame`): there
 * the ground is the shader's, and lines cut from cells of fifty
 * metres would web the region over (owner, 2026-09-09).
 *
 * Two memos, two costs: every line is cut on one mesh of ground about the
 * eye, a cell of the grid to the step, and cut again only when the eye
 * leaves the window it was cut for (`quantisedEye`) or the frame changes
 * width; the projection runs on every eye, because the frame's origin is
 * the eye. The coast and the rivers used to be cut for the **planet**
 * instead and kept in bins -- one walk over three million cells,
 * three seconds of it on the loop before a single line appeared, for a
 * shore that is only ever drawn from eleven kilometres in. A near
 * frame's mesh is tens of thousands of samples; there is nothing to wait
 * for.
 */

import { useMemo } from "react";

import { useTerrain } from "./Ground";
import { project, type Eye } from "./globe";
import {
  closeFrame,
  frameLines,
  frameMetres,
  quantisedEye,
  type Segment,
} from "./contours";
import { UNITS_PER_METRE } from "./globe";
import { useRasters } from "./rasters";

/** Segments to one path, dropping what faces away from the eye. */
function pathOf(segments: readonly Segment[], eye: Eye, radius: number): string {
  const parts: string[] = [];
  for (const [a, b] of segments) {
    const p = project(eye, radius, a);
    const q = project(eye, radius, b);
    if (!p.front || !q.front) continue;
    parts.push(`M${p.x.toFixed(1)} ${p.y.toFixed(1)}L${q.x.toFixed(1)} ${q.y.toFixed(1)}`);
  }
  return parts.join("");
}

export function Lines({
  planet,
  eye,
  radius,
  within,
}: {
  planet: string;
  eye: Eye;
  radius: number;
  /** Half the frame's width in map units; undefined from the planet frame. */
  within: number | undefined;
}) {
  const rasters = useRasters(planet);
  const passport = useTerrain(planet)?.raster ?? null;
  //: Whether this frame has lines at all.
  const near = closeFrame(frameMetres(within));
  const { lat, lon } = quantisedEye(eye, radius, within);
  const frame = useMemo(
    () =>
      near && rasters && passport
        ? frameLines(rasters, passport, { lat, lon }, radius, within)
        : null,
    [near, rasters, passport, lat, lon, radius, within],
  );
  const drawn = useMemo(() => {
    if (!frame) return null;
    const drawnOf = (segments: readonly Segment[]) => pathOf(segments, eye, radius);
    return {
      contours: drawnOf(frame.contours.filter((c) => !c.index).flatMap((c) => c.segments)),
      index: drawnOf(frame.contours.filter((c) => c.index).flatMap((c) => c.segments)),
      hachures: drawnOf(frame.hachures),
      rock: drawnOf(frame.shores.rock),
      beach: drawnOf(frame.shores.beach),
      shore: drawnOf(frame.shores.shore),
      lakes: drawnOf(frame.lakes),
    };
  }, [frame, eye, radius]);
  if (!drawn) return null;
  return (
    <g
      className="relief-lines"
      style={{ "--pc": `var(--planet-${planet})` } as React.CSSProperties}
      aria-hidden="true"
    >
      {drawn.contours && <path className="contour" d={drawn.contours} />}
      {drawn.index && <path className="contour index" d={drawn.index} />}
      {drawn.lakes && <path className="coast lake" d={drawn.lakes} />}
      {drawn.shore && <path className="coast shore" d={drawn.shore} />}
      {drawn.beach && <path className="coast beach" d={drawn.beach} />}
      {drawn.rock && <path className="coast rock" d={drawn.rock} />}
      {drawn.hachures && <path className="hachure" d={drawn.hachures} />}
    </g>
  );
}
