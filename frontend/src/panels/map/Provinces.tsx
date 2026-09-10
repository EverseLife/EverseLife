// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The provinces on the globe (landscape plan §9.7: the planet frame shows
 * "подписи провинций" -- the debt wave 6 left and wave 8 pays): the
 * boundary between two named lands, and the name written in the middle of
 * what it encloses.
 *
 * Drawn on the far frames alone, and given up exactly where the relief's
 * lines begin (`closeFrame`). One ladder in two halves: far out the map
 * answers "what land is this", close in it answers "what is the ground
 * here", and neither crowds the other's frame. Close in the province is
 * not lost -- the inspector names it under every find (wave 3).
 *
 * The boundary is read off the province raster once per planet and kept
 * for the page, as the coast is; the projection runs per eye over the bins
 * under the frame, so a frame pays for what is under it.
 */

import { useMemo } from "react";

import { useNames } from "../../actions";
import { provinceLabelEm } from "./bands";
import { useTerrain } from "./Ground";
import { PROVINCE_EM } from "./labels";
import { project, type Eye } from "./globe";
import {
  closeFrame,
  frameMetres,
  provinceFrame,
  provinceMarksOf,
  quantisedEye,
  type ProvinceMark,
} from "./contours";
import { useRasters } from "./rasters";
import { provinceWord } from "./words";

/** Where a planet's names are written, read once for the life of the page:
 *  the mean of each province's ground, which no frame changes. The
 *  boundaries are not kept -- they are cut for the frame's own window and
 *  at its own stride, so the planet's disk is walked two hundred samples
 *  across and the region's at its cells. */
const MARKS = new Map<string, ProvinceMark[]>();

export function Provinces({
  planet,
  eye,
  radius,
  within,
  far,
}: {
  planet: string;
  eye: Eye;
  radius: number;
  /** Half the frame's width in map units; undefined from the planet frame. */
  within: number | undefined;
  /** How far out the frame stands, in half-octaves: the names are drawn at
   *  a size in pixels, and the map's units are pixels only at scale one. */
  far: number;
}) {
  const rasters = useRasters(planet);
  const passport = useTerrain(planet)?.raster ?? null;
  const names = useNames();
  //: Far frames only: the near ones belong to the relief's lines.
  const wide = !closeFrame(frameMetres(within));
  const marks = useMemo(() => {
    if (!wide || !rasters || !passport) return null;
    let held = MARKS.get(planet);
    if (!held) MARKS.set(planet, (held = provinceMarksOf(rasters, passport)));
    return held;
  }, [wide, planet, rasters, passport]);
  const { lat, lon } = quantisedEye(eye, radius, within);
  const edges = useMemo(
    () =>
      wide && rasters && passport
        ? provinceFrame(rasters, passport, { lat, lon }, radius, within)
        : null,
    [wide, rasters, passport, lat, lon, radius, within],
  );
  const drawn = useMemo(() => {
    if (!marks || !edges || !passport) return null;
    const parts: string[] = [];
    for (const [a, b] of edges) {
      const p = project(eye, radius, a);
      const q = project(eye, radius, b);
      if (!p.front || !q.front) continue;
      parts.push(`M${p.x.toFixed(1)} ${p.y.toFixed(1)}L${q.x.toFixed(1)} ${q.y.toFixed(1)}`);
    }
    const labels = marks.flatMap((mark) => {
      const id = passport.provinces?.[mark.code - 1];
      const word = id ? provinceWord({ province: id }, names) : null;
      const at = project(eye, radius, mark.at);
      //: A name on the far side of the globe would be written backwards
      //: over the near side, so only the near half is named.
      return word && at.front ? [{ id, word, x: at.x, y: at.y }] : [];
    });
    return { edges: parts.join(""), labels };
  }, [marks, edges, passport, names, eye, radius]);
  //: The name is set in map units by the stylesheet and grown to the pixels
  //: it wants, as a closed city's name is (`Nodes`): a `font-size` of many
  //: thousands of units is one the browser draws no glyphs for at all.
  const grown = provinceLabelEm(far) / PROVINCE_EM;
  if (!drawn || (!drawn.edges && drawn.labels.length === 0)) return null;
  return (
    <g className="province-lines" aria-hidden="true">
      {drawn.edges && <path className="province" d={drawn.edges} />}
      {drawn.labels.map((label) => (
        <g key={label.id} transform={`translate(${label.x} ${label.y})`}>
          <text className="province-label" transform={`scale(${grown})`}>
            {label.word}
          </text>
        </g>
      ))}
    </g>
  );
}
