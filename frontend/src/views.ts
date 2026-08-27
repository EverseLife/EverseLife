// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The scene's tabs (D-050): map - location. A module of its own so the
 * header component exports components only (fast refresh) and the app shell
 * shares the one list. Circles left the scene for the chat strip (D-238):
 * they only decide who hears what is said, and that choice belongs beside
 * the saying.
 */

export const VIEWS = [
  { id: "map", label: "карта" },
  { id: "place", label: "локация" },
] as const;

export type View = (typeof VIEWS)[number]["id"];
