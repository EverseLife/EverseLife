// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Registration in five steps (D-187): email and password -> line -> character
 * -> printer -> city. Everything goes to the server as one command at the last
 * step: there is no half-account, and a refusal on any field leaves the world
 * untouched.
 *
 * The step order is not accidental: first what is about the account, then
 * what is about the world. The line before the character, because the name
 * and description are written for somebody already. The door last: it is the
 * first **game** decision (D-182), and before it the player must know who they are.
 *
 * The door is two steps and not one, because it is two questions. **Which
 * machine** is a question about a planet, and it is answered on the globe:
 * that step is a heading and a sphere. **Which city** is a question about
 * people, and it is answered on a card of numbers -- one that opens only once
 * a printer is chosen, and would otherwise stand empty beside the globe
 * telling a newcomer to choose something first. Going back from the city is
 * going back to the planet.
 */

import { type FormEvent, useEffect, useState } from "react";
import * as api from "../api";
import type { Door, Enrollment, Line } from "../api";
import { t } from "../locale";
import { Logo } from "../Logo";
import { useNarrow } from "../narrow";
import { Chosen, Doors } from "./Doors";
import { Secret } from "./Secret";

const STEPS = [
  "ui-register-step-account",
  "ui-register-step-line",
  "ui-register-step-character",
  "ui-register-step-printer",
  "ui-register-step-city",
] as const;
type Step = 0 | 1 | 2 | 3 | 4;

//: The limits are the same as the server's (`runtime.py`): the client hints
//: earlier, the server decides. A divergence here is an inconvenience, not a hole.
const PASSWORD_MIN = 8;
const NAME_LIMIT = 24;
const SURNAME_LIMIT = 32;
const ABOUT_LIMIT = 600;
const AGE = { min: 16, max: 120 };

type Props = {
  busy: boolean;
  trouble: string | null;
  onSubmit: (application: Enrollment) => Promise<unknown>;
  /** Whether the email is free: asked at the first step, refused there
   *  rather than at the door, four steps on. Resolves to whether it is. */
  onCheck: (email: string) => Promise<boolean>;
  /** The doors, once read: the globe beside the form draws them (D-319). */
  onDoors: (doors: Door[] | null) => void;
  /** The door chosen on the globe or in the list -- the screen's, not the step's. */
  picked: string | null;
  onPick: (node: string | null) => void;
  onBack: () => void;
};

