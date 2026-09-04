// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * A quantity field for "take" and "put" (D-047, D-181).
 *
 * Everywhere a stack is moved -- market, chest, convoy, station hopper -- the
 * player must be able to say how much. Moving the whole stack by default is
 * the common case; typing a number is the case that used to be impossible,
 * and half the trade was built around it: nobody sells all the ore they own.
 *
 * The value lives in the caller: the same row often has two buttons, and the
 * number belongs to the row, not to a button.
 *
 * `goods` says what is being moved, and the field obeys the thing: a counted
 * one steps and clamps to whole pieces, a measured one takes any part (D-212).
 *
 * The box itself is `NumberField`, which is what lets it be emptied: a plain
 * controlled number box reads an empty string as zero and draws the zero back.
 */
import { counted } from "./amounts";
import { t } from "./locale";
import { NumberField } from "./NumberField";

export function Amount({
  value,
  max,
  onChange,
  title,
  goods,
}: {
  /** How much is set now. `null` means "the whole stack". */
  value: number | null;
  max: number;
  onChange: (value: number | null) => void;
  title?: string;
  /** What is being moved: a piece moves by one, the measured by any part (D-212). */
  goods?: string;
}) {
  const whole = goods !== undefined && counted(goods);
  return (
    <NumberField
      min={0}
      max={max}
      step={whole ? 1 : "any"}
      value={value ?? max}
      onChange={(typed) => {
        //: An emptied box is this field's own default back again -- the whole
        //: stack -- and not a zero: `chosen` reads the `null`, the button stays
        //: live, and the press moves what the box says it will. Reported as
        //: zero it would have moved nothing, with no refusal to explain it.
        if (typed === null) return onChange(null);
        //: A number beyond the stack is not an error worth a refusal: we clamp
        //: it and move on. A piece is clamped to whole ones as well -- the
        //: engine would refuse the fraction, and the field must not offer what
        //: cannot be done (D-212).
        const held = Math.min(Math.max(0, typed), max);
        onChange(whole ? Math.floor(held) : held);
      }}
      //: The ceiling goes in as a string: the field beside it shows the same
      //: figure raw, and Fluent would space out a four-digit one.
      title={title ?? t("ui-amount-max", { whole: String(whole), max: String(max) })}
    />
  );
}
