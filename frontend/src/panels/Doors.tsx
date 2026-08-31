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
 * The cards stand side by side so that in ten seconds the world's main
 * structure is seen: cities differ, they set the terms themselves, and nobody
 * has to be kind. The Forerunners' Printer is the last card: a fallback door
 * with neither residents nor a treasury, and it is always open.
 *
 * Print conditions (D-184) stand as table rows, not in text: the engine
 * enforces them, and the person must see them before clicking, not learn them from a refusal.
 */

import { useMemo, useState } from "react";
import * as api from "../api";
import type { Door } from "../api";
import { t } from "../locale";

type Props = {
  doors: Door[];
  name: string;
  busy: boolean;
  trouble?: string | null;
  onPick: (node: string) => void;
  onBack: () => void;
};

export function Doors({ doors, name, busy, trouble, onPick, onBack }: Props) {
  //: The list comes already sorted -- populous cities first (D-187) -- and
  //: search narrows it by city or node name. An empty search is the whole list.
  const [query, setQuery] = useState("");
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return doors;
    return doors.filter(
      (d) =>
        d.name.toLowerCase().includes(q) ||
        (d.city ?? "").toLowerCase().includes(q) ||
        (d.precursor && "предтеч".includes(q)),
    );
  }, [doors, query]);

  return (
    <section className="wide doors-step">
      <h1>{t("ui-doors-title")}</h1>
      <p className="note center">{t("ui-doors-lead", { name })}</p>

      <div className="row search">
        <input
          type="search"
          placeholder={t("ui-doors-search")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label={t("ui-doors-search-label")}
        />
        <span className="note">
          {t("ui-doors-count", { shown: String(visible.length), total: String(doors.length) })}
        </span>
      </div>

      {doors.length === 0 ? (
        <p className="trouble">{t("ui-doors-empty-world")}</p>
      ) : visible.length === 0 ? (
        <p className="note center">{t("ui-doors-nothing-found")}</p>
      ) : (
        <div className="doors">
          {visible.map((door) => (
            <section key={door.node}>
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
                  {/* Условия печати (D-184). Показаны у городских дверей и
                      только у них: у Предтеч условий нет и быть не может —
                      машина ничья. */}
                  {door.city && (
                    <>
                      <tr>
                        <td>{t("ui-doors-citizenship")}</td>
                        <td className="num">
                          {door.citizenship ? obligation(door.term) : t("ui-doors-not-required")}
                        </td>
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
                <button onClick={() => onPick(door.node)} disabled={busy}>
                  {t("ui-doors-print-here")}
                </button>
              </div>
            </section>
          ))}
        </div>
      )}

      <p className="note">{t("ui-doors-grant-note")}</p>
      <p className="note">{t("ui-doors-rules-note")}</p>
      <p className="note">{t("ui-doors-word-note")}</p>
      {trouble && <p className="trouble">{trouble}</p>}
      <div className="row">
        <button className="quiet" onClick={onBack} disabled={busy}>
          {t("ui-doors-back")}
        </button>
      </div>
    </section>
  );
}

/** The obligation in words: "for 3 days" or just "mandatory". */
function obligation(days: number): string {
  if (days <= 0) return t("ui-doors-term-always");
  if (days < 1) return t("ui-doors-term-hours", { hours: String(Math.round(days * 24)) });
  return t("ui-doors-term-days", {
    days: days % 1 === 0 ? String(days) : days.toFixed(1),
  });
}
