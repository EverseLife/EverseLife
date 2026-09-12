// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The provinces on the globe (landscape plan §9.7: the planet frame shows
 * "подписи провинций" -- the debt wave 6 left and wave 8 pays): the
 * boundary between two named lands, and the name written in the middle of
 * what it encloses.
 *
 * Drawn on every frame while the provinces overlay is on, and on none
 * when it is off (D-331 addendum). They used to be the far frames' alone,
 * given up where the relief's lines begin (`closeFrame`), and the overlay
 * only added them to the near frames -- so on the frames a player looks
 * at most the switch did nothing visible (owner, 2026-09-12: the button
 * switches nothing). On by default: the far frames answer "what land is
 * this" as they always have; off, the map is the ground alone, and the
 * inspector still names the province under every find (wave 3).
 *
 * Where each name is written is read once per planet -- a mean over the
 * ground, which no frame changes -- and so are the boundaries **as the
 * planet's own disk shows them**: that disk has no bounded frame, every eye
 * sees the same whole, and a walk with a fixed phase is the only one whose
 * lines do not crawl a sample sideways after every step of the eye. A
 * bounded frame cuts its own boundaries instead, on a mesh of ground at the
 * frame's own step, and cuts them again when the eye leaves the window they
 * were cut for.
 */

import { useMemo } from "react";

import { useNames } from "../../actions";
import { limbShare, provinceLabelEm } from "./bands";
import { useTerrain } from "./Ground";
import { PROVINCE_EM } from "./labels";
import { placeAt, project, type Eye } from "./globe";
import {
  provinceFrame,
  provinceWhole,
  quantisedEye,
  type ProvinceMark,
  type Segment,
} from "./contours";
import { useRasters } from "./rasters";
import { provinceWord } from "./words";

/** What the planet's own disk shows of its provinces, read once for the
 *  life of the page: where each name is written -- a mean over the ground,
 *  which no frame changes -- and the boundaries at that disk's own
 *  coarseness. A bounded frame cuts its own boundaries instead, on a mesh
 *  of ground at the frame's step (`provinceFrame`). */
const WHOLE = new Map<string, { marks: ProvinceMark[]; edges: Segment[] }>();

export function Provinces({
  planet,
  eye,
  radius,
  within,
  far,
  on = true,
}: {
  planet: string;
  eye: Eye;
  radius: number;
  /** Half the frame's width in map units; undefined from the planet frame. */
  within: number | undefined;
  /** Whether the provinces overlay is on (D-331): boundaries and names on
   *  every frame, or nothing at all. */
  on?: boolean;
  /** How far out the frame stands, in half-octaves: the names are drawn at
   *  a size in pixels, and the map's units are pixels only at scale one. */
  far: number;
}) {
  const rasters = useRasters(planet);
  const passport = useTerrain(planet)?.raster ?? null;
  const names = useNames();
  //: The overlay's switch, and nothing else: on, every frame; off, none.
  const wide = on;
  const whole = useMemo(() => {
    if (!wide || !rasters || !passport) return null;
    let held = WHOLE.get(planet);
    if (!held) WHOLE.set(planet, (held = provinceWhole(rasters, passport)));
    return held;
  }, [wide, planet, rasters, passport]);
  const { lat, lon } = quantisedEye(eye, radius, within);
  //: The planet's disk has no frame to cut for -- every eye sees the same
  //: hemisphere of the same whole -- so it takes the walk that was made
  //: once. A bounded frame cuts its own, and cuts it again when the eye
  //: leaves the window it was cut for.
  const edges = useMemo(
    () =>
      !wide || !rasters || !passport
        ? null
        : within === undefined
          ? (whole?.edges ?? null)
          : provinceFrame(rasters, passport, { lat, lon }, radius, within),
    [wide, rasters, passport, whole, lat, lon, radius, within],
  );
  const drawn = useMemo(() => {
    if (!whole || !edges || !passport) return null;
    const parts: string[] = [];
    for (const [a, b] of edges) {
      const p = project(eye, radius, a);
      const q = project(eye, radius, b);
      if (!p.front || !q.front) continue;
      parts.push(`M${p.x.toFixed(1)} ${p.y.toFixed(1)}L${q.x.toFixed(1)} ${q.y.toFixed(1)}`);
    }
    const labels = whole.marks.flatMap((mark) => {
      const id = passport.provinces?.[mark.code - 1];
      const word = id ? provinceWord({ province: id }, names) : null;
      const at = project(eye, radius, mark.at);
      //: A name on the far side of the globe would be written backwards
      //: over the near side, so only the near half is named.
      return word && at.front
        ? [{ id, word, x: at.x, y: at.y, limb: limbShare(at.x, at.y, radius) }]
        : [];
    });
    return { edges: parts.join(""), labels };
  }, [whole, edges, passport, names, eye, radius]);
  //: The name is set in map units by the stylesheet and grown to the pixels
  //: it wants, as a closed city's name is (`Nodes`): a `font-size` of many
  //: thousands of units is one the browser draws no glyphs for at all.
  const grown = provinceLabelEm(far) / PROVINCE_EM;
  if (!drawn || (!drawn.edges && drawn.labels.length === 0)) return null;
  return (
    <g className="province-lines" aria-hidden="true">
      {drawn.edges && <path className="province" d={drawn.edges} />}
      {drawn.labels.map((label) => (
        //: Stood by a matrix, not a `translate`, whose arguments are lengths
        //: the browser saturates short of the limb (`placeAt`).
        <g key={label.id} transform={placeAt(label)}>
          <text
            className="province-label"
            transform={`scale(${(grown * label.limb).toFixed(4)})`}
          >
            {label.word}
          </text>
        </g>
      ))}
    </g>
  );
}
