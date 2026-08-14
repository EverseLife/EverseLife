/**
 * Облако: тела нет, личность есть (D-012, D-028, D-033).
 *
 * Экран показывает ровно то, что сейчас имеет смысл, — где напечататься и
 * почём. Никаких «вы погибли, нажмите ОК»: наказание уже случилось, и оно
 * экономическое, а не «сиди в углу».
 *
 * Главное, что этот экран обязан сообщать без слов: **бесплатная дверь есть
 * всегда**. Город продаёт скорость, а не жизнь, и заложника из личности
 * сделать нельзя — она в Сети, а не в чужом принтере.
 */

import * as api from "../api";
import type { Look, Printer as Door, Session } from "../api";

type Props = {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

export function Printer({ look, session, busy, act }: Props) {
  const идёт = look.printing ?? null;
  const двери: Door[] = look.printers ?? [];

  return (
    <section>
      <h2>Тела нет</h2>
      <p className="note">
        Личность цела: имя, знания, счёт и обязательства пережили тело. Погибло
        то, что тело несло, — и треть этого осталась лежать на месте гибели.
      </p>

      {идёт ? (
        <p className="sign">печать идёт · тело будет {осталось(идёт.ready_at)}</p>
      ) : двери.length === 0 ? (
        <p className="trouble">
          В мире нет ни одного биопринтера. Это ситуация, которой быть не
          должно: вход в игру не блокируется никогда (D-028).
        </p>
      ) : (
        <table>
          <tbody>
            {двери.map((дверь) => (
              <tr key={дверь.node}>
                <td>
                  {дверь.name}
                  {дверь.city && <span className="note"> · {дверь.city}</span>}
                  {дверь.precursor && <span className="note"> · Предтечи</span>}
                </td>
                <td className="num">{срок(дверь.minutes)}</td>
                <td className="num">
                  {дверь.precursor
                    ? "бесплатно"
                    : дверь.at_city_expense
                      ? "за счёт города"
                      : `${api.tk(дверь.cost)} ₭`}
                </td>
                <td className="note">
                  {дверь.precursor
                    ? "энергии и железа не требует"
                    : `${дверь.energy.toFixed(0)} энергии · железа ${дверь.iron.toFixed(0)} из ${дверь.iron_here.toFixed(0)}`}
                </td>
                <td>
                  <button
                    onClick={() =>
                      act(() => session.send("body.print", { node: дверь.node }))
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

      <p className="note">
        Город продаёт не жизнь, а скорость: заплатил — вернулся через минуты, не
        заплатил — через двенадцать часов у Принтера Предтеч. Поэтому у цены
        воскрешения есть потолок, и никто не может запереть личность у себя
        (D-028, D-033).
      </p>
    </section>
  );
}

function срок(минут: number): string {
  if (минут < 60) return `${Math.round(минут)} мин`;
  return `${(минут / 60).toFixed(0)} ч`;
}

function осталось(когда: string): string {
  const минут = (new Date(когда).getTime() - Date.now()) / 60_000;
  if (минут <= 0) return "вот-вот";
  if (минут < 60) return `через ${Math.round(минут)} мин`;
  return `через ${(минут / 60).toFixed(1)} ч`;
}
