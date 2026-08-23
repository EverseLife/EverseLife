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
export type CityWord = {
  /** The layer tab and the noun: "город". */
  name: string;
  /** Where a node of that layer is: "в городе". */
  within: string;
};

const CITY_WORDS: Record<string, CityWord> = {
  terra: { name: "город", within: "в городе" },
  aquatica: { name: "коммуна", within: "в коммуне" },
  pyroxis: { name: "лагерь", within: "в лагере" },
  aurora: { name: "заброшенный город", within: "в заброшенном городе" },
};

const TERRAN: CityWord = CITY_WORDS.terra;

const PLANET_NAMES: Record<string, string> = {
  terra: "Терра",
  aquatica: "Акватика",
  pyroxis: "Пироксис",
  aurora: "Аврора",
};

/** The planet's name for a key the engine speaks in; the key itself when unknown. */
export function planetName(planet: string): string {
  return PLANET_NAMES[planet] ?? planet;
}

/** The word for the `city` layer on this planet; Terra's for a planet unknown here. */
export function cityWord(planet: string | null | undefined): CityWord {
  return (planet && CITY_WORDS[planet]) || TERRAN;
}
