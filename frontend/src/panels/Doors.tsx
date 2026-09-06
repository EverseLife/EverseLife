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
 * it from a refusal.
 *
 * On a phone the globe is the whole screen (D-319), and then this half waits
 * its turn: until a door is chosen there is nothing here but the way back, and
 * the choosing happens on the planet. Choose one and the card comes up over
 * the globe; the way back is then the way to the globe again, not to the step
 * before -- on a screen where the planet **is** the step, "back" means "back
 * to the planet".
 *
 * On the globe and nowhere else: the row of names that used to stand here was
 * a second way to do the one thing this screen is for, and two ways to choose
 * a city taught neither. The mark itself is the control -- it takes focus and
 * answers Enter (D-077), so the globe is not a hand's privilege. What the
 * globe cannot show is the exception: a door off its planet or without
 * degrees on it is offered below by name rather than leaving a newcomer with
 * no door at all.
 *
 * The rows are the whole of what is said. Three paragraphs used to stand
 * under them -- where a grant comes from, that citizenship holds nobody, that
 * the city's word is the city's and not the engine's -- and they were three
 * screens of reading in front of one click, on the one screen where nobody
 * has yet seen the game. What the engine enforces is in the rows; what the
 * city promises is in its own quotes, and D-183 leaves it that way on
 * purpose.
 */

import * as api from "../api";
import type { Door } from "../api";
import { t } from "../locale";
import { drawnOn } from "./EntryGlobe";

type Props = {
  doors: Door[];
  name: string;
  busy: boolean;
  trouble?: string | null;
  picked: string | null;
  /** Null unchooses: on a phone that is what the way back does. */
  onPick: (node: string | null) => void;
  onEnter: (node: string) => void;
  onBack: () => void;
  /** Whether the globe has the screen to itself and this half floats on it:
   *  a phone. Then an unchosen door means no card and no words at all. */
  overGlobe?: boolean;
};

export function Doors({
  doors,
  name,
  busy,
  trouble,
  picked,
  onPick,
  onEnter,
  onBack,
  overGlobe = false,
}: Props) {
  const door = doors.find((one) => one.node === picked) ?? null;
  //: What no mark stands for. The globe draws one planet (`drawnOn`) and on
  //: it what has degrees: a door with a flat place, or one standing on
  //: another planet, has no mark to press -- and without a name here it could
  //: not be chosen at all. The free door of the Forerunners is among the ones
  //: this must never lose (D-028).
  const globe = drawnOn(doors);
  const unplaced = doors.filter(
    (one) => one.planet !== globe || !one.place || !("lat" in one.place),
  );
  //: A door the globe cannot draw: no place on its sphere, or another
  //: planet's. It stands by name, and normally there are none.
  const names =
    unplaced.length === 0 ? null : (
      <div className="row tabs">
        {unplaced.map((one) => (
          <button
            key={one.node}
            className={one.node === picked ? "" : "quiet"}
            aria-pressed={one.node === picked}
            onClick={() => onPick(one.node)}
            disabled={busy}
          >
            {/* The door's own name, the city after it: the capital has two
                doors, and two buttons reading the city alone would not tell
                them apart (the same rule the card's heading keeps). */}
            {one.precursor ? t("ui-doors-precursor") : one.name}
            {!one.precursor && one.city ? ` · ${one.city}` : ""}
          </button>
        ))}
      </div>
    );

  //: The globe has the screen, and nothing is chosen on it yet: the way back
  //: is the whole of this half. The hint under the planet says what to do,
  //: and a heading over an empty box would only take the sky away from it.
  if (overGlobe && door === null) {
    return (
      <section className="doors-step bare">
        {names}
        {trouble && <p className="trouble">{trouble}</p>}
        <div className="row last">
          <button className="quiet" onClick={onBack} disabled={busy}>
            {t("ui-doors-back")}
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="doors-step">
      <h1>{t("ui-doors-title")}</h1>
      <p className="note center">{t("ui-doors-lead", { name })}</p>

      {doors.length === 0 ? (
        <p className="trouble">{t("ui-doors-empty-world")}</p>
      ) : (
        <>
          {names}

          {door === null ? (
            <p className="note center">{t("ui-doors-pick-on-globe")}</p>
          ) : (
            // `door` names the card for the eye and for a console, not for a
            // rule: the step's own section is the box, and the card lies flat
            // in it (`entry.css`).
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
            </section>
          )}
        </>
      )}

      {trouble && <p className="trouble">{trouble}</p>}
      {/* The last row of the step, where the three steps before it keep
          theirs: the way back on the left, the way on at the right edge.
          Printing is the way on -- it stood inside the door's card, a step
          down and to the left of where the hand had learnt to look. */}
      <div className="row last">
        <button
          className="quiet"
          //: Over the globe the way back is the way to the globe: the planet
          //: is the step, and stepping off the card returns to choosing.
          onClick={() => (overGlobe ? onPick(null) : onBack())}
          disabled={busy}
        >
          {t("ui-doors-back")}
        </button>
        {door && (
          <button onClick={() => onEnter(door.node)} disabled={busy}>
            {t("ui-doors-print-here")}
          </button>
        )}
      </div>
    </section>
  );
}
