// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** View settings survive a reload, and survive a browser that lies to them. */

import { afterEach, describe, expect, it, vi } from "vitest";

import { FLAG, KEYS, NAMED, UNFLAG, WHOLE, forgetKept, keep, kept, oneOf } from "../kept";

//: The tests run in node, where there is no storage: a map stands in for it.
function storage(): Storage {
  const box = new Map<string, string>();
  return {
    getItem: (key: string) => box.get(key) ?? null,
    setItem: (key: string, value: string) => void box.set(key, value),
    removeItem: (key: string) => void box.delete(key),
    clear: () => box.clear(),
    key: (at: number) => [...box.keys()][at] ?? null,
    get length() {
      return box.size;
    },
  };
}

type Tab = "me" | "goods";

describe("a remembered view setting", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("comes back as it was left", () => {
    const box = storage();
    vi.stubGlobal("localStorage", box);
    const TABS = oneOf<Tab>(["me", "goods"]);
    expect(kept("everselife.sidebar.tab", "me", TABS)).toBe("me");
    keep("everselife.sidebar.tab", "goods", TABS);
    expect(kept("everselife.sidebar.tab", "me", TABS)).toBe("goods");
  });

  it("opens on the default when the word is no longer one of the choices", () => {
    const box = storage();
    vi.stubGlobal("localStorage", box);
    //: What an older build wrote: a tab that has since been taken out.
    box.setItem("everselife.sidebar.tab", "circles");
    expect(kept("everselife.sidebar.tab", "me", oneOf<Tab>(["me", "goods"]))).toBe("me");
  });

  it("leaves no key behind for a flag at its default", () => {
    const box = storage();
    vi.stubGlobal("localStorage", box);
    keep("everselife.chat.quiet", true, FLAG);
    expect(box.length).toBe(1);
    keep("everselife.chat.quiet", false, FLAG);
    expect(box.length).toBe(0);
    expect(kept("everselife.chat.quiet", false, FLAG)).toBe(false);
  });

  it("lets a flag whose default is yes be turned off", () => {
    const box = storage();
    vi.stubGlobal("localStorage", box);
    //: The trap `UNFLAG` exists for: under `FLAG` a deliberate "loose" writes
    //: nothing and reads back as the default "tied".
    keep("everselife.map.tethered", false, FLAG);
    expect(kept("everselife.map.tethered", true, FLAG)).toBe(true);
    keep("everselife.map.tethered", false, UNFLAG);
    expect(kept("everselife.map.tethered", true, UNFLAG)).toBe(false);
    keep("everselife.map.tethered", true, UNFLAG);
    expect(box.length).toBe(0);
  });

  it("keeps a set of names, and forgets an empty one", () => {
    const box = storage();
    vi.stubGlobal("localStorage", box);
    const key = "everselife.inventory.opened.tier";
    keep(key, new Set(["good", "plain"]), KEYS);
    expect([...kept(key, new Set<string>(), KEYS)]).toEqual(["good", "plain"]);
    keep(key, new Set<string>(), KEYS);
    expect(box.length).toBe(0);
  });

  it("keeps a name per place, and refuses a shape that is not one", () => {
    const box = storage();
    vi.stubGlobal("localStorage", box);
    const key = "everselife.stand.opened";
    keep(key, { yard: "forge" }, NAMED);
    expect(kept(key, {}, NAMED)).toEqual({ yard: "forge" });
    //: Half a shape is no shape: a number where a name belongs is an older
    //: build's, or somebody's hand in the console.
    box.setItem(key, JSON.stringify({ yard: 7 }));
    expect(kept(key, {}, NAMED)).toEqual({});
    box.setItem(key, "{not json");
    expect(kept(key, {}, NAMED)).toEqual({});
  });

  it("refuses a number that is not whole, and the shapes Number() would take", () => {
    const box = storage();
    vi.stubGlobal("localStorage", box);
    keep("everselife.market.step", 25, WHOLE);
    expect(kept("everselife.market.step", null, WHOLE)).toBe(25);
    //: `Number` happily reads most of these -- 0, 0, 64, 1000, 2.5. A step is
    //: spelled in digits or it is not a step.
    for (const written of ["two", "", " ", "0x40", "1e3", "2.5"]) {
      box.setItem("everselife.market.step", written);
      expect(kept("everselife.market.step", null, WHOLE)).toBe(null);
    }
    keep("everselife.market.step", null, WHOLE);
    expect(box.length).toBe(0);
  });

  it("drops what names a world at logout, and keeps the arrangement", () => {
    const box = storage();
    vi.stubGlobal("localStorage", box);
    box.setItem("everselife.token", "a-token");
    box.setItem("everselife.stand.opened", '{"terra.capital.core":"house"}');
    box.setItem("everselife.inventory.opened.kind", '["raw"]');
    box.setItem("everselife.inventory.opened.maker", '["Somebody"]');
    box.setItem("everselife.inventory.arrange", '{"group":"kind"}');
    box.setItem("everselife.sidebar.tab", "goods");
    box.setItem("everselife.density", "dense");
    forgetKept("everselife.stand.", "everselife.inventory.opened");
    expect(box.length).toBe(4);
    expect(box.getItem("everselife.stand.opened")).toBe(null);
    expect(box.getItem("everselife.inventory.opened.kind")).toBe(null);
    //: Every match goes, not every other one: removing while walking by index
    //: is the classic way to leave half of them behind.
    expect(box.getItem("everselife.inventory.opened.maker")).toBe(null);
    //: The axes are an arrangement, not a world: they stay.
    expect(box.getItem("everselife.inventory.arrange")).toBe('{"group":"kind"}');
    expect(box.getItem("everselife.sidebar.tab")).toBe("goods");
    expect(box.getItem("everselife.density")).toBe("dense");
  });

  it("opens on its defaults in a browser without storage", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("no storage");
      },
      setItem: () => {
        throw new Error("no storage");
      },
      removeItem: () => {
        throw new Error("no storage");
      },
    });
    expect(kept("everselife.chat.quiet", false, FLAG)).toBe(false);
    expect(kept("everselife.map.tethered", true, UNFLAG)).toBe(true);
    expect(() => keep("everselife.chat.quiet", true, FLAG)).not.toThrow();
  });
});
