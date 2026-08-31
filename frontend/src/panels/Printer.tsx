// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The cloud: no body, the identity exists (D-012, D-028, D-033).
 *
 * The screen shows exactly what makes sense now -- where to print and for how
 * much. No "you died, press OK": the punishment already happened, and it is
 * economic, not "sit in the corner".
 *
 * The main thing this screen must convey without words: **there is always a
 * free door**. The city sells speed, not life, and the identity cannot be
 * made a hostage -- it is in the Net, not in somebody's printer.
 */


import * as api from "../api";
import type { Look, Printer as Door } from "../api";
import { Rule } from "../Rule";
import { t } from "../locale";
import { Refusal, useActions, useSession } from "../actions";

type Props = {
  look: Look;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

/**
 * What a printer asks of a place, and what the place has if that is not enough.
 *
 * The shortfall is the only interesting half, and it is named only when there
 * is one: "энергии 281 из 1000" is a reason the print will be refused, while
 * "1000 энергии" is a price. The row used to print the demand and hide the
 * remainder -- so a city holding a fifth of the energy needed looked exactly
 * like one that could print -- and it put the demand first for the iron, where
 * a surplus of fifty against a cost of ten read as "10 из 50", a shortage.
 */
function short(what: string, here: number, needed: number): string {
  if (here >= needed) return t("ui-printer-enough", { what, needed: needed.toFixed(0) });
  return t("ui-printer-short", { what, here: here.toFixed(0), needed: needed.toFixed(0) });
}

export function Printer({ look }: Omit<Props, "busy" | "act">) {
  const session = useSession();
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const ongoing = look.printing ?? null;
  const doors: Door[] = look.printers ?? [];

  return (
    <section>
      <Refusal of={acting} />
      <h2>
        {t("ui-printer-title")}
        <Rule>{t("ui-printer-rule")}</Rule>
      </h2>
      <p className="note">{t("ui-printer-note")}</p>

      {ongoing ? (
        <p className="sign">{t("ui-printer-printing", { when: left(ongoing.ready_at) })}</p>
      ) : doors.length === 0 ? (
        <p className="trouble">{t("ui-printer-none")}</p>
      ) : (
        <table>
          <tbody>
            {doors.map((door) => (
              <tr key={door.node}>
                <td>
                  {door.name}
                  {door.city && <span className="note"> · {door.city}</span>}
                  {door.precursor && <span className="note"> · {t("ui-printer-precursor")}</span>}
                </td>
                <td className="num">{term(door.minutes)}</td>
                <td className="num">
                  {door.precursor
                    ? t("ui-printer-free")
                    : door.at_city_expense
                      ? t("ui-printer-at-city-expense")
                      : `${api.tk(door.cost)} ₭`}
                </td>
                <td className="note">
                  {door.precursor
                    ? t("ui-printer-no-cost")
                    : `${short(t("ui-printer-energy"), door.energy_here, door.energy)} · ${short(
                        t("ui-printer-iron"),
                        door.iron_here,
                        door.iron,
                      )}`}
                </td>
                <td>
                  <button
                    onClick={() =>
                      act(() => session.send("body.print", { node: door.node }))
                    }
                    disabled={busy}
                  >
                    {t("ui-printer-print")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function term(minutes: number): string {
  //: The digits are chosen here and travel as text: a number handed to Fluent
  //: is grouped by the locale's own rules, and the panel would then spell the
  //: same figure two ways depending on which message drew it.
  if (minutes < 60) return t("ui-printer-term-minutes", { minutes: String(Math.round(minutes)) });
  return t("ui-printer-term-hours", { hours: (minutes / 60).toFixed(0) });
}

function left(when: string): string {
  const minutes = (new Date(when).getTime() - Date.now()) / 60_000;
  if (minutes <= 0) return t("ui-printer-soon");
  if (minutes < 60) return t("ui-printer-in-minutes", { minutes: String(Math.round(minutes)) });
  return t("ui-printer-in-hours", { hours: (minutes / 60).toFixed(1) });
}
