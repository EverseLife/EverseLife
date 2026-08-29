// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The console's own arithmetic: what a move demands of the tanks, and how long
 * the air lasts. Both are numbers the client works out for itself from what the
 * server already said (D-226), so both are worth pinning here.
 */

import { describe, expect, it } from "vitest";
import { autonomy, wanted, type Air, type Price } from "../panels/ship/model";

const price = (over: Partial<Price> = {}): Price => ({
  hours: 4,
  fuel: 10,
  needs: 18,
  reachable: true,
  ...over,
});

describe("wanted", () => {
  it("asks for the whole demand, not what the leg burns", () => {
    //: `needs` is the climb **and** the descent home: an orbit has no bunker,
    //: and the engine refuses the order without both (D-245).
    expect(wanted(price())).toBe(18);
  });

  it("asks for nothing where there is nothing offered", () => {
    //: No move on the board -- no arithmetic to do, and the engine names the
    //: figure in its own refusal.
    expect(wanted(null)).toBe(0);
    expect(wanted(price({ needs: null }))).toBe(0);
  });
});

describe("autonomy", () => {
  const air = (over: Partial<Air> = {}): Air => ({
    units: 120,
    water: 40,
    sealed: true,
    per_hour: -10,
    at: "2026-08-29T00:00:00+00:00",
    ...over,
  });

  it("divides the reserve by what the crew breathes", () => {
    expect(autonomy(air())).toBe(12);
  });

  it("counts no hours where the air is not being spent", () => {
    //: At a pier under a sky that has air the hatch may as well be open, and a
    //: hull that makes as much as it breathes never runs down.
    expect(autonomy(air({ sealed: false }))).toBeNull();
    expect(autonomy(air({ per_hour: 0 }))).toBeNull();
    expect(autonomy(air({ per_hour: 3 }))).toBeNull();
  });
});
