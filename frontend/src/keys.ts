// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Keyboard manners for things that act like buttons but cannot be one.
 *
 * A table row is the case this exists for: a `<button>` cannot hold a `<tr>`,
 * and a button inside every cell would put three controls where the eye reads
 * one line. So the row carries the button's manners instead -- a role, a stop
 * on the tab ring, and these two keys.
 */

/** A row that acts on a click acts on Enter and Space too. */
export function onEnter(event: React.KeyboardEvent, act: () => void): void {
  if (event.key !== "Enter" && event.key !== " ") return;
  event.preventDefault();
  act();
}
