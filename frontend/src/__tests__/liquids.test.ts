// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** Liquids and the planets' words (D-230): pure modules, read off the book. */

import { describe, expect, it } from "vitest";

import type { RecipeBook, Thing } from "../api";
import { capacityOf, carried, fill, isLiquid, isVessel, tiersOf } from "../liquids";
import { Words, forget, learn } from "../locale";
import { cityWord, planetName } from "../planets";

//: Ids on the wire since D-251: the Russian `name` stays for display, `holds`
//: carries the property id, and old Russian spellings live in `synonyms`.
const book = {
  bulk: ["water"],
  liquid: ["water", "rocket_fuel"],
  materials: [],
  units: {},
  operations: [],
  recipes: [
    { name: "Канистра", id: "canister", level: 3, kind: "tool", roles: false, food: false, inputs: [], amounts: {}, store: 20, holds: "liquid" },
    { name: "Сундук", id: "chest", level: 2, kind: "furniture", roles: false, food: false, inputs: [], amounts: {}, store: 300 },
    { name: "Кровать", id: "bed", level: 2, kind: "furniture", roles: false, food: false, inputs: [], amounts: {} },
  ],
  classes: {},
  tool_classes: {},
  synonyms: { "Вода родниковая": "water", Бак: "canister", Канистра: "canister" },
} as RecipeBook;

const thing = (over: Partial<Thing>): Thing =>
  ({ id: "x", goods: "canister", amount: 1, tier: "common", mass: 6, condition: 100, ...over }) as Thing;

describe("liquids", () => {
  it("reads liquids and vessels off the book, synonyms included", () => {
    expect(isLiquid(book, "water")).toBe(true);
    expect(isLiquid(book, "Вода родниковая")).toBe(true);
    expect(isLiquid(book, "pipe")).toBe(false);
    expect(isVessel(book, "canister")).toBe(true);
    //: The old Russian spellings still resolve through the synonyms.
    expect(isVessel(book, "Бак")).toBe(true);
    expect(isVessel(book, "Канистра")).toBe(true);
    //: A chest has a capacity but admits no liquid; a bed has neither.
    expect(isVessel(book, "chest")).toBe(false);
    expect(isVessel(book, "bed")).toBe(false);
    expect(isVessel(null, "canister")).toBe(false);
  });

  it("spells the fill with the catalog's capacity, in the player's words", () => {
    const names = { goods: { water: "Вода" } } as never;
    expect(capacityOf(book, "canister")).toBe(20);
    expect(fill(book, names, thing({ content: [] }))).toBe("пусто · 0.0 из 20 кг");
    const water = thing({ id: "w", goods: "water", amount: 12, mass: 0.2 });
    expect(fill(book, names, thing({ content: [water] }))).toBe("Вода 12.0 · 2.4 из 20 кг");
    //: No bundle yet -- the raw id is still an honest render, never a crash.
    expect(fill(book, null, thing({ content: [water] }))).toBe("water 12.0 · 2.4 из 20 кг");
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
  });

  //: The planet's name is the vault's, not this module's (D-251 wave IV): it
  //: comes from the renames bundle through `PLANET($planet)`, so the test has
  //: to hand over a bundle before it can expect a Russian word.
  it("names the planet from the renames bundle, the id without one", () => {
    expect(planetName("pyroxis")).toBe("pyroxis");
    learn(
      new Words(
        { locale: "ru", locales: ["ru"], ftl: "" },
        { planets: { pyroxis: "Пироксис" } } as never,
      ),
    );
    expect(planetName("pyroxis")).toBe("Пироксис");
    //: A planet the table does not know still reads as its id.
    expect(planetName("void")).toBe("void");
    forget();
  });
});

describe("how much of a liquid the hands hold", () => {
  //: Nothing in `look.inventory` is ever named for a liquid (D-230): the
  //: water is inside the canisters, and counting it means opening them.
  const canister = (id: string, inside: { goods: string; amount: number; tier?: string }[]) =>
    ({
      id,
      key: "canister",
      goods: "canister",
      tier: "awful",
      amount: 1,
      content: inside.map((one, n) => ({
        id: `${id}-${n}`,
        key: one.goods,
        goods: one.goods,
        tier: one.tier ?? "awful",
        amount: one.amount,
        mass: 1,
      })),
    }) as unknown as Thing;

  it("adds up what every vessel holds of the one liquid", () => {
    const hands = [canister("a", [{ goods: "water", amount: 12 }]), canister("b", [{ goods: "water", amount: 8 }])];
    expect(carried(hands, "water")).toBe(20);
  });

  it("counts the asked liquid and no other", () => {
    const hands = [canister("a", [{ goods: "water", amount: 12 }, { goods: "alcohol", amount: 3 }])];
    expect(carried(hands, "alcohol")).toBe(3);
  });

  //: A liquid has quality like anything else, so one name in one pocket may
  //: be two positions: the spirit of a good batch and of a bad one.
  it("counts one tier at a time when asked for one", () => {
    const hands = [
      canister("a", [{ goods: "spirit", amount: 4, tier: "отличное" }]),
      canister("b", [{ goods: "spirit", amount: 6, tier: "обычное" }]),
    ];
    expect(carried(hands, "spirit")).toBe(10);
    expect(carried(hands, "spirit", "отличное")).toBe(4);
    //: Which tiers, not in which order: the vessels arrive unordered, and
    //: `tiersOf` says so. Whoever needs an order sorts by the ladder.
    expect(new Set(tiersOf(hands, "spirit"))).toEqual(new Set(["обычное", "отличное"]));
  });

  it("is nought where the hands carry no vessel at all", () => {
    expect(carried([{ id: "x", key: "iron_ore", goods: "iron_ore", tier: "good", amount: 5 } as unknown as Thing], "water")).toBe(0);
  });
});
