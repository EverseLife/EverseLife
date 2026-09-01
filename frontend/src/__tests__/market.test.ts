// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** What the market panel offers and how it reads quality (D-239): pure module. */

import { describe, expect, it } from "vitest";

import type { RecipeBook } from "../api";
import type { Order } from "../api";
import { catalogue, floorOf, freeOnCounter, openAt, shareFree, tierOf } from "../market";

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

  it("keeps out a relic: nobody makes it, takes it down or carries it away", () => {
    expect(catalogue(shelf)).not.toContain("ТЭЦ Предтеч");
  });

  //: Out until D-255, in since: the terminal grew a tank, and the cells behind
  //: the counter are its inside.
  it("offers a liquid: it trades out of the terminal's tank", () => {
    expect(catalogue(shelf)).toContain("Вода");
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

describe("what of the counter is free", () => {
  //: One counter, one node, one player: the shelf as `look.stall` gives it and
  //: the orders as `orders` does.
  const shelf = [
    { goods: "iron_ore", tier: "хорошее", amount: 10 },
    { goods: "iron_ore", tier: "обычное", amount: 4 },
  ];
  const order = (
    over: Partial<Pick<Order, "side" | "node_key" | "goods" | "tier" | "left">> = {},
  ): Pick<Order, "side" | "node_key" | "goods" | "tier" | "left"> => ({
    side: "sell",
    node_key: "market",
    goods: "iron_ore",
    tier: "хорошее",
    left: 6,
    ...over,
  });

  it("counts the whole shelf where nothing stands under an order", () => {
    expect(freeOnCounter(shelf, [], "market", "iron_ore", "хорошее")).toBe(10);
  });

  it("takes off what one's own order holds", () => {
    expect(freeOnCounter(shelf, [order()], "market", "iron_ore", "хорошее")).toBe(4);
  });

  it("holds a tier at a time: another tier's order frees nothing here", () => {
    expect(freeOnCounter(shelf, [order({ tier: "обычное" })], "market", "iron_ore", "хорошее")).toBe(
      10,
    );
  });

  it("counts orders of this node alone: a lot listed elsewhere lies elsewhere", () => {
    expect(freeOnCounter(shelf, [order({ node_key: "port" })], "market", "iron_ore", "хорошее")).toBe(
      10,
    );
  });

  it("does not count a bid: money is frozen under it, not goods", () => {
    expect(freeOnCounter(shelf, [order({ side: "buy" })], "market", "iron_ore", "хорошее")).toBe(10);
  });

  //: A lot may be sold out from under an order between two reads; the shelf
  //: must not go negative and offer a "Забрать" of minus two.
  it("never falls below nothing", () => {
    expect(freeOnCounter(shelf, [order({ left: 99 })], "market", "iron_ore", "хорошее")).toBe(0);
  });
});

describe("the free part laid out over the rows", () => {
  //: Two stacks of one pair -- the shape the shelf takes when ore of two
  //: qualities inside one tier is loaded twice.
  const shelf = [
    { id: "a", key: "iron_ore", goods: "iron_ore", tier: "хорошее", amount: 6, quality: 71 },
    { id: "b", key: "iron_ore", goods: "iron_ore", tier: "хорошее", amount: 6, quality: 62 },
    { id: "c", key: "clay", goods: "clay", tier: "обычное", amount: 3, quality: 45 },
  ];

  it("gives one stack what it holds and the next what is left", () => {
    const free = shareFree(shelf, (goods) => (goods === "iron_ore" ? 8 : 3));
    //: The worse of the pair first: that is the one a take moves.
    expect(free.get("b")).toBe(6);
    expect(free.get("a")).toBe(2);
    expect(free.get("c")).toBe(3);
  });

  //: The shelf arrives unordered, and the answer must not depend on that:
  //: the same stacks in the other order must free the same ones.
  it("reads the shelf in the engine's order, not the shelf's", () => {
    const shuffled = [shelf[1], shelf[2], shelf[0]];
    const free = shareFree(shuffled, (goods) => (goods === "iron_ore" ? 8 : 3));
    expect(free.get("b")).toBe(6);
    expect(free.get("a")).toBe(2);
  });

  //: A thing with no quality at all -- a coin, a seed -- goes first, the way
  //: `nulls_first` puts it in the engine.
  it("puts the unqualified before the qualified", () => {
    const mixed = [
      { id: "p", key: "coin", goods: "coin", tier: "скверное", amount: 2 },
      { id: "q", key: "coin", goods: "coin", tier: "скверное", amount: 2, quality: 50 },
    ];
    const free = shareFree(mixed, () => 2);
    expect(free.get("p")).toBe(2);
    expect(free.get("q")).toBe(0);
  });

  it("never lets a pair's rows add up to more than the pair has free", () => {
    const free = shareFree(shelf, () => 4);
    expect((free.get("a") ?? 0) + (free.get("b") ?? 0)).toBe(4);
  });

  it("leaves every row at nothing when the whole pair is under an order", () => {
    const free = shareFree(shelf, (goods) => (goods === "iron_ore" ? 0 : 3));
    expect(free.get("a")).toBe(0);
    expect(free.get("b")).toBe(0);
  });

  //: The counter's own name for a stack, not the item's: a written carrier is
  //: a position per recipe (D-209), and two of them are two pairs.
  it("keeps carriers of different recipes apart", () => {
    const carriers = [
      { id: "x", key: "recipe: glass", goods: "recipe", tier: "обычное", amount: 1 },
      { id: "y", key: "recipe: rope", goods: "recipe", tier: "обычное", amount: 1 },
    ];
    const free = shareFree(carriers, (goods) => (goods === "recipe: glass" ? 1 : 0));
    expect(free.get("x")).toBe(1);
    expect(free.get("y")).toBe(0);
  });
});

describe("the tier a name opens at", () => {
  const tiers = [
    { from: 0, to: 19, name: "скверное" },
    { from: 20, to: 39, name: "плохое" },
    { from: 40, to: 59, name: "обычное" },
    { from: 60, to: 79, name: "хорошее" },
    { from: 80, to: 100, name: "отличное" },
  ];
  const near = [
    { goods: "iron_ore", tier: "хорошее" },
    { goods: "iron_ore", tier: "обычное" },
    { goods: "clay", tier: "плохое" },
  ];

  it("keeps the tier being looked at when this name trades in it", () => {
    const looking = { goods: "clay", tier: "хорошее" };
    expect(openAt("iron_ore", near, [], looking, tiers)).toBe("хорошее");
  });

  //: The books are matched by tier exactly (D-058): opening bread at the tier
  //: the ore was read in would write an order into a book nobody deals in.
  it("falls to what this name actually trades in", () => {
    const looking = { goods: "iron_ore", tier: "отличное" };
    expect(openAt("clay", near, [], looking, tiers)).toBe("плохое");
  });

  //: A liquid is in no list of stacks before it is poured -- it is inside a
  //: canister (D-230) -- so what the hands hold is the only witness to its
  //: tier, and it has one like everything else (a crafted spirit its batch's).
  it("opens a liquid at the tier the vessels hold it in", () => {
    const looking = { goods: "iron_ore", tier: "хорошее" };
    expect(openAt("spirit", near, ["отличное"], looking, tiers)).toBe("отличное");
  });

  //: The vessels come off an unordered read, so the answer must not depend on
  //: which tier the heap happened to hand over first.
  it("takes the worst of several held tiers, whichever order they arrive in", () => {
    expect(openAt("spirit", near, ["отличное", "плохое"], null, tiers)).toBe("плохое");
    expect(openAt("spirit", near, ["плохое", "отличное"], null, tiers)).toBe("плохое");
  });

  it("puts a band the ladder does not know last, not first", () => {
    expect(openAt("spirit", near, ["невиданное", "хорошее"], null, tiers)).toBe("хорошее");
  });

  it("prefers the counter to the pocket: what trades here wins", () => {
    const traded = [...near, { goods: "spirit", tier: "обычное" }];
    expect(openAt("spirit", traded, ["отличное"], null, tiers)).toBe("обычное");
  });

  it("falls back to the middle band for a name nobody has and nobody trades", () => {
    expect(openAt("bread", near, [], null, tiers)).toBe("обычное");
  });
});
