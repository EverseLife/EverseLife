// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useEffect, useState } from "react";
import type { Look } from "../../api";
import { t } from "../../locale";
import { Rule } from "../../Rule";

/** A day, in milliseconds: the reactor's term is given in days of real time. */
const DAY = 86_400_000;
/** How often the count is redrawn. A day-long hand needs no faster beat. */
const BEAT = 60_000;

/** Whole days from now to the date, never below zero. */
export const daysLeft = (until: string) =>
  Math.max(0, Math.floor((new Date(until).getTime() - Date.now()) / DAY));

/** The short state line of the tile: what the player sees without opening it. */
export const reactorState = (until: string) => {
  const days = daysLeft(until);
  return days <= 0 ? t("ui-place-reactor-out") : t("ui-place-reactor-days", { days });
};

/**
 * The Forerunners' reactor: decay heat, without fuel and without people -- and
 * a year of it (D-232).
 *
 * The client counts the days itself from the one date the server sends: the
 * fading is a straight line, and asking the server for today's output would be
 * a poll for a number that moves by a hair (D-226). The point of showing it at
 * all is that the day it goes silent has to be seen from far off: that is the
 * day the city must already be standing on its own coal.
 */
export function Reactor({ look }: { look: Look }) {
  const until = look.node?.reactor_until;
  const [days, setDays] = useState(() => (until ? daysLeft(until) : 0));
  useEffect(() => {
    if (!until) return;
    setDays(daysLeft(until));
    const timer = setInterval(() => setDays(daysLeft(until)), BEAT);
    return () => clearInterval(timer);
  }, [until]);
  if (!until) return null;

  return (
    <div>
      <p className="sign">
        {t("ui-place-reactor-title")}
        <Rule>{t("ui-place-reactor-rule")}</Rule>
      </p>
      <table>
        <tbody>
          <tr>
            <td>{t("ui-place-reactor-when")}</td>
            <td className="num">
              {days <= 0 ? t("ui-place-reactor-already") : t("ui-place-reactor-in", { days })}
            </td>
          </tr>
        </tbody>
      </table>
      {days > 0 && days < 30 && (
        <p className="trouble">{t("ui-place-reactor-warning")}</p>
      )}
    </div>
  );
}
