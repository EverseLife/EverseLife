// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The heat reserve counted on the client (D-231).
 *
 * The same arithmetic exists on the server (`engine.frost`), and the two must
 * agree: the number the player watches falling is the number the server will
 * charge them by.
 */

import { describe, expect, it } from "vitest";

import type { Frost } from "../api";
import { reserveAt } from "../warmth";

const AT = Date.parse("2026-08-25T12:00:00Z");
const HOUR = 3_600_000;

const cold: Frost = {
  climate: "frost",
  warm: false,
  hours: 6,
  at: "2026-08-25T12:00:00Z",
  per_hour: -1,
  max: 6,
};

const warm: Frost = { ...cold, warm: true, hours: 0, per_hour: 3 };

describe("теплозапас", () => {
  it("тает час за час в холодном узле", () => {
    expect(reserveAt(cold, AT)).toBe(6);
    expect(reserveAt(cold, AT + 2 * HOUR)).toBe(4);
  });

  it("не уходит ниже нуля: замёрзший замёрз, глубже некуда", () => {
    expect(reserveAt(cold, AT + 10 * HOUR)).toBe(0);
  });

  it("восполняется в тёплом узле и стоит на потолке", () => {
    expect(reserveAt(warm, AT + HOUR)).toBe(3);
    expect(reserveAt(warm, AT + 100 * HOUR)).toBe(6);
  });

  it("не растёт назад во времени: отметка сервера — не будущее", () => {
    expect(reserveAt(cold, AT - HOUR)).toBe(6);
  });
});
