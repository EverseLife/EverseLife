// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * A number typed by hand.
 *
 * ## Why the plain input was not enough
 *
 * A controlled number box holds a number, and a number has no way to say "the
 * box is empty". `Number("")` is `0`, so clearing the box reported zero, zero
 * was drawn back into it, and the digit the hand had just deleted reappeared
 * under the cursor: typing 123 into a cleared field gave 0123. The box could
 * be added to and never emptied -- one had to select the whole of it first,
 * which is a thing one has to know, and nothing on screen said so.
 *
 * So the characters being typed are kept here, beside the number, and an
 * emptied box stays empty until the hand leaves it. On the way out it snaps
 * back to the number it holds: what a field shows when nobody is in it is
 * always the value, never a draft. The rule itself is `fields.shownNumber`,
 * which is where it is tested.
 *
 * **An empty box reports `null`, not zero**, and every caller says what that
 * means for it. Most mean nothing -- `?? 0`, and their buttons are already shut
 * on a zero. `Amount` means the whole stack, which is its documented default:
 * folded into a zero, an emptied box would have made the take move nothing at
 * all, silently and without a refusal.
 *
 * The number itself belongs to the caller -- the same row often has two
 * buttons -- and so does the clamping: `Amount` clamps to the stack, the
 * market's volume to whole pieces.
 *
 * Not every number box in the client is this component: six of them keep their
 * value as a string already (`Account`, `Register`, three in `Alpha`,
 * `DragMove`), which is the same cure by another route -- a string can be
 * empty. They are left alone; a box that holds a number needs this one.
 */

import { useState, type InputHTMLAttributes } from "react";

import { shownNumber, typedNumber } from "./fields";

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type"> & {
  /** The number the field holds. `null` draws an empty field. */
  value: number | null;
  /** What has been typed. `null` -- the box is empty; what that means is the
   *  caller's to say. */
  onChange: (value: number | null) => void;
};

export function NumberField({ value, onChange, onBlur, ...rest }: Props) {
  //: The characters in the box while they are being typed. `null` -- nobody is
  //: typing, and the box shows the number.
  const [typed, setTyped] = useState<string | null>(null);
  return (
    <input
      {...rest}
      type="number"
      value={shownNumber(typed, value)}
      onChange={(e) => {
        const text = e.target.value;
        setTyped(text);
        onChange(typedNumber(text));
      }}
      onBlur={(e) => {
        setTyped(null);
        onBlur?.(e);
      }}
    />
  );
}
