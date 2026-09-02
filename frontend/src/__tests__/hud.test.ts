// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** A pane's fold is remembered, and forgotten cleanly (brief, desktop layout). */

import { afterEach, describe, expect, it, vi } from "vitest";

import { folded, rememberFolded } from "../hud";

//: The tests run in node, where there is no storage: a map stands in for it.
function storage(): Storage {
  const kept = new Map<string, string>();
  return {
    getItem: (key: string) => kept.get(key) ?? null,
    setItem: (key: string, value: string) => void kept.set(key, value),
    removeItem: (key: string) => void kept.delete(key),
    clear: () => kept.clear(),
    key: () => null,
    get length() {
      return kept.size;
    },
  };
}

describe("a pane's fold", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is open until folded, and the fold is remembered", () => {
    const kept = storage();
    vi.stubGlobal("localStorage", kept);
    expect(folded("sidebar")).toBe(false);
    rememberFolded("sidebar", true);
    expect(folded("sidebar")).toBe(true);
    //: Each pane on its own: folding one does not fold the other.
    expect(folded("chat")).toBe(false);
    rememberFolded("sidebar", false);
    expect(folded("sidebar")).toBe(false);
    //: Unfolded is the default, so it leaves no key behind.
    expect(kept.length).toBe(0);
  });

  it("stays open in a browser without storage", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("no storage");
      },
      setItem: () => {
        throw new Error("no storage");
      },
    });
    expect(folded("chat")).toBe(false);
    expect(() => rememberFolded("chat", true)).not.toThrow();
  });
});
