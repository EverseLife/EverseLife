// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** The pure modules: no React, no socket -- the first tests the frontend ever
 *  had (review 2026-08-23, wave 3). */

import { describe, expect, it } from "vitest";

import * as amounts from "../amounts";
import type { Thing } from "../api";
import { arrange } from "../arrange";
import { answered, askless, CHEST_ANY, chestOf, chestZone, fits, halved } from "../drag";
import { goodsGlyph, nodeGlyph } from "../marks";
import { duration, hands, stamp, when, worldTime } from "../clock";
import { groundName } from "../grounds";
import { stockOf, tierLabel, tiersOf } from "../tiers";

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

  it("carries the rounding instead of printing a full unit", () => {
    //: Rounding each part on its own printed "7 ч 60 мин" for eight hours --
    //: which is what a build site an instant short of ready actually showed.
    expect(duration(3600 * 8 - 1)).toBe("8 ч");
    expect(duration(3600 - 1)).toBe("1 ч");
    expect(duration(59.7)).toBe("1 мин");
    expect(duration(3600 * 24 - 1, 24)).toBe("1.0 сут");
  });
});

describe("amounts", () => {
  it("reads pieces and measures off the book", () => {
    amounts.learn({
      bulk: ["Вода"],
      materials: [],
      units: { Проволока: "м" },
      operations: [],
      recipes: [],
      classes: {},
      tool_classes: {},
      synonyms: { "Вода родниковая": "Вода" },
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

describe("drag", () => {
  //: The book from the amounts suite above is already learned: Вода is
  //: measured, everything else is pieces.
  const pieces = (amount: number) => ({ goods: "Кирка", amount });
  const water = (amount: number) => ({ goods: "Вода", amount });

  it("skips the question for a single counted piece only", () => {
    expect(askless(pieces(1))).toBe(true);
    expect(askless(pieces(2))).toBe(false);
    //: A measured litre still asks: 0.4 of it is a legal move.
    expect(askless(water(1))).toBe(false);
  });

  it("halves pieces to whole ones and measures to any part", () => {
    expect(halved(pieces(5))).toBe(2);
    expect(halved(pieces(1))).toBe(0);
    expect(halved(water(5))).toBe(2.5);
  });

  it("clamps the typed answer to the stack and floors pieces", () => {
    expect(answered(pieces(5), 3)).toBe(3);
    expect(answered(pieces(5), 12)).toBe(5);
    expect(answered(pieces(5), 2.7)).toBe(2);
    expect(answered(water(5), 2.7)).toBe(2.7);
    expect(answered(water(5), 12)).toBe(5);
  });

  it("refuses an answer that is not a move", () => {
    expect(answered(pieces(5), 0)).toBeNull();
    expect(answered(pieces(5), -2)).toBeNull();
    expect(answered(pieces(5), Number.NaN)).toBeNull();
    //: 0.4 of a piece floors to zero pieces: no move either.
    expect(answered(pieces(5), 0.4)).toBeNull();
  });

  it("matches zones exactly and families by prefix", () => {
    expect(fits(["hands"], "hands")).toBe(true);
    expect(fits(["hands"], "floor")).toBe(false);
    expect(fits([CHEST_ANY], chestZone("a1"))).toBe(true);
    expect(fits([CHEST_ANY], "hold")).toBe(false);
    //: An exact chest entry does not become a family by accident.
    expect(fits([chestZone("a1")], chestZone("a2"))).toBe(false);
  });

  it("reads the chest id back out of its zone name", () => {
    expect(chestOf(chestZone("a1"))).toBe("a1");
    //: A colon inside the id survives the round trip.
    expect(chestOf(chestZone("x:y"))).toBe("x:y");
  });
});

describe("marks", () => {
  const recipe = (name: string, kind: string, food = false) =>
    ({ name, level: 1, kind, roles: false, food, inputs: [], amounts: {} });
  const BOOK = {
    bulk: [],
    liquid: ["Вода"],
    materials: [
      { name: "Железная руда", class: "Ископаемое" },
      { name: "Брёвна", class: "Растительное" },
    ],
    units: {},
    operations: [],
    recipes: [
      recipe("Хлеб", "consumable", true),
      recipe("Верстак", "station"),
      recipe("Куртка", "gear"),
      recipe("Зубило", "tool"),
    ],
    classes: {},
    tool_classes: { "Кирка": ["Железная кирка"], "Топор": ["Топор"] },
    synonyms: { "Вода родниковая": "Вода" },
  } as never;

  it("marks goods by class, never by name", () => {
    expect(goodsGlyph(BOOK, "Железная кирка")).toBe("pick");
    expect(goodsGlyph(BOOK, "Топор")).toBe("axe");
    //: A tool of no named class wears the plain hammer.
    expect(goodsGlyph(BOOK, "Зубило")).toBe("tool");
    expect(goodsGlyph(BOOK, "Хлеб")).toBe("food");
    expect(goodsGlyph(BOOK, "Верстак")).toBe("station");
    expect(goodsGlyph(BOOK, "Куртка")).toBe("gear");
    expect(goodsGlyph(BOOK, "Железная руда")).toBe("ore");
    expect(goodsGlyph(BOOK, "Брёвна")).toBe("plant");
    //: A liquid is found through its synonym, like everything else.
    expect(goodsGlyph(BOOK, "Вода родниковая")).toBe("water");
  });

  it("falls back to the honest crate", () => {
    expect(goodsGlyph(BOOK, "Нечто безвестное")).toBe("goods");
    expect(goodsGlyph(null, "Хлеб")).toBe("goods");
  });

  it("marks nodes by their place signs", () => {
    expect(nodeGlyph({ features: ["лес"] })).toBe("forest");
    //: The Forerunners' sign outranks the woods that grew over it.
    expect(nodeGlyph({ features: ["лес", "предтечи"] })).toBe("ruins");
    //: The rarer resource outranks the woods too: a stony forest is mined.
    expect(nodeGlyph({ features: ["лес", "камни"] })).toBe("ore");
    expect(nodeGlyph({ features: ["луг"] })).toBe("glade");
    expect(nodeGlyph({ settlement: true })).toBe("state");
    expect(nodeGlyph({ port: true })).toBe("port");
    expect(nodeGlyph({})).toBeNull();
  });

  it("lets the owner's emblem beat the land's signs", () => {
    expect(nodeGlyph({ emblem: "мастерская", features: ["лес"] })).toBe("station");
    //: An unknown word from a newer server falls back to the signs.
    expect(nodeGlyph({ emblem: "неведомое", features: ["лес"] })).toBe("forest");
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

  it("counts what is held whether or not it is worth a tier", () => {
    //: The bench asks how much is in the hands, and plenty of matter carries no
    //: quality at all -- seeds carry a cultivar, a liquid carries nothing. Summed
    //: over tiers it came out zero, and the master holding ten was told they had
    //: none, while the batch started on it perfectly well.
    const held = [
      thing({ goods: "Ткань", amount: 10 }),
      thing({ goods: "Руда", quality: 62, tier: "хорошее", amount: 18.6 }),
      thing({ goods: "Руда", amount: 3 }),
      thing({ goods: "Верёвка", quality: 90, tier: "отличное", amount: 9 }),
    ];
    expect(stockOf(held, "Ткань")).toBe(10);
    expect(stockOf(held, "Руда")).toBe(21.6);
    expect(stockOf(held, "Кирпич")).toBe(0);
    //: The tier picker keeps its own rule: no quality, no band to choose.
    expect(tiersOf(held, "Ткань")).toEqual([]);
  });

  it("reaches into a vessel: a liquid is never a stack of its own", () => {
    //: Water lies in a canister as its `content` (D-230), and the engine
    //: gathers a batch's materials the same way (`liquid.reach`). Counting only
    //: the top level, bread and broth read "в руках 0" over a full canister.
    const held = [
      thing({
        goods: "Канистра",
        amount: 1,
        content: [thing({ goods: "Вода", amount: 12 })],
      }),
      thing({ goods: "Канистра", amount: 1, content: [thing({ goods: "Вода", amount: 3 })] }),
      thing({ goods: "Вода", amount: 1 }),
    ];
    expect(stockOf(held, "Вода")).toBe(16);
    expect(stockOf(held, "Канистра")).toBe(2);
    expect(stockOf(held, "Спирт")).toBe(0);
  });

  it("offers the tiers of a liquid too: poured, it keeps its quality", () => {
    //: `liquid.settle` moves the stack whole, quality and all, and the engine
    //: honours the tier chosen for it. Counting the water but refusing to offer
    //: its bands would say "в руках 16" beside "в руках нет" on one row.
    const held = [
      thing({
        goods: "Канистра",
        amount: 1,
        content: [thing({ goods: "Спирт", quality: 70, tier: "хорошее", amount: 4 })],
      }),
      thing({
        goods: "Канистра",
        amount: 1,
        content: [thing({ goods: "Спирт", quality: 30, tier: "скверное", amount: 6 })],
      }),
    ];
    expect(tiersOf(held, "Спирт").map((s) => [s.tier, s.amount])).toEqual([
      ["хорошее", 4],
      ["скверное", 6],
    ]);
  });
});

describe("grounds", () => {
  it("says the ground in words", () => {
    expect(groundName("tax_land")).toBe("земельный налог");
    expect(groundName("court_fee")).toBe("пошлина суда");
  });

  //: A server newer than the client is no reason to drop the line: a key reads
  //: worse than a word and better than a blank.
  it("leaves an unknown ground as it came", () => {
    expect(groundName("свежее_основание")).toBe("свежее_основание");
  });
});
