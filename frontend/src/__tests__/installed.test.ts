// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Put up or lying on the client (D-278): which things get an "install"
 * button, and when the place may take one more.
 *
 * Both are pure and both decide what is offered: a sack must never get the
 * button, and a full house must not collect a refusal after the click.
 */

import { describe, expect, it } from "vitest";

import type { Look, RecipeBook } from "../api";
import { mayInstall } from "../building";
import { isGear, recipeKind } from "../classes";

const book = {
  recipes: [
    { name: "Верстак", id: "workbench", kind: "station", roles: false },
    { name: "Сундук", id: "chest", kind: "furniture", roles: false },
    { name: "Гвозди", id: "nails", kind: "material", roles: false },
    //: A vessel is a tool by kind and stands on the hull's lines only put up
    //: (D-288): the button is offered for what holds a liquid, not for a pick.
    { name: "Канистра", id: "canister", kind: "tool", roles: false, holds: "liquid", store: 20 },
    { name: "Кирка", id: "pickaxe", kind: "tool", roles: false },
  ],
} as unknown as RecipeBook;

const place = (space: Partial<NonNullable<Look["floor"]>["space"]>, mine = true) =>
  ({
    floor: {
      space: { area: 20, used: 0, cargo_mass: 0, free: 20, slots: 2, slots_used: 0, ...space },
      things: [],
      open: true,
      mine,
    },
  }) as unknown as Look;

describe("что ставят, а что кладут (D-278)", () => {
  it("станок и мебель ставят, материал только кладут", () => {
    expect(recipeKind(book, "workbench")).toBe("station");
    expect(isGear(book, "workbench")).toBe(true);
    expect(isGear(book, "chest")).toBe(true);
    expect(isGear(book, "nails")).toBe(false);
    expect(isGear(book, "iron_ore")).toBe(false);
    expect(isGear(null, "workbench")).toBe(false);
  });

  it("тару ставят, как мебель, а инструмент без тары -- нет (D-288)", () => {
    expect(isGear(book, "canister")).toBe(true);
    expect(isGear(book, "pickaxe")).toBe(false);
  });
});

describe("когда предлагают «Установить»", () => {
  it("своё здание с местом", () => {
    expect(mayInstall(place({}))).toBe(true);
  });

  it("не своё место -- нет", () => {
    expect(mayInstall(place({}, false))).toBe(false);
  });

  it("без крыши -- нет", () => {
    expect(mayInstall(place({ area: 0, slots: 0 }))).toBe(false);
  });

  it("места заняты -- нет", () => {
    expect(mayInstall(place({ slots: 2, slots_used: 2 }))).toBe(false);
    expect(mayInstall(place({ slots: 2, slots_used: 1 }))).toBe(true);
  });

  it("без пола вовсе -- нет", () => {
    expect(mayInstall({} as unknown as Look)).toBe(false);
  });
});
