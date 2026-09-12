// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** The year's winder on the planet's map (D-334): the sky's control, for
 *  the season and the weather. */

import { t } from "../../locale";
import { Winder } from "./Winder";
import type { Year } from "./useYear";

export function YearClock({ year }: { year: Year }) {
  return (
    <Winder state={year} wind={t("ui-map-year-wind")} slider={t("ui-map-year-slider")} rule={t("ui-map-year-rule")} />
  );
}
