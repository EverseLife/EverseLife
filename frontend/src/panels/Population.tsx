// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Population -- a state tab of the sidebar (D-140, D-154).
 *
 * Visible only to state officials: who lives, who governs, how many people
 * are in the city and in the world. Appointing and dismissing is in the
 * administration (D-155); from here one only looks.
 */


import { useState } from "react";
import { useSession } from "../actions";
import { Rule } from "../Rule";
import type { StateView } from "./State";

export function Population({ view, busy }: { view: StateView; busy: boolean }) {
  const session = useSession();
  const [target, setTarget] = useState("");
  const { city, panel, world } = view;

  return (
    <div>
      <p className="sign">{city.name}</p>
      <table>
        <tbody>
          <tr>
            <td>личностей в мире</td>
            <td className="num">{world["people"] ?? 0}</td>
          </tr>
          {panel && (
            <>
              <tr>
                <td>тел в городе</td>
                <td className="num">{panel.people.here}</td>
              </tr>
              <tr>
                <td>напечатано за окно</td>
                <td className="num">{panel.people.printed}</td>
              </tr>
            </>
          )}
        </tbody>
      </table>

      <h3>
        Должности
        <Rule>
          Назначать и снимать — в администрации: власть присутственна.
        </Rule>
      </h3>
      {city.offices.length === 0 ? (
        <p className="note">должностей нет</p>
      ) : (
        city.offices.map((office) => (
          <p key={office.id}>
            <b>{office.title}</b> · {office.who}
            <span className="note"> · {office.powers.join(", ")}</span>
          </p>
        ))
      )}

      <h3>Жители</h3>
      <p className="note">{city.citizens.join(" · ") || "пока никого"}</p>

      {/* Дефектная печать (D-173): по лору принтер иногда печатает людей без
          интеллекта. Репорт снижает доверие и кредит, а не убивает: необратимую
          переработку делает только внеигровой саппорт. */}
      <div className="row">
        <input
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          placeholder="имя дефектной печати"
        />
        <button
          className="quiet"
          onClick={() => void session.send("person.report", { who: target })}
          disabled={busy || !target.trim()}
        >
          Сообщить
        </button>
        <button
          className="quiet"
          onClick={() => void session.send("person.unreport", { who: target })}
          disabled={busy || !target.trim()}
          title="отозвать свой репорт"
        >
          Отозвать
        </button>
        <span className="note">
          Репорт снижает доверие и кредитный лимит цели — не больше того. Ошиблись —
          отзовите.
        </span>
      </div>
    </div>
  );
}
