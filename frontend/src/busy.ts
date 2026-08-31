// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What the body is at, and what that forbids (D-211).
 *
 * One body does one thing, so a button that starts a second one must be grey
 * before it is pressed, with the reason on it -- a refusal collected after the
 * click tells the player the same thing one step too late.
 *
 * The server sends the list (`look.doings`); the rule of who forbids whom lives
 * here, in one place, because it is a rule of the interface: the engine already
 * refuses correctly on its own.
 */

import type { Look } from "./api";
import { when } from "./clock";
import { t } from "./locale";

/** Sleep is not in anybody's way: lying down freezes a batch, and the machine goes free. */
export const CRAFT = "craft";
export const SLEEP = "sleep";
export const FORAGE = "forage";

/**
 * The occupation that forbids starting `what`, or nothing when the hands are free.
 *
 * `besides` names the kinds that do not count for this button -- the occupation
 * asking about itself: a second batch is a place in the queue rather than a
 * second work, and sleep is refused by nothing but another occupation.
 */
export function busyWith(look: Look, besides: string[] = []): string | null {
  const doing = (look.doings ?? []).find((d) => !besides.includes(d.kind));
  if (!doing) return null;
  //: The deadline is said as a distance -- "через 12 мин" -- not as a stamp:
  //: the player is choosing between waiting and going elsewhere, and the world
  //: counts a day of its own length anyway (D-029).
  //: `doing.what` arrives already rendered, in the language of whoever is
  //: reading: the engine names the occupation and its own `i18n` writes it out
  //: (`api/commands/look`). Only the wrapper around it is ours.
  return doing.until
    ? t("ui-busy-what-until", { what: doing.what, when: when(doing.until) })
    : t("ui-busy-what", { what: doing.what });
}
