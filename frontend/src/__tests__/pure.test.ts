// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** The pure modules: no React, no socket -- the first tests the frontend ever
 *  had (review 2026-08-23, wave 3). */

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import * as amounts from "../amounts";
import type { CityVote, RecipeBook, Thing } from "../api";
import { arrange } from "../arrange";
import { answered, askless, CHEST_ANY, chestOf, chestZone, fits, halved } from "../drag";
import { goodsGlyph, nodeGlyph } from "../marks";
import { duration, hands, stamp, when, worldTime } from "../clock";
import { groundName } from "../grounds";
import { forget, learn, Words } from "../locale";
import { catalogue, coins, exactly } from "../market";
import { pollTally, pollThreshold } from "../polls";
import {
  flavorText,
  goodsKeyName,
  goodsName,
  plantName,
  tierName,
  type Names,
} from "../names";
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

  it("names a share in whole percent, and a rare one as under one", () => {
    expect(amounts.percent(0.625)).toBe("63%");
    expect(amounts.percent(1)).toBe("100%");
    expect(amounts.percent(0.0101)).toBe("1%");
    expect(amounts.percent(0.0024)).toBe("<1%");
    expect(amounts.percent(0.0099)).toBe("<1%");
    expect(amounts.percent(0)).toBe("0%");
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
  //: The wire speaks ids since D-251: recipes carry an `id` beside the Russian
  //: `name`, and everything is looked up by the id.
  const recipe = (name: string, id: string, kind: string, food = false) =>
    ({ name, id, kind, roles: false, food, inputs: [], amounts: {} });
  const BOOK = {
    bulk: [],
    liquid: ["water"],
    materials: [
      { name: "Железная руда", id: "iron_ore", class: "minable" },
      { name: "Брёвна", id: "logs", class: "flora" },
    ],
    units: {},
    operations: [],
    recipes: [
      recipe("Хлеб", "bread", "consumable", true),
      recipe("Верстак", "workbench", "station"),
      recipe("Куртка", "jacket", "gear"),
      recipe("Зубило", "chisel", "tool"),
    ],
    classes: {},
    tool_classes: { pickaxe: ["iron_pickaxe"], axe: ["axe"] },
    synonyms: { "Вода родниковая": "water", "Железная кирка": "iron_pickaxe" },
  } as never;

  it("marks goods by class, never by name", () => {
    expect(goodsGlyph(BOOK, "iron_pickaxe")).toBe("pick");
    expect(goodsGlyph(BOOK, "axe")).toBe("axe");
    //: A tool of no named class wears the plain hammer.
    expect(goodsGlyph(BOOK, "chisel")).toBe("tool");
    expect(goodsGlyph(BOOK, "bread")).toBe("food");
    expect(goodsGlyph(BOOK, "workbench")).toBe("station");
    expect(goodsGlyph(BOOK, "jacket")).toBe("gear");
    expect(goodsGlyph(BOOK, "iron_ore")).toBe("ore");
    expect(goodsGlyph(BOOK, "logs")).toBe("plant");
    //: An old Russian spelling still resolves through the synonyms.
    expect(goodsGlyph(BOOK, "Вода родниковая")).toBe("water");
    expect(goodsGlyph(BOOK, "Железная кирка")).toBe("pick");
  });

  it("falls back to the honest crate", () => {
    expect(goodsGlyph(BOOK, "нечто безвестное")).toBe("goods");
    expect(goodsGlyph(null, "bread")).toBe("goods");
  });

  it("marks nodes by their place signs", () => {
    expect(nodeGlyph({ features: ["woods"] })).toBe("forest");
    //: The Forerunners' sign outranks the woods that grew over it.
    expect(nodeGlyph({ features: ["woods", "precursors"] })).toBe("ruins");
    //: The rarer resource outranks the woods too: a stony forest is mined.
    expect(nodeGlyph({ features: ["woods", "stones"] })).toBe("ore");
    expect(nodeGlyph({ features: ["meadow"] })).toBe("glade");
    expect(nodeGlyph({ settlement: true })).toBe("state");
    expect(nodeGlyph({ port: true })).toBe("port");
    expect(nodeGlyph({})).toBeNull();
  });

  it("lets the owner's emblem beat the land's signs", () => {
    expect(nodeGlyph({ emblem: "workshop", features: ["woods"] })).toBe("station");
    //: An unknown word from a newer server falls back to the signs.
    expect(nodeGlyph({ emblem: "unheard_of", features: ["woods"] })).toBe("forest");
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

  it("sorts ids by their Russian display words, not by the id", () => {
    //: "rope" < "ore" in ASCII, but Верёвка < Руда is what the player reads.
    const stacks = [
      thing({ id: "a", goods: "ore" }),
      thing({ id: "b", goods: "rope" }),
    ];
    const names = { goods: { ore: "Руда", rope: "Верёвка" } } as never;
    expect(arrange(stacks, "name", false, names).map((t) => t.goods)).toEqual(["rope", "ore"]);
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

describe("names", () => {
  const table = {
    goods: { iron_ore: "Железная руда", soup: "Суп", beans: "Бобы", recorded_recipe: "Рецепт" },
    tiers: { fine: "отличное" },
    virtual_stations: { by_hand: "Руками" },
    plants: { spelt: "Полба" },
  } as unknown as Names;

  it("says an id in the player's words and passes the unknown through", () => {
    expect(goodsName(table, "iron_ore")).toBe("Железная руда");
    expect(goodsName(table, "by_hand")).toBe("Руками");
    //: Old data must never crash a render: an unknown id is shown as it came.
    expect(goodsName(table, "mystery_thing")).toBe("mystery_thing");
    expect(goodsName(null, "iron_ore")).toBe("iron_ore");
    expect(tierName(table, "fine")).toBe("отличное");
    expect(tierName(table, "скверное")).toBe("скверное");
  });

  it("renders an id-composed flavor token by token, old flavors verbatim", () => {
    expect(flavorText(table, "soup · beans")).toBe("Суп · Бобы");
    expect(flavorText(table, "суп — бобы")).toBe("суп — бобы");
  });

  it("names a crop by the plants domain, a bred cultivar's key raw", () => {
    expect(plantName(table, "spelt")).toBe("Полба");
    //: A bred cultivar's agrotech is keyed by its variety id: no bundle names
    //: it, and the raw key must come through rather than crash the render.
    expect(plantName(table, "0f8b3a52-aaaa-bbbb-cccc-000000000000")).toBe(
      "0f8b3a52-aaaa-bbbb-cccc-000000000000",
    );
  });

  it("splits a carrier's counter key and keeps old keys whole", () => {
    expect(goodsKeyName(table, "recorded_recipe: soup")).toBe("Рецепт: Суп");
    expect(goodsKeyName(table, "iron_ore")).toBe("Железная руда");
    //: A stored Russian key from before the rename passes through verbatim.
    expect(goodsKeyName(table, "Рецепт: Стекло")).toBe("Рецепт: Стекло");
  });
});

describe("grounds", () => {
  //: The words of a ground are the **server's**: they name members of
  //: `PostingReason`, and the backend's own suite checks that none is left
  //: without one. So they arrive over the wire like every other sentence the
  //: engine says, and the test speaks them the same way a session does.
  const SERVED = import.meta.glob("../../../backend/locales/ru/money.ftl", {
    query: "?raw",
    import: "default",
    eager: true,
  }) as Record<string, string>;

  //: Checked out on its own, the client has no backend beside it and the glob
  //: comes back empty. Say so and skip, the way the backend twin of this test
  //: does (`test_ledger.py`) -- an empty bundle would fail these two with a
  //: diff that says «tax_land is not земельный налог» and nothing about why.
  const served = Object.values(SERVED).join("\n");

  beforeEach(() => learn(new Words({ locale: "ru", locales: ["ru"], ftl: served }, null)));
  afterEach(() => forget());

  it.skipIf(!served)("says the ground in words", () => {
    expect(groundName("tax_land")).toBe("земельный налог");
    expect(groundName("court_fee")).toBe("пошлина суда");
  });

  //: A server newer than the client is no reason to drop the line: a key reads
  //: worse than a word and better than a blank.
  it("leaves an unknown ground as it came", () => {
    expect(groundName("свежее_основание")).toBe("свежее_основание");
  });
});

describe("polls", () => {
  //: The words are the client's own, and they are shared by the two windows
  //: that draw a ballot -- so they are read here the way a session reads them,
  //: from the very file the bundle is built out of.
  const SAID = import.meta.glob("../locales/ru/city.ftl", {
    query: "?raw",
    import: "default",
    eager: true,
  }) as Record<string, string>;
  const said = Object.values(SAID).join("\n");

  const poll = (over: Partial<CityVote>): CityVote =>
    ({
      id: "p",
      kind: "law",
      voters: "citizens",
      value: "7",
      candidates: [],
      closes_at: "2026-09-03T00:00:00Z",
      threshold: "simple",
      quorum: 0,
      electorate: 9,
      yes: 0,
      no: 0,
      may_vote: true,
      ...over,
    }) as CityVote;

  beforeEach(() => learn(new Words({ locale: "ru", locales: ["ru"], ftl: said }, null)));
  afterEach(() => forget());

  it.skipIf(!said)("counts a law for and against, an election by turnout", () => {
    //: An election has no "against": every ballot in one names a candidate,
    //: and «против 0» would read as "nobody objects".
    expect(pollTally(poll({ yes: 4, no: 2 }))).toBe("за 4 · против 2 · из 9");
    expect(pollTally(poll({ kind: "election", yes: 4 }))).toBe("проголосовало 4 из 9");
  });

  it.skipIf(!said)("says the bar, and the quorum only where there is one", () => {
    expect(pollThreshold(poll({ threshold: "two_thirds" }))).toBe("две трети");
    expect(pollThreshold(poll({ quorum: 60 }))).toBe("простое большинство · кворум 60%");
  });

  //: The charter's options live in the vault and may outgrow the client's map
  //: (D-094): a key reads worse than a word and better than a blank.
  it("shows a threshold it does not know as it came", () => {
    expect(pollThreshold(poll({ threshold: "три четверти" as never }))).toBe("три четверти");
  });
});

describe("market", () => {
  const book = {
    bulk: [],
    liquid: ["water", "spirit"],
    materials: [
      { name: "Железная руда", id: "iron_ore" },
      { name: "Вода", id: "water" },
      { name: "Биопринтер Предтеч", id: "precursor_bioprinter", relic: true },
    ],
    units: {},
    operations: [],
    recipes: [
      { name: "Хлеб", id: "bread" },
      { name: "Спирт", id: "spirit" },
    ],
    classes: {},
    tool_classes: {},
    synonyms: {},
  } as unknown as RecipeBook;

  it("prints money to the last minor unit", () => {
    //: A bid of one minor unit is a real bid: printed as "0" beside a live
    //: button it would be a lie about what the button does.
    expect(coins(1)).toBe("0.0001");
    expect(coins(10)).toBe("0.001");
    expect(coins(30000)).toBe("3");
    expect(coins(0)).toBe("0");
    expect(coins(12345)).toBe("1.2345");
  });

  it("keeps out of the catalogue what an order could never be filled with", () => {
    const ids = catalogue(book);
    expect(ids).toContain("iron_ore");
    expect(ids).toContain("bread");
    //: A relic is found, never made or carried (D-232), so an order for one
    //: would hold money until it expires. It is the only kind left out.
    expect(ids).not.toContain("precursor_bioprinter");
  });

  //: Kept out until D-255 for a reason that has gone: a liquid lives only in a
  //: vessel (D-230), and the terminal is now a vessel of its own.
  it("offers a liquid: the terminal has a tank to hold it", () => {
    const ids = catalogue(book);
    expect(ids).toContain("water");
    expect(ids).toContain("spirit");
  });

  it("orders the ids by their Russian display words", () => {
    //: Хлеб < Железная руда is wrong; Вода, Железная руда, Спирт, Хлеб is the
    //: Russian order the picker reads in, whatever the ids spell.
    const names = {
      goods: { iron_ore: "Железная руда", bread: "Хлеб", water: "Вода", spirit: "Спирт" },
    } as never;
    expect(catalogue(book, names)).toEqual(["water", "iron_ore", "spirit", "bread"]);
  });

  it("answers with an empty catalogue before the book arrives", () => {
    expect(catalogue(null)).toEqual([]);
  });

  it("shows a quantity as it is, whole or fractional", () => {
    expect(exactly(4)).toBe("4");
    expect(exactly(0.5)).toBe("0.5");
    expect(exactly(0)).toBe("0");
  });
});
