// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** The detail beside a line of the return digest (`panels/happened.ts`):
 *  every id in the payload is named in the reader's language, and a find,
 *  which has no name (D-321), by the keys of its ground (`facet.told_of`) --
 *  the face first, the biome failing it, as the map names it. */

import { describe, expect, it } from "vitest";

import { detail, type Happened } from "../panels/happened";
import { nodeWord } from "../panels/map/words";
import { DEFAULT_LOCALE, Words, learn, t } from "../locale";
import type { Names } from "../names";

//: The general word for a nameless place comes off the client's own locale.
learn(new Words({ locale: DEFAULT_LOCALE, locales: [DEFAULT_LOCALE], ftl: "" }, null));

const AT = "2026-09-19T12:00:00+00:00";

function line(kind: string, payload: Record<string, unknown>): Happened {
  return { at: AT, kind, payload };
}

//: The two languages' tables as `/public/renames` sends them, cut to what
//: the digest reads, and the vault's own word off `/public/constants`.
const english = {
  biomes: { forest: "Forest" },
  facets: { forest_edge: "Forest edge" },
  goods: { iron_ore: "Iron ore" },
} as unknown as Names;
const russian = {
  biomes: { forest: "Лес" },
  facets: { forest_edge: "Опушка" },
  goods: { iron_ore: "Железная руда" },
} as unknown as Names;
const book = { constants: { "biome.names": { forest: "Лес", taiga: "Тайга" } } };

describe("the digest's detail", () => {
  //: The defect this pins: the journal carried the vault's Russian word, and
  //: an English reader got it after an English line.
  it("names a find in the reader's language", () => {
    const annexed = line("land.annexed", {
      city: "Capital",
      near: "terra.core",
      biome: "forest",
      facet: "forest_edge",
    });
    expect(detail(annexed, english, book)).toBe("Forest edge");
    expect(detail(annexed, russian, book)).toBe("Опушка");
  });

  //: One find, one word: the line says what the map beside it says.
  it("names a find by the rule the map names it by", () => {
    const ground = { facet: "forest_edge", features: ["forest"] };
    const found = line("explore.found", { biome: "forest", facet: "forest_edge" });
    expect(detail(found, english, book)).toBe(nodeWord(ground, book.constants["biome.names"], english));
  });

  it("says the biome of a find with no face written on it", () => {
    for (const kind of ["explore.found", "travel.arrived", "agro.stalled", "land.annexed"]) {
      expect(detail(line(kind, { biome: "forest" }), english, book), kind).toBe("Forest");
    }
  });

  //: A name goes as it is, and a run's line keeps the kind of a named node
  //: as well: the name comes first.
  it("prefers the node's own name to its ground", () => {
    const found = line("explore.found", { node: "Old Mill", biome: "forest", known: true });
    expect(detail(found, english, book)).toBe("Old Mill");
  });

  //: The names have not arrived yet, or have no overlay for this biome: the
  //: vault's word, and the general word for a place when neither table has
  //: it -- never the bare key.
  it("falls back to the vault's word, then to the general word", () => {
    expect(detail(line("explore.found", { biome: "taiga" }), english, book)).toBe("Тайга");
    expect(detail(line("explore.found", { biome: "taiga" }), null, null)).toBe(
      t("ui-map-node-unnamed"),
    );
  });

  it("names goods before any place", () => {
    const made = line("craft.finished", { output: "iron_ore", biome: "forest" });
    expect(detail(made, english, book)).toBe("Iron ore");
  });

  it("says nothing where the payload names nothing", () => {
    expect(detail(line("land.annexed", { city: "Capital" }), english, book)).toBeNull();
    expect(detail(line("land.annexed", { node: "", biome: "", facet: "" }), english, book)).toBeNull();
  });
});
