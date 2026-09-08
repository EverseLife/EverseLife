// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** The sky's own arithmetic as the map words it: terms, arcs, mooring and the
 *  ejection window (`panels/map/orbits.ts`, D-271, D-289, D-316). Cut out of
 *  `map.test.ts` on 2026-09-08 to bring that file back under the 800-line bar. */

import { describe, expect, it } from "vitest";

import { along, forecast, mooring, term, windowOpen } from "../panels/map/orbits";
import { DEFAULT_LOCALE, Words, learn } from "../locale";

//: Terms are spelled from the client's own locale (D-251).
learn(new Words({ locale: DEFAULT_LOCALE, locales: [DEFAULT_LOCALE], ftl: "" }, null));

describe("orbits", () => {
  it("counts a term in hours up to a day and in days after it", () => {
    expect(term(3.25)).toBe("3.3 ч");
    expect(term(12)).toBe("12 ч");
    //: Rounded before the unit is chosen: the other way round 23.9 gave "24 ч"
    //: and 24 gave "1.0 сут", the earlier term reading as the longer one.
    expect(term(23.9)).toBe("1.0 сут");
    expect(term(23.4)).toBe("23 ч");
    expect(term(24)).toBe("1.0 сут");
    expect(term(36)).toBe("1.5 сут");
  });

  //: The corridor's calendar is the engine's (D-271): the client leafs to the
  //: day shown and reads the cheapest arc off it, never recomputes it.
  it("leafs the calendar to the day shown and clamps at its ends", () => {
    const route = {
      a: "terra",
      b: "pyroxis",
      days: [
        { day: 10, dv: 30, hours: 200 },
        { day: 11, dv: 12, hours: 240 },
        { day: 12, dv: 20, hours: 220 },
      ],
    } as never;
    expect(forecast(route, 11.4)?.dv).toBe(12);
    expect(forecast(route, 3)?.day).toBe(10);
    expect(forecast(route, 99)?.day).toBe(12);
    expect(forecast({ a: "a", b: "b", days: [] } as never, 11)).toBeUndefined();
  });

  it("calls the window open within a tenth of the spread above the dip", () => {
    const route = {
      a: "terra",
      b: "pyroxis",
      days: [
        { day: 0, dv: 30, hours: 1 },
        { day: 1, dv: 12, hours: 1 },
        { day: 2, dv: 13, hours: 1 },
        { day: 3, dv: 20, hours: 1 },
      ],
    } as never;
    expect(windowOpen(route, 1)).toBe(true);
    expect(windowOpen(route, 2)).toBe(true);
    expect(windowOpen(route, 3)).toBe(false);
  });

  //: The arc's points are at equal time steps: a share of the time is a
  //: place on the polyline, interpolated inside its segment.
  it("finds the place along an arc by the share of the time gone", () => {
    const arc: [number, number][] = [
      [0, 0],
      [10, 0],
      [10, 10],
    ];
    expect(along(arc, 0)).toEqual([0, 0]);
    expect(along(arc, 0.25)).toEqual([5, 0]);
    expect(along(arc, 0.5)).toEqual([10, 0]);
    expect(along(arc, 1)).toEqual([10, 10]);
    expect(along(arc, 2)).toEqual([10, 10]);
    expect(along([[3, 4]], 0.5)).toEqual([3, 4]);
  });

  //: A ship's mooring is its own and does not move: otherwise the hull would
  //: jump around its planet on every reread of the map.
  it("gives a ship a steady mooring somewhere on the circle", () => {
    expect(mooring("ship.node.abc")).toBe(mooring("ship.node.abc"));
    expect(mooring("ship.node.abc")).not.toBe(mooring("ship.node.abd"));
    for (const key of ["a", "ship.node.abc", "", "длинный ключ"]) {
      expect(mooring(key)).toBeGreaterThanOrEqual(0);
      expect(mooring(key)).toBeLessThan(Math.PI * 2);
    }
  });
});
