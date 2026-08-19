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
 */
import { counted } from "./amounts";

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
    <input
      type="number"
      min={0}
      max={max}
      step={whole ? 1 : "any"}
      value={value ?? max}
      onChange={(e) => {
        const typed = Number(e.target.value);
        //: An empty field or a number beyond the stack is not an error worth a
        //: refusal: we clamp it and move on. A piece is clamped to whole ones
        //: as well -- the engine would refuse the fraction, and the field must
        //: not offer what cannot be done (D-212).
        if (!Number.isFinite(typed)) return onChange(null);
        const held = Math.min(Math.max(0, typed), max);
        onChange(whole ? Math.floor(held) : held);
      }}
      title={title ?? (whole ? `не больше ${max}, целыми штуками` : `не больше ${max}`)}
    />
  );
}

