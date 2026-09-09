// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** How the map says a length, a price and the name of a thing with none
 *  (`panels/map/words.ts`). Cut out of `map.test.ts` on 2026-09-08: that file
 *  had grown past the project's 800-line bar, and these tests share nothing
 *  with the camera and the scene but the folder. */

import { describe, expect, it } from "vitest";

import { bulk, long, nameWord, nodeWord, price, provinceWord, spread } from "../panels/map/words";
import { DEFAULT_LOCALE, Words, learn } from "../locale";

//: The terms below are assembled from the client's own locale (D-251), which
//: ships with the build rather than over the wire -- so an empty bundle is
//: still a complete one here, and `spread(40, 120)` comes out in words.
learn(new Words({ locale: DEFAULT_LOCALE, locales: [DEFAULT_LOCALE], ftl: "" }, null));

describe("words", () => {
  //: The hour is the border between two units and both sides of it want their
  //: own: "60 мин" reads worse than "1 ч", and "0.7 ч" worse than "40 мин".
  it("keeps minutes up to the hour and goes over to hours after it", () => {
    expect(long(40)).toBe("40 мин");
    expect(long(59)).toBe("59 мин");
    expect(long(60)).toBe("1 ч");
    expect(long(90)).toBe("1.5 ч");
  });

  it("names the unit once while there is only one of them", () => {
    expect(spread(10, 40)).toBe("10–40 мин");
    expect(spread(60, 120)).toBe("1–2 ч");
    //: Across the border both ends are spelled whole, or it reads "40–2 ч".
    expect(spread(40, 120)).toBe("40 мин – 2 ч");
  });

  it("prints an empty spread as the term it is", () => {
    expect(spread(30, 30)).toBe("30–30 мин");
  });

  //: A step across town costs a fraction of a unit, and "0.0" would lie about
  //: it: there is a price.
  it("does not round the road's price down to nothing", () => {
    expect(price(0)).toBe("0");
    expect(price(0.02)).toBe("<0.1");
    expect(price(0.34)).toBe("0.3");
  });

  //: And a road worn by a hair asks for a hair of surface: «Подсыпать за 0»
  //: offered work for no price, which is a button nobody believes.
  it("does not round a price in materials down to nothing", () => {
    expect(bulk(0)).toBe("0");
    expect(bulk(0.4)).toBe("<1");
    expect(bulk(1)).toBe("1");
    expect(bulk(40.6)).toBe("41");
  });

  //: A found node has no name (D-321), and a heading, a menu or a leg of a
  //: walk with nothing in it reads as a breakage rather than as a node.
  it("calls a node with no name what it is", () => {
    expect(nameWord("Рынок")).toBe("Рынок");
    expect(nameWord("")).toBe("Безымянный узел");
    expect(nameWord(null)).toBe("Безымянный узел");
    expect(nameWord(undefined)).toBe("Безымянный узел");
  });

  //: And where the node itself is at hand, its kind is a better word than
  //: "безымянный" -- the same one the world's refusals use (D-321), so the
  //: column and the world do not call one node two things.
  it("says a found node's biome, in the vault's word", () => {
    const names = { forest: "Лес", desert: "Пустыня" };
    //: The province (landscape plan, wave 3): the vault's word by id, the id
    //: itself before the table arrives, nothing for a node without one.
    const provinces = { provinces: { ore_ridge: "Рудный кряж" } };
    expect(provinceWord({ province: "ore_ridge" }, provinces)).toBe("Рудный кряж");
    expect(provinceWord({ province: "ore_ridge" }, null)).toBe("ore_ridge");
    expect(provinceWord({}, provinces)).toBeNull();
    expect(nodeWord({ name: "Рынок", features: ["forest"] }, names)).toBe("Рынок");
    expect(nodeWord({ name: "", features: ["forest"] }, names)).toBe("Лес");
    //: Marks that are not biomes are passed over: a vein in the woods is
    //: still the woods by name, and the vein is the sign it wears.
    expect(nodeWord({ name: null, features: ["vein", "desert"] }, names)).toBe("Пустыня");
  });

  it("falls back to the general word when the kind is unknown or untold", () => {
    expect(nodeWord({ name: "", features: ["vein"] }, { forest: "Лес" })).toBe("Безымянный узел");
    expect(nodeWord({ name: "", features: ["forest"] }, undefined)).toBe("Безымянный узел");
    expect(nodeWord({}, { forest: "Лес" })).toBe("Безымянный узел");
  });
});
