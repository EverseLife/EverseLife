// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What a number box shows and what it means (`NumberField.tsx`).
 *
 * Kept apart from the component so that the rule can be read and tested on its
 * own: it decides, on every keystroke, between the characters the hand is
 * typing and the number the caller holds, and the wrong branch there silently
 * eats what somebody is writing.
 */

/**
 * The number a box's text means, or `null` for an empty box.
 *
 * A browser reports a half-typed number -- "-", "1e" -- as an empty string, so
 * "nothing yet" and "not a number yet" are the same answer: the box holds no
 * number.
 */
export function typedNumber(text: string): number | null {
  if (text.trim() === "") return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

/**
 * What the box draws: the draft, or the number.
 *
 * Three cases, and each is a decision:
 *
 * - **nobody is typing** (`typed` is null) -- the number, which is what a field
 *   at rest must always show;
 * - **the box was emptied** -- it stays empty until the hand leaves it. This is
 *   the whole reason the draft is kept at all: a controlled number box reads an
 *   empty string as zero, draws the zero back, and the digit just deleted
 *   reappears under the cursor (typing 123 into a cleared field gave 0123);
 * - **the draft still means the number held** -- the draft, so that "1.50" and
 *   a trailing dot survive being typed. Where it no longer does, the caller has
 *   moved the number under our hands -- a clamp to the stack, an answer from
 *   the server -- and the number wins, so a clamp is felt the moment it lands.
 */
export function shownNumber(typed: string | null, value: number | null): string {
  const held = value == null ? "" : String(value);
  if (typed === null) return held;
  if (typed.trim() === "") return "";
  return Number(typed) === value ? typed : held;
}
