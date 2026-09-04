// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

import { describe, expect, it } from "vitest";
import { sameTarget } from "../panels/ship/model";

describe("the console's target (D-289, wave 3)", () => {
  it("tells a planet from a hull, and one hull from another", () => {
    expect(sameTarget({ planet: "aurora" }, { planet: "aurora" })).toBe(true);
    expect(sameTarget({ planet: "aurora" }, { planet: "terra" })).toBe(false);
    expect(sameTarget({ ship: "a" }, { ship: "a" })).toBe(true);
    expect(sameTarget({ ship: "a" }, { ship: "b" })).toBe(false);
    //: A planet and a hull are never the same thing, whatever their keys.
    expect(sameTarget({ planet: "a" }, { ship: "a" })).toBe(false);
  });

  it("treats nothing chosen as equal only to nothing chosen", () => {
    expect(sameTarget(null, null)).toBe(true);
    expect(sameTarget(null, { planet: "aurora" })).toBe(false);
    expect(sameTarget({ ship: "a" }, null)).toBe(false);
  });
});
