// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The readings the wire types carry with them.
 *
 * These seven functions came out of `api.ts` when it was cut, and the cut is
 * what makes them testable: `api.ts` pulled in `host`, which reads
 * `window.location` while being evaluated, so nothing in it could be loaded in
 * node at all. `wire/*` takes only `locale` and types, and so it loads here.
 *
 * Each is a sentence about a shape the server sends: what a house is when the
 * node has no building, whose the land is, what stands in the node, what the
 * slow parts look like folded back into a `look`. They are pure, they are read
 * on every redraw, and until now none of them had ever been run outside a
 * browser.
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { compose, houseOf, isCivic, isMine, isWild, stationsOf } from "../wire/look";
import { spell } from "../wire/travel";
import type { Look, LiveLook, Parts } from "../wire/look";
import type { Names } from "../names";
import { forget, learn, Words } from "../locale";

/** A node with only what the reading under test looks at. */
const node = (over: Record<string, unknown> = {}) =>
  ({ key: "terra.yard", name: "Двор", ...over }) as unknown as Look["node"];

describe("houseOf", () => {
  it("answers with a whole house for a node that has no building", () => {
    //: Every panel that draws a house reads these seven fields without
    //: checking first. A missing building must therefore be zeroes, not
    //: `undefined` -- otherwise the arithmetic downstream reads `NaN`.
    expect(houseOf(node())).toEqual({
      area: 0, ground: 0, floors: 0, decay: 0, slots: 0, used: 0, sites: [],
    });
  });

  it("keeps what the building does say and fills only the rest", () => {
    const house = houseOf(node({ building: { area: 40, floors: 2, kind: "wooden" } }));
    expect(house.area).toBe(40);
    expect(house.floors).toBe(2);
    expect(house.kind).toBe("wooden");
    //: Untouched by the building, so still the floor value rather than absent.
    expect(house.ground).toBe(0);
    expect(house.sites).toEqual([]);
  });
});

describe("whose the land is", () => {
  it("is mine when the owner is me, and not when it is somebody else", () => {
    expect(isMine({ identity: "me", node: node({ owner: "me" }) })).toBe(true);
    expect(isMine({ identity: "me", node: node({ owner: "you" }) })).toBe(false);
  });

  it("is not mine when nobody owns it -- unowned is not the same as mine", () => {
    expect(isMine({ identity: "me", node: node({ owner: null }) })).toBe(false);
    expect(isMine({ identity: "me", node: undefined })).toBe(false);
  });

  it("is wild only with no owner and no city (D-198)", () => {
    expect(isWild(node({ owner: null, owner_city: null }))).toBe(true);
    expect(isWild(node({ owner: "you", owner_city: null }))).toBe(false);
    expect(isWild(node({ owner: null, owner_city: "Ferrum" }))).toBe(false);
    expect(isWild(undefined)).toBe(false);
  });

  it("is civic while the city holds it, bought or not", () => {
    expect(isCivic(node({ owner_city: "Ferrum" }))).toBe(true);
    //: Sold to a person and still the city's land: the two are not exclusive,
    //: which is exactly why they are two readings and not one.
    expect(isCivic(node({ owner: "me", owner_city: "Ferrum" }))).toBe(true);
    expect(isCivic(node({ owner_city: null }))).toBe(false);
  });
});

describe("stationsOf", () => {
  const look = {
    bench: [{ goods: "forge" }, { goods: "lathe" }],
    furniture: [{ goods: "chest" }, { goods: "forge" }],
  } as unknown as Pick<Look, "bench" | "furniture">;

  it("names every kind once, benches and furniture together", () => {
    //: `forge` stands in both lists; a player sees one machine, not two.
    expect(stationsOf(look)).toEqual(["chest", "forge", "lathe"]);
  });

  it("sorts by the word the player reads, not by the id underneath", () => {
    //: Only the domain this reading looks in; the other nine are not its
    //: business, so the cast goes through `unknown` rather than pretending
    //: a partial table is a whole one.
    const names = {
      goods: { forge: "Кузница", lathe: "Ампер", chest: "Ящик" },
    } as unknown as Names;
    //: By id the order is chest, forge, lathe; by the Russian words it is
    //: Ампер, Кузница, Ящик -- so this fails if the sort forgets the names.
    expect(stationsOf(look, names)).toEqual(["lathe", "forge", "chest"]);
  });

  it("says nothing about an empty node", () => {
    expect(stationsOf({} as Pick<Look, "bench" | "furniture">)).toEqual([]);
  });
});

describe("compose", () => {
  const parts = {
    knowledge: { knows: ["forge"], discovered: [] },
    profile: { name: "Kira" },
    orders: { orders: [] },
    deeds: [],
    shelf: [],
  } as unknown as Parts;

  it("folds the slow parts back into the live look", () => {
    const live = { identity: "me", node: node() } as unknown as LiveLook;
    const whole = compose(live, parts);
    expect(whole.identity).toBe("me");
    expect(whole.profile).toEqual({ name: "Kira" });
    expect(whole.knows).toEqual(["forge"]);
  });

  it("leaves the node alone when the shelf is empty", () => {
    //: An empty shelf is not a shelf of nothing: a node with no library must
    //: keep the object it already had, or every redraw makes a new one and
    //: the panels below re-render for no reason.
    const live = { identity: "me", node: node() } as unknown as LiveLook;
    expect(compose(live, parts).node).toBe(live.node);
  });

  it("puts the shelf on the node when there is one", () => {
    const live = { identity: "me", node: node() } as unknown as LiveLook;
    const shelved = { ...parts, shelf: [{ recipe: "forge" }] } as unknown as Parts;
    expect(compose(live, shelved).node).not.toBe(live.node);
    expect((compose(live, shelved).node as { shelf: unknown[] }).shelf).toHaveLength(1);
  });
});

describe("spell", () => {
  //: It draws its units from the locale, so it needs a language to speak.
  beforeEach(() => learn(new Words({ locale: "ru", locales: ["ru"], ftl: "" }, null)));
  //: Put the language back, so a later file does not inherit this one's.
  afterEach(() => forget());

  it("carries up to the next unit before choosing it", () => {
    //: 59.7 s is a minute, not "60 s": the rounding happens before the unit
    //: is picked, the same carry `clock.duration` takes.
    expect(spell(59.7)).toBe("1 мин");
    expect(spell(59)).toBe("59 с");
  });

  it("counts in seconds, minutes and hours as the term grows", () => {
    expect(spell(0)).toBe("0 с");
    expect(spell(90)).toBe("2 мин");
    expect(spell(3600)).toBe("1.0 ч");
    expect(spell(5400)).toBe("1.5 ч");
  });

  it("writes a long term without a thousands separator", () => {
    //: The count goes in as a string on purpose: a term is read, not summed,
    //: and Fluent's own `NUMBER` would put a space inside "1200".
    expect(spell(1200 * 3600)).toBe("1200.0 ч");
  });
});
