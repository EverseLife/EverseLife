// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The building's places (D-106, D-278): pure arithmetic over the look, kept
 * apart from the panels so the rule is testable without a window.
 */

import type { Look } from "./api";

/**
 * Whether the viewer may put a machine up here right now (D-278): the place
 * is theirs to furnish, a roof stands to put it under (D-106), and a place is
 * left in the building. The server refuses the same three; the button is not
 * offered where the click would only collect the refusal.
 */
export function mayInstall(look: Look): boolean {
  const floor = look.floor;
  if (!floor?.mine || floor.space.area <= 0) return false;
  return floor.space.slots_used < floor.space.slots;
}
