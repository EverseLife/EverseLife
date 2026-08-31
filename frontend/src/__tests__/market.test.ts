// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** What the market panel offers and how it reads quality (D-239): pure module. */

import { describe, expect, it } from "vitest";

import type { RecipeBook } from "../api";
import { catalogue, floorOf, tierOf } from "../market";

const book = {
  bulk: ["Вода"],
  liquid: ["Вода", "Ракетное топливо"],
  materials: [{ name: "Железная руда" }, { name: "Вода" }, { name: "ТЭЦ Предтеч", relic: true }],
  units: {},
  operations: [],
  recipes: [
    { name: "Канистра", level: 3, kind: "tool", roles: false, food: false, inputs: [], amounts: {}, store: 20, holds: "жидкость" },
    { name: "Сундук", level: 2, kind: "furniture", roles: false, food: false, inputs: [], amounts: {}, store: 300 },
  ],
  classes: {},
  tool_classes: {},
  synonyms: { "Вода родниковая": "Вода" },
} as RecipeBook;

describe("market catalogue", () => {
  //: The picker offers only what the engine would take (`_tradable`): the
  //: same book, the same three rules, so the two do not drift apart.
  const shelf = book;

  it("offers made things and the world's own stuff", () => {
    expect(catalogue(shelf)).toContain("Железная руда");
    expect(catalogue(shelf)).toContain("Канистра");
  });

  it("offers neither a relic nor a liquid: neither can be laid on a counter", () => {
    expect(catalogue(shelf)).not.toContain("ТЭЦ Предтеч");
    expect(catalogue(shelf)).not.toContain("Вода");
  });

  it("has nothing to offer without a book", () => {
    expect(catalogue(null)).toEqual([]);
  });
});

describe("quality floors", () => {
  //: The world's window, as `/public/quality/tiers` serves it.
  const tiers = [
    { from: 0, to: 19, name: "скверное" },
    { from: 20, to: 39, name: "плохое" },
    { from: 40, to: 59, name: "обычное" },
    { from: 60, to: 79, name: "хорошее" },
    { from: 80, to: 100, name: "отличное" },
  ];

  it("reads a tier button as the start of its band", () => {
    expect(floorOf(tiers, "хорошее")).toBe(60);
    expect(floorOf(tiers, "скверное")).toBe(0);
  });

  it("says which window a floor falls into, bands included", () => {
    expect(tierOf(tiers, 60)).toBe("хорошее");
    expect(tierOf(tiers, 79)).toBe("хорошее");
    expect(tierOf(tiers, 80)).toBe("отличное");
    //: Below the first band there is still a band: quality has no negatives.
    expect(tierOf(tiers, 0)).toBe("скверное");
  });
});
