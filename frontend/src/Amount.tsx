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
 */

export function Amount({
  value,
  max,
  onChange,
  title,
}: {
  /** How much is set now. `null` means "the whole stack". */
  value: number | null;
  max: number;
  onChange: (value: number | null) => void;
  title?: string;
}) {
  return (
    <input
      type="number"
      min={0}
      max={max}
      step="any"
      value={value ?? max}
      onChange={(e) => {
        const typed = Number(e.target.value);
        //: An empty field or a number beyond the stack is not an error worth a
        //: refusal: we clamp it and move on.
        onChange(Number.isFinite(typed) ? Math.min(Math.max(0, typed), max) : null);
      }}
      title={title ?? `не больше ${max}`}
    />
  );
}

