// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The scene's tabs (D-050): map - location. A module of its own so the
 * header component exports components only (fast refresh) and the app shell
 * shares the one list. Circles left the scene for the chat strip (D-238):
 * they only decide who hears what is said, and that choice belongs beside
 * the saying.
 */

import { t } from "./locale";

//: `label` is a getter: this list is built once, when the module is first
//: imported, and the language can be switched long afterwards. A plain string
//: would be the word of whichever language happened to be spoken then, frozen
//: for the rest of the session (the same reason `arrange` uses getters).
export const VIEWS = [
  { id: "map", get label() { return t("ui-view-map"); } },
  { id: "place", get label() { return t("ui-view-place"); } },
] as const;

export type View = (typeof VIEWS)[number]["id"];
