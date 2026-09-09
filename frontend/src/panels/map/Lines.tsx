// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The lines of the relief on the globe (landscape plan wave 6): contours,
 * the coast by the form of its land, the hachures of the cliffs and the
 * rivers -- read off the rasters (`contours.ts`) and projected by the eye.
 * Drawn over the ground and under the nodes, thin at any zoom.
 *
 * Three memos, three costs: the planet's own lines -- the coast, the lakes,
 * the rivers -- are read once per planet and kept in bins; the frame's
 * lines -- contours, hachures -- are read again only when the eye leaves
 * the window they were read for (`quantisedEye`) or the frame changes
 * width; the projection to the frame runs on every eye, over the bins
 * under the frame, because the frame's origin is the eye.
 */

import { useMemo } from "react";

import { useTerrain } from "./Ground";
import { project, type Eye } from "./globe";
import {
  closeFrame,
  frameLines,
  frameMetres,
  planetLines,
  quantisedEye,
  underFrame,
  type Bins,
  type Segment,
} from "./contours";
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
  const own = useMemo(
    () => (rasters && passport ? planetLines(rasters, passport) : null),
    [rasters, passport],
  );
  const { lat, lon } = quantisedEye(eye, radius, within);
  const frame = useMemo(
    () => (rasters && passport ? frameLines(rasters, passport, { lat, lon }, radius, within) : null),
    [rasters, passport, lat, lon, radius, within],
  );
  const drawn = useMemo(() => {
    if (!own || !frame) return null;
    const under = (bins: Bins) => pathOf(underFrame(bins, eye, radius, within), eye, radius);
    //: The rivers are a near frame's line: from afar the cells of the water
    //: raster read as a web over the land rather than as threads.
    const near = closeFrame(frameMetres(within));
    return {
      contours: pathOf(frame.contours.filter((c) => !c.index).flatMap((c) => c.segments), eye, radius),
      index: pathOf(frame.contours.filter((c) => c.index).flatMap((c) => c.segments), eye, radius),
      hachures: pathOf(frame.hachures, eye, radius),
      rock: under(own.shores.rock),
      beach: under(own.shores.beach),
      shore: under(own.shores.shore),
      lakes: under(own.lakes),
      rivers: near ? under(own.rivers) : "",
    };
  }, [own, frame, eye, radius, within]);
  if (!drawn) return null;
  return (
    <g
      className="relief-lines"
      style={{ "--pc": `var(--planet-${planet})` } as React.CSSProperties}
      aria-hidden="true"
    >
      {drawn.contours && <path className="contour" d={drawn.contours} />}
      {drawn.index && <path className="contour index" d={drawn.index} />}
      {drawn.rivers && <path className="river" d={drawn.rivers} />}
      {drawn.lakes && <path className="coast lake" d={drawn.lakes} />}
      {drawn.shore && <path className="coast shore" d={drawn.shore} />}
      {drawn.beach && <path className="coast beach" d={drawn.beach} />}
      {drawn.rock && <path className="coast rock" d={drawn.rock} />}
      {drawn.hachures && <path className="hachure" d={drawn.hachures} />}
    </g>
  );
}
