// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** The pure modules: no React, no socket -- the first tests the frontend ever
 *  had (review 2026-08-23, wave 3). */

import { describe, expect, it } from "vitest";

import * as amounts from "../amounts";
import type { Thing } from "../api";
import { arrange } from "../arrange";
import { duration, hands, stamp, when, worldTime } from "../clock";
import { tierLabel, tiersOf } from "../tiers";

const thing = (over: Partial<Thing>): Thing =>
  ({ id: "x", goods: "Руда", amount: 1, tier: "обычное", mass: 1, condition: 100, ...over }) as Thing;

describe("clock", () => {
  const clock = { planet: "Терра", epoch: "2026-01-01T00:00:00Z", day_hours: 30 };

  it("counts the planet's day from the epoch in its own hours", () => {
    const at = new Date("2026-01-02T07:30:00Z"); // 31.5 h later: day 2, 01:30
    expect(worldTime(clock, at)).toEqual({ day: 2, hour: 1, minute: 30 });
    expect(hands(clock, at)).toBe("01:30");
    expect(stamp(clock, at)).toBe("сутки 2 · 01:30");
  });

  it("never goes before the epoch", () => {
    expect(worldTime(clock, new Date("2025-12-31T00:00:00Z")).day).toBe(1);
  });

  it("says a moment relative to now in words", () => {
    const now = new Date("2026-01-01T00:00:00Z");
    expect(when("2026-01-01T00:00:20Z", now)).toBe("вот-вот");
    expect(when("2026-01-01T00:05:00Z", now)).toBe("через 5 мин");
    expect(when("2025-12-31T21:50:00Z", now)).toBe("2 ч 10 мин назад");
    expect(when(null, now)).toBe("—");
  });

  it("spells a duration by the world's day", () => {
    expect(duration(30)).toBe("30 с");
    expect(duration(90)).toBe("2 мин");
    expect(duration(3600 * 2.5)).toBe("2 ч 30 мин");
    expect(duration(3600 * 45, 30)).toBe("1.5 сут");
  });
});

describe("amounts", () => {
  it("reads pieces and measures off the book", () => {
    amounts.learn({
      raw: [],
      bulk: ["Вода"],
      units: { Проволока: "м" },
      operations: [],
      recipes: [],
      classes: {},
      materials: [],
      tool_classes: {},
      synonyms: { "Вода родниковая": "Вода" },
      labor_hours: {},
    });
    expect(amounts.counted("Кирка")).toBe(true);
    expect(amounts.counted("Вода")).toBe(false);
    expect(amounts.counted("Вода родниковая")).toBe(false);
    expect(amounts.tally("Кирка", 3)).toBe("3 шт.");
    expect(amounts.tally("Вода", 47.5)).toBe("47.5");
    expect(amounts.tally("Проволока", 2)).toBe("2 м");
    expect(amounts.step("Кирка")).toBe(1);
    expect(amounts.step("Вода")).toBe("any");
  });

  it("takes the whole stack when nothing was typed", () => {
    expect(amounts.chosen(null, 7)).toBe(7);
    expect(amounts.chosen(3, 7)).toBe(3);
    expect(amounts.chosen(12, 7)).toBe(7);
  });

  it("drops trailing zeros", () => {
    expect(amounts.trim(3)).toBe("3");
    expect(amounts.trim(3.5)).toBe("3.5");
    expect(amounts.trim(0.25)).toBe("0.25");
  });
});

describe("arrange", () => {
  const things = [
    thing({ id: "a", goods: "Руда", quality: 40, amount: 5, mass: 2 }),
    thing({ id: "b", goods: "Верёвка", amount: 2, mass: 0.5 }),
    thing({ id: "c", goods: "Руда", quality: 70, amount: 1, mass: 2 }),
  ];

  it("sorts by quality with the qualityless last either way", () => {
    expect(arrange(things, "quality", false).map((t) => t.id)).toEqual(["a", "c", "b"]);
    expect(arrange(things, "quality", true).map((t) => t.id)).toEqual(["c", "a", "b"]);
  });

  it("sorts by name in Russian order and by amount", () => {
    expect(arrange(things, "name", false).map((t) => t.goods)).toEqual(["Верёвка", "Руда", "Руда"]);
    expect(arrange(things, "amount", true).map((t) => t.id)).toEqual(["a", "b", "c"]);
  });
});

describe("tiers", () => {
  it("gathers the tiers of one goods, best first, with the quality span", () => {
    const stock = tiersOf(
      [
        thing({ goods: "Руда", quality: 40, tier: "обычное", amount: 3 }),
        thing({ goods: "Руда", quality: 45, tier: "обычное", amount: 2 }),
        thing({ goods: "Руда", quality: 80, tier: "отличное", amount: 1 }),
        thing({ goods: "Верёвка", quality: 90, tier: "отличное", amount: 9 }),
        thing({ goods: "Руда", amount: 9 }),
      ],
      "Руда",
    );
    expect(stock.map((s) => [s.tier, s.amount])).toEqual([
      ["отличное", 1],
      ["обычное", 5],
    ]);
    expect(tierLabel(stock[1])).toBe("обычное · 5 · кач. 40–45");
    expect(tierLabel(stock[0])).toBe("отличное · 1 · кач. 80");
  });
});
