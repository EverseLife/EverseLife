// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Where to print for the first time (D-013, D-182), in two steps: the printer,
 * then the city it stands in.
 *
 * The newcomer's first decision, and it is deliberately about people, not
 * numbers: there is no price or term here at all -- **the first body is
 * printed at once and for free** at any door (D-040). The twelve hours of the
 * Forerunners' Printer take effect from the second print, and speaking of
 * them on this screen would be lying.
 *
 * **The printer** is chosen on the globe beside this (D-319), and nowhere
 * else: a mark on the planet is a door, and the row of names that used to
 * stand here was a second way to do the one thing this screen is for -- two
 * ways to choose a city taught neither. The mark itself is the control: it
 * takes focus and answers Enter (D-077), so the globe is not a hand's
 * privilege. What the globe cannot show is the exception: a door off its
 * planet or without degrees on it is offered by name rather than leaving a
 * newcomer with no door at all.
 *
 * **The city** is the step after it, and it is a city that is read there --
 * its name at the head, its people, its grant, its tax as table rows, not in
 * text: the engine enforces them, and the person must see them before
 * clicking, not learn them from a refusal (D-184, D-281). What the machine is
 * called and what a bioprinter does are the same at every door and are not
 * said again here; what differs is the city, and the city's own word under
 * the rows is its promise, not the engine's (D-183).
 *
 * On a phone the globe is the whole screen (D-319), and then the printer step
 * is a heading and the way back: the choosing happens on the planet.
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
  onPick: (node: string) => void;
  onBack: () => void;
  /** Whether the globe has the screen to itself and this half floats on it:
   *  a phone. Then the step is its heading and the way back, and the words
   *  that would explain it stand between the player and the planet. */
  overGlobe?: boolean;
};

export function Doors({
  doors,
  name,
  busy,
  trouble,
  picked,
  onPick,
  onBack,
  overGlobe = false,
}: Props) {
  //: What no mark stands for. The globe draws one planet (`drawnOn`) and on
  //: it what has degrees: a door with a flat place, or one standing on
  //: another planet, has no mark to press -- and without a name here it could
  //: not be chosen at all. The free door of the Forerunners is among the ones
  //: this must never lose (D-028).
  const globe = drawnOn(doors);
  const unplaced = doors.filter(
    (one) => one.planet !== globe || !one.place || !("lat" in one.place),
  );

  return (
    <section className={`doors-step${overGlobe ? " bare" : ""}`}>
      <h1>{t("ui-doors-title")}</h1>
      {doors.length === 0 ? (
        <p className="trouble">{t("ui-doors-empty-world")}</p>
      ) : (
        <>
          {/* The globe says the rest of it: on a phone it is the screen, and
              three lines of explanation over a planet are three lines the
              planet does not have. */}
          {!overGlobe && (
            <>
              <p className="note center">{t("ui-doors-lead", { name })}</p>
              <p className="note center">{t("ui-doors-pick-on-globe")}</p>
            </>
          )}
          {/* A door the globe cannot draw: no place on its sphere, or another
              planet's. It stands here by name, and normally there are none. */}
          {unplaced.length > 0 && (
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
                      doors, and two buttons reading the city alone would not
                      tell them apart. */}
                  {one.precursor ? t("ui-doors-precursor") : one.name}
                  {!one.precursor && one.city ? ` · ${one.city}` : ""}
                </button>
              ))}
            </div>
          )}
        </>
      )}
      {trouble && <p className="trouble">{trouble}</p>}
      <div className="row last">
        <button className="quiet" onClick={onBack} disabled={busy}>
          {t("ui-doors-back")}
        </button>
      </div>
    </section>
  );
}

/**
 * The city of the chosen printer: what it gives and what it asks, and the
 * button that prints a body in it.
 *
 * Its name is the heading -- a door is chosen by the city one comes out into,
 * and repeating the machine's name over a table that says nothing about the
 * machine only pushed the city a line down. A door with no city round it has
 * no name to put there but its own.
 */
export function Chosen({
  door,
  busy,
  trouble,
  onEnter,
  onBack,
}: {
  door: Door;
  busy: boolean;
  trouble?: string | null;
  onEnter: (node: string) => void;
  onBack: () => void;
}) {
  return (
    <section className="doors-step city">
      <h1>{door.city ?? (door.precursor ? t("ui-doors-precursor") : door.name)}</h1>
      <section className="card flat door">
        <table>
          <tbody>
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
            {/* What the door gives (D-184, D-281). Shown for a city door and
                only for it: citizenship is the city's to give, and a door
                with no city round it has nothing to enrol one into. The term
                is gone -- one may leave in the first minute, as long as no
                loan is open. */}
            {door.city && (
              <>
                <tr>
                  <td>{t("ui-doors-citizenship")}</td>
                  <td className="num">{t("ui-doors-citizenship-at-once")}</td>
                </tr>
                <tr>
                  <td>{t("ui-doors-tax")}</td>
                  <td className="num">{door.tax > 0 ? `${door.tax}%` : t("ui-doors-nothing")}</td>
                </tr>
              </>
            )}
          </tbody>
        </table>
        {/* The city's word: the authority writes it, not the engine (D-183).
            A silent city shows its numbers only -- there is nothing to make up
            on its behalf. */}
        {door.about && <p className="say">«{door.about}»</p>}
      </section>
      {trouble && <p className="trouble">{trouble}</p>}
      <div className="row last">
        <button className="quiet" onClick={onBack} disabled={busy}>
          {t("ui-doors-back")}
        </button>
        <button onClick={() => onEnter(door.node)} disabled={busy}>
          {t("ui-doors-print-here")}
        </button>
      </div>
    </section>
  );
}