export function Register({
  busy,
  trouble,
  onSubmit,
  onCheck,
  onDoors,
  picked,
  onPick,
  onBack,
}: Props) {
  const [step, setStep] = useState<Step>(0);
  //: On a phone the globe is the background of the whole screen (D-319), and
  //: at the step of the doors it is the step itself: the way in gets out of
  //: its way until a door is chosen on the planet.
  const narrow = useNarrow();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [line, setLine] = useState<Line["id"] | null>(null);
  //: Which line the tabs are showing. Not the same as the line chosen: one
  //: reads both before choosing either, and the choice is the button below.
  const [reading, setReading] = useState<Line["id"] | null>(null);
  const [name, setName] = useState("");
  const [surname, setSurname] = useState("");
  const [age, setAge] = useState("");
  const [about, setAbout] = useState("");

  const [lines, setLines] = useState<Line[] | null>(null);
  const [doors, setDoors] = useState<Door[] | null>(null);
  const [local, setLocal] = useState<string | null>(null);

  //: Catalogs are read when reached: lines on the second step, doors on the
  //: fourth. Whoever quit on the first loaded nothing extra.

  useEffect(() => {
    if (step === 1 && lines === null) {
      api.lines().then((r) => setLines(r.lines)).catch((e) => setLocal(String(e)));
    }
    if (step >= 3 && doors === null) {
      api.doors().then((r) => setDoors(r.doors)).catch((e) => setLocal(String(e)));
    }
  }, [step, lines, doors]);
  //: The globe beside the form shows the doors while a door is the question --
  //: on the step that chooses one and on the step that reads its city, where
  //: the mark stays lit to say which planet one is looking at.
  useEffect(() => {
    onDoors(step >= 3 ? doors : null);
  }, [step, doors, onDoors]);
  //: A printer chosen on the globe **is** that step answered: the city it
  //: stands in is the next one, and nobody has to press anything to be told
  //: so. Going back unchooses (below), so this cannot bounce the player
  //: forward again the moment they step off it.
  useEffect(() => {
    if (step === 3 && picked) {
      //: The same clearing every other change of step does (`go`): a refusal
      //: from the step behind is not a refusal of the one ahead.
      setLocal(null);
      setStep(4);
    }
  }, [step, picked]);
  //: And a key that names no door of this world is not a step at all: back to
  //: the planet, which is the only place a key comes from. Unreachable while
  //: the only two things that set one are the globe and the names beside it --
  //: and worth its three lines anyway, because the shape of that failure is a
  //: player locked on the last screen before the world.
  useEffect(() => {
    if (step === 4 && doors !== null && !doors.some((one) => one.node === picked)) {
      onPick(null);
      setStep(3);
    }
  }, [step, doors, picked, onPick]);

  const go = (to: Step) => {
    setLocal(null);
    setStep(to);
  };

  const credentials = (e: FormEvent) => {
    e.preventDefault();
    const address = email.trim();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(address)) return setLocal(t("ui-register-bad-email"));
    if (password.length < PASSWORD_MIN) {
      return setLocal(t("ui-register-short-password", { min: PASSWORD_MIN }));
    }
    if (password !== again) return setLocal(t("ui-register-password-mismatch"));
    setLocal(null);
    //: The server says whether the address is free before the other three
    //: steps are walked: a taken one is refused here, in its own words.
    void onCheck(address).then((free) => {
      if (free) go(1);
    });
  };

  const character = (e: FormEvent) => {
    e.preventDefault();
    const trimmedName = name.trim().replace(/\s+/g, " ");
    if (!trimmedName) return setLocal(t("ui-register-no-name"));
    if (trimmedName.length > NAME_LIMIT) {
      return setLocal(t("ui-register-long-name", { limit: NAME_LIMIT }));
    }
    if (surname.trim().length > SURNAME_LIMIT) {
      return setLocal(t("ui-register-long-surname", { limit: SURNAME_LIMIT }));
    }
    if (age !== "") {
      const n = Number(age);
      if (!Number.isInteger(n) || n < AGE.min || n > AGE.max) {
        return setLocal(t("ui-register-age-range", { min: AGE.min, max: AGE.max }));
      }
    }
    if (about.length > ABOUT_LIMIT) {
      return setLocal(t("ui-register-long-about", { limit: ABOUT_LIMIT }));
    }
    setName(trimmedName);
    go(3);
  };

  const finish = (node: string) => {
    if (!line) return go(1);
    void onSubmit({
      email: email.trim(),
      password,
      password_again: again,
      line,
      name,
      surname: surname.trim(),
      age: age === "" ? null : Number(age),
      about: about.trim(),
      node,
    });
  };

  const error = local ?? trouble;
  //: On a phone the printer step is the planet and a heading over it: the
  //: words that would explain the choosing stand between the player and the
  //: thing they are choosing from.
  const bare = narrow && step === 3;
  //: The door being read at the city step, if the key still names one: a key
  //: from before a reload may name a door this world no longer has.
  const chosen = (doors ?? []).find((one) => one.node === picked) ?? null;
  //: The line under the tabs: the one being read, else the first playable one
  //: -- the alpha has one, and opening on a promise would read as the offer.
  const shown =
    lines?.find((l) => l.id === reading) ?? lines?.find((l) => l.playable) ?? lines?.[0] ?? null;

  return (
    <main className={`entry auth${bare ? " bare" : ""}`}>
      {!bare && (
        <div className="brand">
          <Logo height={72} />
        </div>
      )}

      <ol className="steps" aria-label={t("ui-register-steps-label")}>
        {STEPS.map((label, i) => (
          <li
            key={label}
            className={i === step ? "now" : i < step ? "done" : ""}
            aria-current={i === step ? "step" : undefined}
          >
            <span className="n">{i + 1}</span> {t(label)}
          </li>
        ))}
      </ol>

      {step === 0 && (
        <form className="card" onSubmit={credentials}>
          <h1>{t("ui-register-account")}</h1>
          <label>
            <span>{t("ui-register-email")}</span>
            <input
              type="email"
              autoComplete="username"
              placeholder="you@example.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={busy}
              autoFocus
            />
          </label>
          <label>
            <span>{t("ui-register-password")}</span>
            <Secret
              value={password}
              onChange={setPassword}
              autoComplete="new-password"
              placeholder={t("ui-register-password-hint", { min: PASSWORD_MIN })}
              disabled={busy}
            />
          </label>
          <label>
            <span>{t("ui-register-again")}</span>
            <Secret
              value={again}
              onChange={setAgain}
              autoComplete="new-password"
              placeholder={t("ui-register-again-hint")}
              disabled={busy}
              invalid={again.length > 0 && again !== password}
            />
          </label>
          {error && <p className="trouble">{error}</p>}
          <div className="row">
            <button type="button" className="quiet" onClick={onBack} disabled={busy}>
              {t("ui-register-to-login")}
            </button>
            <button type="submit" disabled={busy}>
              {t("ui-register-next")}
            </button>
          </div>
        </form>
      )}

      {step === 1 && (
        <section className="lines-step">
          <h1>{t("ui-register-line")}</h1>
          <p className="note center">{t("ui-register-line-note")}</p>
          {lines === null ? (
            <p className="note center">…</p>
          ) : lines.length === 0 ? (
            //: A world with no lines is a world not yet built -- say so, the
            //: way the door step says it of a world with no doors.
            <p className="trouble">{t("ui-register-lines-empty")}</p>
          ) : (
            <>
              {/* Tabs, not cards side by side: two lines fitted, and a third
                  would not -- and the step is the width of the three around
                  it now. One is read at a time, and the tab says which. */}
              <div className="row tabs">
                {lines.map((l) => (
                  <button
                    key={l.id}
                    type="button"
                    className={l.id === shown?.id ? "" : "quiet"}
                    aria-pressed={l.id === shown?.id}
                    onClick={() => setReading(l.id)}
                    disabled={busy}
                  >
                    {l.name}
                  </button>
                ))}
              </div>
              {shown && (
                <section className={`card flat line${shown.playable ? "" : " soon"}`}>
                  <p className="note">{shown.world}</p>
                  <p>{shown.summary}</p>
                  <ul>
                    {shown.traits.map((trait) => (
                      <li key={trait}>{trait}</li>
                    ))}
                  </ul>
                  <table>
                    <tbody>
                      <tr>
                        <td>{t("ui-register-line-players")}</td>
                        <td className="num">{shown.playable ? shown.players : "—"}</td>
                      </tr>
                      <tr>
                        <td>{t("ui-register-line-world")}</td>
                        <td className="num">{shown.world}</td>
                      </tr>
                    </tbody>
                  </table>
                </section>
              )}
            </>
          )}
          {error && <p className="trouble">{error}</p>}
          {/* The step's last row, like every other step's: back on the left,
              on at the right edge -- and a line still in the works says so
              where its button would be (D-104): a promise, not a stub. */}
          <div className="row last">
            <button type="button" className="quiet" onClick={() => go(0)} disabled={busy}>
              {t("ui-register-back")}
            </button>
            {shown &&
              (shown.playable ? (
                <button
                  type="button"
                  onClick={() => {
                    setLine(shown.id);
                    go(2);
                  }}
                  disabled={busy}
                >
                  {t("ui-register-pick")}
                </button>
              ) : (
                <span className="soon-tag">{t("ui-register-soon")}</span>
              ))}
          </div>
        </section>
      )}

      {step === 2 && (
        <form className="card" onSubmit={character}>
          <h1>{t("ui-register-character")}</h1>
          <label>
            <span>{t("ui-register-name")}</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("ui-register-name-hint")}
              maxLength={NAME_LIMIT}
              disabled={busy}
              autoFocus
            />
          </label>
          <p className="note">{t("ui-register-name-note")}</p>
          <label>
            <span>{t("ui-register-surname")}</span>
            <input
              value={surname}
              onChange={(e) => setSurname(e.target.value)}
              maxLength={SURNAME_LIMIT}
              disabled={busy}
            />
          </label>
          <label>
            <span>{t("ui-register-age")}</span>
            <input
              type="number"
              min={AGE.min}
              max={AGE.max}
              value={age}
              onChange={(e) => setAge(e.target.value)}
              placeholder="—"
              disabled={busy}
            />
          </label>
          <label>
            <span>{t("ui-register-about")}</span>
            <textarea
              value={about}
              onChange={(e) => setAbout(e.target.value)}
              rows={4}
              maxLength={ABOUT_LIMIT}
              placeholder={t("ui-register-about-hint")}
              disabled={busy}
            />
          </label>
          {error && <p className="trouble">{error}</p>}
          <div className="row">
            <button type="button" className="quiet" onClick={() => go(1)} disabled={busy}>
              {t("ui-register-back")}
            </button>
            <button type="submit" disabled={busy || !name.trim()}>
              {t("ui-register-next")}
            </button>
          </div>
        </form>
      )}

      {/* The step draws itself while the doors are still coming, and if they
          never come: a bare "…" would be a screen with no way off it, and on
          a phone the way in does not even take a press in that state. */}
      {step === 3 && (
        <Doors
          doors={doors}
          overGlobe={bare}
          name={name}
          busy={busy}
          trouble={error}
          picked={picked}
          onPick={onPick}
          onBack={() => go(2)}
        />
      )}

      {step === 4 &&
        (chosen === null ? (
          //: A blink at most: the effect above puts the player back on the
          //: planet as soon as the doors are known.
          <p className="note center">…</p>
        ) : (
          <Chosen
            door={chosen}
            busy={busy}
            trouble={error}
            onEnter={finish}
            onBack={() => {
              //: Back to the planet, and to no printer chosen: the mark one
              //: pressed is the answer to the step behind, and stepping off
              //: the city asks the question again.
              onPick(null);
              go(3);
            }}
          />
        ))}
    </main>
  );
}
