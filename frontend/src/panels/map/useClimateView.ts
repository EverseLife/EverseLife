// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What the map shows of the sky over the ground as of the moment shown:
 * the year's winder (D-334), and off its moment the sun, the season and
 * the weather (D-335) of the shown ball. One hook, so `GraphMap` hands
 * the ground, the probe and the night one set of answers.
 */

import { useMemo } from "react";

import type { Look, RecipeBook } from "../../api";
import { UNITS_PER_METRE, type Geo } from "./globe";
import { seasonOf, sunOf } from "./Ground";
import type { Season } from "./season";
import { useYear, yearOf, type Year } from "./useYear";
import { daysSince, weatherLaw, type WeatherLaw } from "./weather";

export type ClimateView = {
  year: Year;
  sun: Geo | null;
  season: Season;
  weather: WeatherLaw;
  /** Real days since the epoch at the moment shown. */
  weatherDays: number;
};

export function useClimateView(
  book: RecipeBook | null,
  planet: string | null,
  radius: number | null,
  clock: Look["clock"],
): ClimateView {
  const year = useYear(yearOf(book?.constants, planet ?? ""));
  //: One law per book and radius, so the ground redraws by the moment and
  //: not by the render.
  const weather = useMemo(
    () => weatherLaw(book?.constants, (radius ?? 0) / UNITS_PER_METRE),
    [book, radius],
  );
  const shown = planet ?? "";
  return {
    year,
    sun: sunOf(shown, clock, book, year.atMs),
    season: seasonOf(shown, clock, book, year.atMs),
    weather,
    weatherDays: daysSince(clock?.epoch ?? null, year.atMs),
  };
}
