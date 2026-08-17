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
 * `<details>` rather than a hover popover on purpose: it works from the
 * keyboard, works on a phone where there is no hover at all, and needs no
 * script to open.
 */

import type { ReactNode } from "react";
import { useDensity } from "./density";

export function Rule({ children }: { children: ReactNode }) {
  const density = useDensity();
  //: In the plain density the rule is simply visible -- for the first hours
  //: that is exactly what is wanted, and the "?" would only be one more thing
  //: to work out.
  const spelled = density === "plain";
  return (
    <details className="rule" open={spelled}>
      <summary aria-label="как это работает">
        <span aria-hidden="true">?</span>
        <span className="rule-lead">как это работает</span>
      </summary>
      <div className="rule-body">{children}</div>
    </details>
  );
}
