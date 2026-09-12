// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The lines of the relief on the globe (landscape plan wave 6): the
 * contours and the figures of the growth (`figures.ts`) -- read off the
 * rasters (`contours.ts`) and projected by the eye.
 *
 * The rivers left this layer 2026-09-11 and went into the shader: as a line
 * a river was a thread laid over the ground rather than water in it, and
 * the owner asked for the raster instead (the stream raster, cut at the
 * bank). The coast and the lakes' rims left the same evening: the shader's water
 * has its own edge, cut to the pixel, and a line along it said nothing the
 * colour did not (owner, 2026-09-11: what is the coastline for at all).
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

import { useBook } from "../../actions";
import { growthOf } from "./figures";
import { useTerrain } from "./Ground";
import { project, type Eye } from "./globe";
import {
  closeFrame,
  figureLines,
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
  frameM,
  show = { contours: true, figures: true },
}: {
  planet: string;
  eye: Eye;
  radius: number;
  /** Half the frame's width in map units; undefined from the planet frame. */
  within: number | undefined;
  /** The frame's own width, metres, stepped by half-octaves
   *  (`bands.nearFrameM`): the figures are cut to it, not to the window. */
  frameM?: number;
  /** Which of the layer's lines are on (D-331): the contours, the growth. */
  show?: { contours: boolean; figures: boolean };
}) {
  const rasters = useRasters(planet);
  const passport = useTerrain(planet)?.raster ?? null;
  //: The figure and the shares of each biome, off the book of constants
  //: (D-225: the client derives what it can).
  const book = useBook();
  const growth = useMemo(() => growthOf(book?.constants, passport), [book, passport]);
  //: Whether this frame has lines at all.
  const near = closeFrame(frameMetres(within));
  const { lat, lon } = quantisedEye(eye, radius, within);
  const frame = useMemo(
    () =>
      near && rasters && passport
        ? frameLines(rasters, passport, { lat, lon }, radius, within, undefined, {
            contours: show.contours,
            figures: false,
            growth,
          })
        : null,
    [near, rasters, passport, lat, lon, radius, within, show.contours, growth],
  );
  //: The figures on a memo of their own, cut to the frame: their eye is
  //: quantised to the frame's own window, finer than the contours', and
  //: a step of the zoom (`frameM`) recuts them without recutting the
  //: contours.
  const figureEye = quantisedEye(eye, radius, frameM === undefined ? within : (frameM * UNITS_PER_METRE) / 2);
  const grown = useMemo(
    () =>
      near && rasters && passport && show.figures && growth && frameM !== undefined
        ? figureLines(rasters, passport, figureEye, radius, frameM, undefined, growth)
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the eye by its two numbers, not the object
    [near, rasters, passport, figureEye.lat, figureEye.lon, radius, frameM, show.figures, growth],
  );
  const drawn = useMemo(() => {
    if (!frame) return null;
    const drawnOf = (segments: readonly Segment[]) => pathOf(segments, eye, radius);
    return {
      contours: drawnOf(frame.contours.filter((c) => !c.index).flatMap((c) => c.segments)),
      index: drawnOf(frame.contours.filter((c) => c.index).flatMap((c) => c.segments)),
      figures: Object.entries(grown ?? {}).map(([name, segments]) => [name, drawnOf(segments ?? [])] as const),
    };
  }, [frame, grown, eye, radius]);
  if (!drawn) return null;
  return (
    <g
      className="relief-lines"
      style={{ "--pc": `var(--planet-${planet})` } as React.CSSProperties}
      aria-hidden="true"
    >
      {drawn.contours && <path className="contour" d={drawn.contours} />}
      {drawn.index && <path className="contour index" d={drawn.index} />}
      {/* The growth under the lines of the relief and over the ground's own
          colour: a wood is a thing of the country, a contour is a reading of
          it, and a reading is written on top. One path a figure, so the
          stylesheet gives each its colour (D-331). */}
      {drawn.figures.map(([name, d]) => d && <path key={name} className={`figure ${name}`} d={d} />)}
    </g>
  );
}
