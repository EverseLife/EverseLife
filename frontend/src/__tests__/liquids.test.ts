// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** Liquids and the planets' words (D-230): pure modules, read off the book. */

import { describe, expect, it } from "vitest";

import type { RecipeBook, Thing } from "../api";
import { capacityOf, fill, isLiquid, isVessel } from "../liquids";
import { cityWord, planetName } from "../planets";

const book = {
  bulk: ["Вода"],
  liquid: ["Вода", "Ракетное топливо"],
  materials: [],
  units: {},
  operations: [],
  recipes: [
    { name: "Канистра", level: 3, kind: "tool", roles: false, food: false, inputs: [], amounts: {}, store: 20, holds: "жидкость" },
    { name: "Сундук", level: 2, kind: "furniture", roles: false, food: false, inputs: [], amounts: {}, store: 300 },
    { name: "Кровать", level: 2, kind: "furniture", roles: false, food: false, inputs: [], amounts: {} },
  ],
  classes: {},
  tool_classes: {},
  synonyms: { "Вода родниковая": "Вода", Бак: "Канистра" },
} as RecipeBook;

const thing = (over: Partial<Thing>): Thing =>
  ({ id: "x", goods: "Канистра", amount: 1, tier: "обычное", mass: 6, condition: 100, ...over }) as Thing;

describe("liquids", () => {
  it("reads liquids and vessels off the book, synonyms included", () => {
    expect(isLiquid(book, "Вода")).toBe(true);
    expect(isLiquid(book, "Вода родниковая")).toBe(true);
    expect(isLiquid(book, "Труба")).toBe(false);
    expect(isVessel(book, "Канистра")).toBe(true);
    expect(isVessel(book, "Бак")).toBe(true);
    //: A chest has a capacity but admits no liquid; a bed has neither.
    expect(isVessel(book, "Сундук")).toBe(false);
    expect(isVessel(book, "Кровать")).toBe(false);
    expect(isVessel(null, "Канистра")).toBe(false);
  });

  it("spells the fill with the catalog's capacity", () => {
    expect(capacityOf(book, "Канистра")).toBe(20);
    expect(fill(book, thing({ content: [] }))).toBe("пусто · 0.0 из 20 кг");
    const water = thing({ id: "w", goods: "Вода", amount: 12, mass: 0.2 });
    expect(fill(book, thing({ content: [water] }))).toBe("Вода 12.0 · 2.4 из 20 кг");
  });
});

describe("planets", () => {
  it("names the built-up layer by the planet, Terra's word otherwise", () => {
    expect(cityWord("terra").name).toBe("город");
    expect(cityWord("pyroxis")).toEqual({ name: "лагерь", within: "в лагере" });
    expect(cityWord("aurora").within).toBe("в заброшенном городе");
    expect(cityWord("aquatica").name).toBe("коммуна");
    expect(cityWord(null).name).toBe("город");
    expect(cityWord("void").name).toBe("город");
    expect(planetName("pyroxis")).toBe("Пироксис");
    expect(planetName("void")).toBe("void");
  });
});
