// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The Forerunners on the client (D-232): the reactor's countdown and the mark
 * that keeps their things where they were found.
 *
 * Both are pure and both drive what the player sees offered: a relic must never
 * get a "take it down" button, and a reactor's last month must be visible as a
 * number rather than as a surprise.
 */

import { describe, expect, it } from "vitest";

import type { RecipeBook } from "../api";
import { isRelic } from "../classes";
import { daysLeft, reactorState } from "../panels/place/Reactor";

const DAY = 86_400_000;
const soon = (days: number) => new Date(Date.now() + days * DAY).toISOString();

const book = {
  materials: [
    { name: "ТЭЦ Предтеч", class: "ТЭЦ", relic: true },
    { name: "Уголь", class: "Ископаемое" },
  ],
  classes: { "ТЭЦ": ["ТЭЦ", "ТЭЦ Предтеч"] },
} as unknown as RecipeBook;

describe("реактор Предтеч", () => {
  it("считает целые сутки до молчания", () => {
    //: С запасом в полсуток: между вычислением даты и проверкой проходят
    //: миллисекунды, а счёт идёт вниз — ровно десять суток были бы девятью.
    expect(daysLeft(soon(10.5))).toBe(10);
    expect(reactorState(soon(10.5))).toBe("10 сут");
  });

  it("не уходит в минус: погасший погас", () => {
    expect(daysLeft(soon(-5))).toBe(0);
    expect(reactorState(soon(-5))).toBe("погас");
  });
});

describe("реликвия", () => {
  it("узнаётся по каталогу, а не по имени в коде", () => {
    expect(isRelic(book, "ТЭЦ Предтеч")).toBe(true);
    expect(isRelic(book, "ТЭЦ")).toBe(false);
    expect(isRelic(book, "Уголь")).toBe(false);
    expect(isRelic(null, "ТЭЦ Предтеч")).toBe(false);
  });
});
