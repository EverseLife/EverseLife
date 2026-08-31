// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

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
 * become unreachable. It stands behind a "?" **beside the title of whatever it
 * explains**: the words "как это работает" used to be spelled out at the foot
 * of every block, and four such lines one under another said nothing about
 * which block each belonged to.
 *
 * It is the same icon and the same floating layer as a `Hint` -- the split
 * between state and rule is ours, not the player's, and there is no reason for
 * a person to meet two kinds of "?" on one screen.
 */

import type { ReactNode } from "react";
import { Hint } from "./Hint";
import { t } from "./locale";

export function Rule({ children }: { children: ReactNode }) {
  return <Hint label={t("ui-rule")}>{children}</Hint>;
}
