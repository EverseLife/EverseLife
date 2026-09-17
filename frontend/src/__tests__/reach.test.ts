// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** What is at hand for a work: the tiers of a thing, the count of it, and the
 *  reach it is counted over (D-058, D-315, D-344). Cut out of `pure.test.ts`
 *  when that file passed the 800-line bar; the modules are as pure as the
 *  ones tested there -- no React, no socket. */

import { describe, expect, it } from "vitest";

import type { Thing } from "../api";
import { stockOf, tierLabel, tiersOf } from "../tiers";
import { reachOf } from "../wire/look";

const thing = (over: Partial<Thing>): Thing =>
  ({ id: "x", goods: "Руда", amount: 1, tier: "обычное", mass: 1, condition: 100, ...over }) as Thing;

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
        goods: "canister",
        amount: 1,
        content: [thing({ goods: "water", amount: 12 })],
      }),
      thing({ goods: "canister", amount: 1, content: [thing({ goods: "water", amount: 3 })] }),
      thing({ goods: "canister", amount: 1, content: [] }),
      thing({ goods: "water", amount: 1 }),
    ];
    expect(stockOf(held, "water")).toBe(16);
    //: The vessels themselves: only the empty one is material (D-344) -- the
    //: engine does not spend a canister with water in it, and a count of three
    //: would promise a batch it refuses.
    expect(stockOf(held, "canister")).toBe(1);
    expect(stockOf(held, "spirit")).toBe(0);
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

describe("the reach of a work", () => {
  //: What a batch may be counted from (D-315). The server sends no such list:
  //: it sends the pocket, the hold, the chests one may open and the two
  //: surfaces of the place, and the sum is this side's arithmetic (D-225).
  const at = (over: Partial<Thing>) => thing(over);
  const place = (mine: boolean) => ({
    inventory: [at({ id: "pocket", goods: "Руда", amount: 1 })],
    convoy: {
      id: "cart",
      type_key: "cart",
      condition: 100,
      capacity: 200,
      mass: 10,
      speed_k: 1.4,
      heavy: false,
      cargo: [at({ id: "hold", goods: "Руда", amount: 2 })],
    },
    storages: [
      { id: "chest", goods: "chest", capacity: 100, mass: 4, mine, content: [at({ id: "chest-ore", goods: "Руда", amount: 4 })] },
    ],
    floor: {
      space: { area: 20, used: 0, cargo_mass: 0, free: 20, slots: 2, slots_used: 0 },
      things: [at({ id: "floor", goods: "Руда", amount: 8 })],
      open: true,
      mine,
    },
    ground: {
      space: { area: 20, used: 0, cargo_mass: 0, free: 20 },
      things: [at({ id: "yard", goods: "Руда", amount: 16 })],
    },
  });

  it("adds the place to the hands where the place is ours", () => {
    expect(stockOf(reachOf(place(true) as never), "Руда")).toBe(1 + 2 + 4 + 8 + 16);
  });

  it("keeps the place out where it is somebody else's, and the hold in", () => {
    //: A guest is refused the host's chest and floor by the engine (D-181), and
    //: their own convoy is theirs wherever it stands: it walks with the body.
    expect(stockOf(reachOf(place(false) as never), "Руда")).toBe(1 + 2);
  });

  it("leaves the fuel plant its heap, and not the chest beside it", () => {
    //: The pile where a fuel plant stands is the plant's tank (D-189), and
    //: coal is an input of five recipes -- so this is the one node where a
    //: count over the visible stacks would promise what the batch refuses.
    //: The bar stops at the lid: a chest is not the plant's bunker.
    const book = {
      classes: { fuel_plant: ["coal_plant"] },
      constants: { "energy.fuel_energy": { coal: 4 } },
    } as never;
    const node = {
      inventory: [],
      bench: [{ id: "plant", goods: "coal_plant", condition: 100, busy: false, mine: true }],
      storages: [
        { id: "chest", goods: "chest", capacity: 100, mass: 4, mine: true, content: [at({ id: "in-chest", goods: "coal", amount: 7 })] },
      ],
      floor: {
        space: { area: 20, used: 0, cargo_mass: 0, free: 20, slots: 2, slots_used: 0 },
        things: [at({ id: "heap", goods: "coal", amount: 30 })],
        open: true,
        mine: true,
      },
    } as never;
    expect(stockOf(reachOf(node, book), "coal")).toBe(7);
    //: Without the book the sum is the wider one, and the forecast is the answer.
    expect(stockOf(reachOf(node), "coal")).toBe(37);
  });

  it("leaves out a chest lying here with something in it (D-344)", () => {
    //: The engine spends only an empty chest; a full one lying on the floor is
    //: a store, not material. Its window says what it weighs -- the lying
    //: chest is in `storages` as well as on the floor -- and that is how this
    //: side knows, since a dry chest's things have no `content` of their own.
    const node = {
      inventory: [],
      storages: [
        { id: "full", goods: "chest", capacity: 300, mass: 4, mine: true, content: [at({ id: "in-full", goods: "iron_ore", amount: 4 })] },
        { id: "bare", goods: "chest", capacity: 300, mass: 0, mine: true, content: [] },
      ],
      floor: {
        space: { area: 20, used: 0, cargo_mass: 0, free: 20, slots: 2, slots_used: 0 },
        things: [at({ id: "full", goods: "chest", amount: 1 }), at({ id: "bare", goods: "chest", amount: 1 })],
        open: true,
        mine: true,
      },
    } as never;
    expect(stockOf(reachOf(node), "chest")).toBe(1);
    //: What lies in the full one is still at hand: the work reaches into chests.
    expect(stockOf(reachOf(node), "iron_ore")).toBe(4);
  });

  it("counts nothing that is not there: no convoy, no chest, no house", () => {
    expect(stockOf(reachOf({ inventory: [at({ goods: "Руда", amount: 3 })] } as never), "Руда")).toBe(3);
  });
});
