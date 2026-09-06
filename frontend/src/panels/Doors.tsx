// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Where to print for the first time (D-013, D-182).
 *
 * The newcomer's first decision, and it is deliberately about people, not
 * numbers: there is no price or term here at all -- **the first body is
 * printed at once and for free** at any door (D-040). The twelve hours of the
 * Forerunners' Printer take effect from the second print, and speaking of
 * them on this screen would be lying.
 *
 * The door is chosen on the globe beside this (D-319): a mark on the planet
 * is a door, and this half shows the card of the one chosen -- what a door
 * gives and what it asks (D-184, D-281) as table rows, not in text: the
 * engine enforces it, and the person must see it before clicking, not learn
 * it from a refusal. The doors are listed by name too, for a hand that
 * cannot reach the globe.
 */

import * as api from "../api";
import type { Door } from "../api";
import { t } from "../locale";

type Props = {
  doors: Door[];
  name: string;
  busy: boolean;
  trouble?: string | null;
  picked: string | null;
  onPick: (node: string) => void;
  onEnter: (node: string) => void;
  onBack: () => void;
};

export function Doors({ doors, name, busy, trouble, picked, onPick, onEnter, onBack }: Props) {
  const door = doors.find((one) => one.node === picked) ?? null;

  return (
    <section className="doors-step">
      <h1>{t("ui-doors-title")}</h1>
      <p className="note center">{t("ui-doors-lead", { name })}</p>

      {doors.length === 0 ? (
        <p className="trouble">{t("ui-doors-empty-world")}</p>
      ) : (
        <>
          {/* The list by name: the same doors as the marks, for those who
              do not turn globes. Sorted as the server sorts, by people. */}
          <div className="row tabs doors-list">
            {doors.map((one) => (
              <button
                key={one.node}
                className={one.node === picked ? "" : "quiet"}
                aria-pressed={one.node === picked}
                onClick={() => onPick(one.node)}
                disabled={busy}
              >
                {/* The door's own name, the city after it: the capital has two
                    doors, and two buttons reading the city alone would not
                    tell them apart (the same rule the card's heading keeps). */}
                {one.precursor ? t("ui-doors-precursor") : one.name}
                {!one.precursor && one.city ? ` · ${one.city}` : ""}
              </button>
            ))}
          </div>

          {door === null ? (
            <p className="note center">{t("ui-doors-pick-on-globe")}</p>
          ) : (
            <section className="card flat door">
              {/* В заголовке — чем эта дверь отличается от соседней. Город
                  вынесен в строку: у столицы дверей две, и одинаковые
                  заголовки не давали бы их различить. */}
              <h2>{door.precursor ? t("ui-doors-precursor") : door.name}</h2>
              <p className="note">
                {door.precursor ? t("ui-doors-precursor-note") : t("ui-doors-city-note")}
              </p>
              <table>
                <tbody>
                  <tr>
                    <td>{t("ui-doors-city")}</td>
                    <td className="num">{door.city ?? t("ui-doors-outside")}</td>
                  </tr>
                  <tr>
                    <td>{t("ui-doors-people")}</td>
                    <td className="num">{door.city ? door.population : "—"}</td>
                  </tr>
                  <tr>
                    <td>{t("ui-doors-citizens")}</td>
                    <td className="num">{door.city ? door.citizens : "—"}</td>
                  </tr>
                  <tr>
                    <td>{t("ui-doors-grant")}</td>
                    <td className="num">
                      {door.grant > 0 ? `${api.tk(door.grant)} ₭` : t("ui-doors-nothing")}
                    </td>
                  </tr>
                  <tr>
                    <td>{t("ui-doors-first-body")}</td>
                    <td className="num">{t("ui-doors-at-once")}</td>
                  </tr>
                  {/* Что даёт дверь (D-184, D-281). Показано у городских
                      дверей и только у них: гражданство даёт город, а дверь,
                      вокруг которой города нет, не даёт ничего — записывать
                      не во что. Срока обязательства больше нет: выйти можно
                      в первую же минуту, пока не взят кредит. */}
                  {door.city && (
                    <>
                      <tr>
                        <td>{t("ui-doors-citizenship")}</td>
                        <td className="num">{t("ui-doors-citizenship-at-once")}</td>
                      </tr>
                      <tr>
                        <td>{t("ui-doors-tax")}</td>
                        <td className="num">
                          {door.tax > 0 ? `${door.tax}%` : t("ui-doors-nothing")}
                        </td>
                      </tr>
                    </>
                  )}
                </tbody>
              </table>
              {/* Слово города: его пишет власть, а не движок (D-183). Молчащий
                  город показывает только числа — сочинять за него нечего. */}
              {door.about && <p className="say">«{door.about}»</p>}
              <div className="row">
                <button onClick={() => onEnter(door.node)} disabled={busy}>
                  {t("ui-doors-print-here")}
                </button>
              </div>
            </section>
          )}
        </>
      )}

      {trouble && <p className="trouble">{trouble}</p>}
      <div className="row">
        <button className="quiet" onClick={onBack} disabled={busy}>
          {t("ui-doors-back")}
        </button>
      </div>
    </section>
  );
}
