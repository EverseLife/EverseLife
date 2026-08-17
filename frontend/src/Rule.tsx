/**
 * A rule of the world, told once rather than forever.
 *
 * There are two utterly different things wearing the same grey small type in
 * this client, and telling them apart is what won back half the screen:
 *
 * - **state** -- what is true here and now: "кровать здесь: сон быстрее",
 *   "занято 92,5 из 260 м²". It changes, it is worth reading, it stays;
 * - **a rule of the world** -- how the world works: "за рабочей станцией
 *   работает один", "сайдбар — это Сеть". It is identical on the thousandth session, and
 *   permanently under every panel it becomes a background you hunt for buttons
 *   through. Measured on one node: 35 explanations, 60% of all the text on screen.
 *
 * A rule is not deleted -- a newcomer needs it, and what was read once must not
 * become unreachable. It moves behind a "?" that opens on click or a tap, and
 * in the `plain` density it is spelled out in place again.
 *
 * The rule opens in the same small window as a `Hint`, not in place. `<details>`
 * unfolded between the panel's own rows: the text pushed everything below it
 * down, and on a long panel the button the hand was going for left the screen
 * at the moment of the tap. The window costs the page no room at all -- and
 * still opens from the keyboard and from a phone, where there is no hover.
 */

import { useRef, useState, type ReactNode } from "react";
import { useDensity } from "./density";
import { HintWindow } from "./Hint";

export function Rule({ children }: { children: ReactNode }) {
  const density = useDensity();
  const [open, setOpen] = useState(false);
  const mark = useRef<HTMLButtonElement>(null);

  //: In the plain density the rule is simply visible -- for the first hours
  //: that is exactly what is wanted, and the "?" would only be one more thing
  //: to work out.
  if (density === "plain") return <div className="rule rule-body">{children}</div>;

  const shut = () => {
    setOpen(false);
    mark.current?.focus();
  };

  return (
    <div className="rule">
      <button
        ref={mark}
        type="button"
        className="rule-mark bare"
        aria-label="как это работает"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        <span className="rule-sign" aria-hidden="true">
          ?
        </span>
        <span className="rule-lead">как это работает</span>
      </button>
      {open && (
        <HintWindow label="Как это работает" onClose={shut}>
          {children}
        </HintWindow>
      )}
    </div>
  );
}
