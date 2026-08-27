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
  if (here >= needed) return `${needed.toFixed(0)} ${what}`;
  return `${what} ${here.toFixed(0)} из ${needed.toFixed(0)}`;
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
        Тела нет
        <Rule>
          Город продаёт не жизнь, а скорость: заплатил — вернулся через минуты, не
          заплатил — через двенадцать часов у Принтера Предтеч. Поэтому у цены
          воскрешения есть потолок, и никто не может запереть личность у себя.
        </Rule>
      </h2>
      <p className="note">
        Личность цела: имя, знания, счёт и обязательства пережили тело. Погибло
        то, что тело несло, — и треть этого осталась лежать на месте гибели.
      </p>

      {ongoing ? (
        <p className="sign">печать идёт · тело будет {left(ongoing.ready_at)}</p>
      ) : doors.length === 0 ? (
        <p className="trouble">
          В мире нет ни одного биопринтера. Это ситуация, которой быть не
          должно: вход в игру не блокируется никогда.
        </p>
      ) : (
        <table>
          <tbody>
            {doors.map((door) => (
              <tr key={door.node}>
                <td>
                  {door.name}
                  {door.city && <span className="note"> · {door.city}</span>}
                  {door.precursor && <span className="note"> · Предтечи</span>}
                </td>
                <td className="num">{term(door.minutes)}</td>
                <td className="num">
                  {door.precursor
                    ? "бесплатно"
                    : door.at_city_expense
                      ? "за счёт города"
                      : `${api.tk(door.cost)} ₭`}
                </td>
                <td className="note">
                  {door.precursor
                    ? "энергии и железа не требует"
                    : `${short("энергии", door.energy_here, door.energy)} · ${short(
                        "железа",
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
                    Печатать
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
  if (minutes < 60) return `${Math.round(minutes)} мин`;
  return `${(minutes / 60).toFixed(0)} ч`;
}

function left(when: string): string {
  const minutes = (new Date(when).getTime() - Date.now()) / 60_000;
  if (minutes <= 0) return "вот-вот";
  if (minutes < 60) return `через ${Math.round(minutes)} мин`;
  return `через ${(minutes / 60).toFixed(1)} ч`;
}
