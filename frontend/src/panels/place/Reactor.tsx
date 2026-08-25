// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useEffect, useState } from "react";
import type { Look } from "../../api";
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
  return days <= 0 ? "погас" : `${days} сут`;
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
        Наследие Предтеч
        <Rule>
          Реактор греет город и кормит маяк космодрома без топлива и без людей —
          но выход падает и в свой срок доходит до нуля. Дальше город держат те,
          кто в нём живёт: своя генерация, своя ТЭЦ. Погаснет последний
          работающий космодром планеты — сесть будет некуда, и планета потеряна.
        </Rule>
      </p>
      <table>
        <tbody>
          <tr>
            <td>гаснет</td>
            <td className="num">{days <= 0 ? "уже погас" : `через ${days} сут`}</td>
          </tr>
        </tbody>
      </table>
      {days > 0 && days < 30 && (
        <p className="trouble">
          Реактор на исходе: без своей генерации город остынет, а космодром
          погаснет вместе с ним.
        </p>
      )}
    </div>
  );
}
