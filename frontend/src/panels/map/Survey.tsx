// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The panel of an aimed point (D-321): how far it is, the two verbs -- go,
 * or take the point back -- and, since the owner's word of 2026-09-12
 * (D-321 addendum), what the field says there before the walk: the biome
 * and the face of the ground, the water, the climate, the soil's moisture,
 * and the chances of what the run rolls -- the marks of the place, a vein,
 * a scheme of nodes. Chances, never the roll: the roll is the find's own.
 * All of it comes from `explore.peek`, which judges the aim by the run's
 * rule first, so a point one may not walk to says why here, before the
 * walk is paid for.
 */

import { useBook, useNames } from "../../actions";
import type { Peek } from "../../api";
import { t } from "../../locale";
import { useTerrain } from "./Ground";
import { dryLaw, moistureOf } from "./shade";
import type { Preview } from "./useScout";
import { biomeWord, provinceWord } from "./words";

/** The marks a place may carry, in the order they are told. */
const MARKS = ["woods", "stones", "meadow"] as const;

function markWord(mark: (typeof MARKS)[number]): string {
  switch (mark) {
    case "woods": return t("ui-emblem-woods");
    case "stones": return t("ui-emblem-stones");
    case "meadow": return t("ui-emblem-meadow");
  }
}

function waterWord(water: Peek["water"]): string {
  switch (water) {
    case "river": return t("ui-map-peek-water-river");
    case "lake": return t("ui-map-peek-water-lake");
    default: return t("ui-map-peek-water-none");
  }
}

/** What the field says at the point, as lines of the panel. */
function Told({
  peek,
  planet,
  nameOf,
  joining,
}: {
  peek: Peek;
  planet: string | null;
  /** The name of a node the client knows by its key, for a cell already found. */
  nameOf: (key: string) => string | null;
  /** The heading names the node already when the way is aimed at it. */
  joining: boolean;
}) {
  const book = useBook();
  const names = useNames();
  const passport = useTerrain(planet)?.raster ?? null;
  //: The biome's word in the reader's language (`biomeWord`); the face's
  //: and the province's off the same table, as the inspector names a find.
  //: The mountain is a fact of the ground, and stands with the ground's
  //: words, not under the water's (copy review, 2026-09-12).
  const ground = [
    biomeWord(peek.biome, names, book),
    peek.facet ? (names?.facets?.[peek.facet] ?? peek.facet) : null,
    peek.province ? provinceWord({ province: peek.province }, names) : null,
    peek.mountain ? t("ui-map-peek-mountain") : null,
  ]
    .filter((word): word is string => Boolean(word))
    .join(" · ");
  //: The stream the relief does not draw is a die of the run (D-321 item
  //: 2): told as its chance beside the map's own answer.
  const water = [
    waterWord(peek.water),
    peek.water === "none" && peek.stream_chance > 0
      ? t("ui-map-peek-stream", { percent: peek.stream_chance })
      : null,
  ]
    .filter((word): word is string => Boolean(word))
    .join(" · ");
  //: The soil's moisture by the same law the map's layer is drawn by
  //: (`shade.moistureOf`, D-296): the water is the peek's, the planet's
  //: hot end the passport's. No rain: the survey tells a place, not a
  //: moment, and the rain that waters it comes and goes (D-338).
  const law = dryLaw(book?.constants);
  const moisture = moistureOf(
    law,
    peek.temperature_c,
    peek.water === "none" ? 0 : 1,
    passport?.temperature_c.hot ?? peek.temperature_c,
    0,
  );
  //: One message a mark -- the word and its chance are the language's to
  //: order -- and the list joined by the dot, D-258's third lawful place.
  const marks = MARKS.filter((mark) => (peek.marks[mark] ?? 0) > 0)
    .map((mark) => t("ui-map-peek-mark", { mark: markWord(mark), percent: peek.marks[mark] }))
    .join(" · ");
  return (
    <dl className="peek">
      {peek.found && !joining && (
        <>
          <dt>{t("ui-map-peek-found")}</dt>
          <dd>{nameOf(peek.found) ?? t("ui-map-peek-found-there")}</dd>
        </>
      )}
      <dt>{t("ui-map-peek-ground")}</dt>
      <dd>{ground}</dd>
      <dt>{t("ui-map-peek-water")}</dt>
      <dd>{water}</dd>
      <dt>{t("ui-map-peek-climate")}</dt>
      <dd>
        {t("ui-map-peek-climate-value", {
          c: Math.round(peek.temperature_c),
          swing: Math.round(peek.swing_c),
          rain: Math.round(peek.rain),
        })}
      </dd>
      <dt>{t("ui-map-peek-moisture")}</dt>
      <dd>{t("ui-map-peek-percent", { percent: Math.round(moisture * 100) })}</dd>
      {marks && (
        <>
          <dt>{t("ui-map-peek-marks")}</dt>
          <dd>{marks}</dd>
        </>
      )}
      <dt>{t("ui-map-peek-vein")}</dt>
      <dd>{t("ui-map-peek-percent", { percent: peek.vein_chance })}</dd>
      {peek.complex_chance > 0 && (
        <>
          <dt>{t("ui-map-peek-complex")}</dt>
          <dd>{t("ui-map-peek-percent", { percent: peek.complex_chance })}</dd>
        </>
      )}
    </dl>
  );
}

export function Survey({
  metres,
  preview,
  planet,
  nameOf,
  joining,
  target,
  busy,
  trouble,
  onGo,
  onClear,
}: {
  metres: number;
  /** What the field says at the point, on its way or here (`useScout`). */
  preview: Preview;
  planet: string | null;
  /** The name of a node the client knows by its key (the map's `byKey`). */
  nameOf: (key: string) => string | null;
  /** Whether the aim is a known node and the run lays a way to it (D-321
   *  addendum): the words are the way's, the walk is the same. */
  joining: boolean;
  /** The aimed node's name while joining: the heading names it. */
  target: string | null;
  busy: boolean;
  /** The world's refusal of the last run asked for, if any: the map's own. */
  trouble: string | null;
  onGo: () => void;
  onClear: () => void;
}) {
  return (
    <aside className="inspect survey">
      <p className="sign">
        {joining
          ? t("ui-map-join-aim", { node: target ?? "", metres: Math.round(metres) })
          : t("ui-map-survey-aim", { metres: Math.round(metres) })}
      </p>
      {preview.pending && <p className="note">{t("ui-map-peek-asking")}</p>}
      {/* The peek's refusal is the run's, said before the run: a point one
          may not walk to is told so here, and the verb below would only
          say it again. */}
      {preview.trouble && <p className="trouble">{preview.trouble}</p>}
      {preview.peek && (
        <Told peek={preview.peek} planet={planet} nameOf={nameOf} joining={joining} />
      )}
      {trouble && <p className="trouble">{trouble}</p>}
      <div className="row">
        {/* The way's verb is for the node aimed at and nothing else: a peek
            that found no node in the cell says the tap named no node. */}
        <button
          onClick={onGo}
          disabled={busy || preview.refused || (joining && preview.peek !== null && preview.peek.found === null)}
        >
          {t(joining ? "ui-map-join-go" : "ui-map-survey")}
        </button>
        <button className="quiet" onClick={onClear} disabled={busy}>
          {t("ui-map-survey-clear")}
        </button>
      </div>
    </aside>
  );
}
