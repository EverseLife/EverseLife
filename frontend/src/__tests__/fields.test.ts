// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The number box's rule (`fields.ts`), which stands under some thirty fields:
 * the market's volume and price, the bank's sum, the mint's count, every
 * "how much" of a stack. The branch that draws the value over the draft is a
 * silent one -- it eats what a hand is typing -- so all three are pinned here.
 */

import { describe, expect, it } from "vitest";

import { shownNumber, typedNumber } from "../fields";

describe("typedNumber", () => {
  it("reads a number", () => {
    expect(typedNumber("123")).toBe(123);
    expect(typedNumber("0")).toBe(0);
    expect(typedNumber("1.5")).toBe(1.5);
  });

  it("calls an empty box empty, not zero", () => {
    expect(typedNumber("")).toBeNull();
    expect(typedNumber("   ")).toBeNull();
  });

  it("calls a half-typed number empty: the box holds none yet", () => {
    //: What a browser reports for "-" or "1e" is the empty string; anything
    //: else that is not a number is the same answer.
    expect(typedNumber("-")).toBeNull();
    expect(typedNumber("abc")).toBeNull();
  });
});

describe("shownNumber", () => {
  it("shows the number when nobody is typing", () => {
    expect(shownNumber(null, 12)).toBe("12");
    expect(shownNumber(null, 0)).toBe("0");
  });

  it("draws an empty box for no value at all", () => {
    expect(shownNumber(null, null)).toBe("");
  });

  it("leaves an emptied box empty -- the whole point", () => {
    //: The caller has folded the empty box into a zero, as most do. Drawn from
    //: the value the box would show "0" again, and the next keystroke would
    //: make "0123" out of a wanted 123.
    expect(shownNumber("", 0)).toBe("");
  });

  it("keeps the draft while it still means the number held", () => {
    expect(shownNumber("1.50", 1.5)).toBe("1.50");
    expect(shownNumber("007", 7)).toBe("007");
  });

  it("gives way to a value the caller moved -- a clamp is felt at once", () => {
    //: Typed 50 into a field whose stack is 10: the caller clamped, and the
    //: box must say 10 rather than go on showing a number nothing will honour.
    expect(shownNumber("50", 10)).toBe("10");
  });
});
