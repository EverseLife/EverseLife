// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What the built-up layer is called on each planet (D-230).
 *
 * The graph has one `city` layer everywhere, but the word "город" is Terra's:
 * on Pyroxis nothing gets built -- the ground shakes too often -- so what
 * stands there is a camp; on Aurora the scouts find parts of a city that
 * stood already, the Forerunners' abandoned one; on Aquatica people live in
 * communes. A display word, not a layer of its own: the engine knows nothing
 * of it, and a fifth planet adds a line here.
 */

import { t } from "./locale";
export type CityWord = {
  /** The layer tab and the noun: "город". */
  name: string;
  /** Where a node of that layer is: "в городе". */
  within: string;
};

/** The pair of message **keys** for one planet's word (D-251 wave IV).
 *
 * Keys and not words: this map is built once when the module is evaluated, and
 * a `t()` at that moment would freeze whichever language happened to be spoken
 * into every later render. `cityWord` says the words. */
type CityWordKeys = { name: string; within: string };

const CITY_WORDS: Record<string, CityWordKeys> = {
  terra: { name: "ui-city-word-terra", within: "ui-city-word-terra-in" },
  aquatica: { name: "ui-city-word-aquatica", within: "ui-city-word-aquatica-in" },
  pyroxis: { name: "ui-city-word-pyroxis", within: "ui-city-word-pyroxis-in" },
  aurora: { name: "ui-city-word-aurora", within: "ui-city-word-aurora-in" },
};

const TERRAN: CityWordKeys = CITY_WORDS.terra;

/**
 * The planet's name for a key the engine speaks in; the key itself when unknown.
 *
 * There is no table of planet names here, and that is the point: the vault
 * already owns them (`renames.json`, domain `planets`), they arrive with
 * `/public/renames`, and `PLANET($planet)` is the bundle's own way of asking
 * for one. A second list in this file would be a copy that drifts in silence
 * -- and, worse, a Russian copy: a planet renamed in the vault, or named in
 * another language in wave V, would still read «Терра» here.
 *
 * The fallback is the same as it always was: a planet the table does not know
 * reads as its id, because `PLANET` falls back to the id it was handed.
 */
export function planetName(planet: string): string {
  return t("ui-planet-name", { planet });
}

/** The word for the `city` layer on this planet; Terra's for a planet unknown here. */
export function cityWord(planet: string | null | undefined): CityWord {
  const keys = (planet && CITY_WORDS[planet]) || TERRAN;
  return { name: t(keys.name), within: t(keys.within) };
}
