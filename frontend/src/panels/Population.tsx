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
import { t } from "../locale";
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
            <td>{t("ui-city-people-world")}</td>
            <td className="num">{world["people"] ?? 0}</td>
          </tr>
          {panel && (
            <>
              <tr>
                <td>{t("ui-city-people-here")}</td>
                <td className="num">{panel.people.here}</td>
              </tr>
              <tr>
                <td>{t("ui-city-people-printed")}</td>
                <td className="num">{panel.people.printed}</td>
              </tr>
            </>
          )}
        </tbody>
      </table>

      <h3>
        {t("ui-city-offices")}
        <Rule>{t("ui-city-offices-rule")}</Rule>
      </h3>
      {city.offices.length === 0 ? (
        <p className="note">{t("ui-city-offices-none")}</p>
      ) : (
        city.offices.map((office) => (
          <p key={office.id}>
            <b>{office.title}</b> · {office.who}
            <span className="note"> · {office.powers.join(", ")}</span>
          </p>
        ))
      )}

      <h3>{t("ui-city-residents")}</h3>
      <p className="note">{city.citizens.join(" · ") || t("ui-city-residents-none")}</p>

      {/* Дефектная печать (D-173): по лору принтер иногда печатает людей без
          интеллекта. Репорт снижает доверие и кредит, а не убивает: необратимую
          переработку делает только внеигровой саппорт. */}
      <div className="row">
        <input
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          placeholder={t("ui-city-report-who")}
        />
        <button
          className="quiet"
          onClick={() => void session.send("person.report", { who: target })}
          disabled={busy || !target.trim()}
        >
          {t("ui-city-report")}
        </button>
        <button
          className="quiet"
          onClick={() => void session.send("person.unreport", { who: target })}
          disabled={busy || !target.trim()}
          title={t("ui-city-unreport-title")}
        >
          {t("ui-city-unreport")}
        </button>
        <span className="note">{t("ui-city-report-note")}</span>
      </div>
    </div>
  );
}
