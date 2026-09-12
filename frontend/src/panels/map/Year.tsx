// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** The year's winder on the planet's map (D-334): the sky's control, for
 *  the season and the weather, at a pace of the watcher's choosing. */

import { t } from "../../locale";
import { Winder } from "./Winder";
import { PACES, type Pace, type Year } from "./useYear";

/** Each pace's word, asked for by its own key: the locale's suite counts
 *  the keys the code names, and a key built from a template names none. */
const WORD_OF: Record<Pace, () => string> = {
  sixteenth: () => t("ui-map-year-pace-sixteenth"),
  eighth: () => t("ui-map-year-pace-eighth"),
  quarter: () => t("ui-map-year-pace-quarter"),
  one: () => t("ui-map-year-pace-one"),
  four: () => t("ui-map-year-pace-four"),
  sixteen: () => t("ui-map-year-pace-sixteen"),
};

export function YearClock({ year }: { year: Year }) {
  return (
    <Winder
      state={year}
      wind={t("ui-map-year-wind")}
      slider={t("ui-map-year-slider")}
      rule={t("ui-map-year-rule")}
      paces={{
        label: t("ui-map-year-pace"),
        options: PACES.map((key) => ({ key, word: WORD_OF[key]() })),
        current: year.pace,
        pick: (key) => year.setPace(key as Pace),
      }}
    />
  );
}
