// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The account tab of the sidebar (D-187, D-238).
 *
 * It used to be a modal opened from the header; the redesign gives the account
 * a tab of its own and clears the header of account controls entirely.
 *
 * **Nothing game-related** here: the account is payment and device, the
 * identity is the game. The body's readings live in the header's instrument
 * strip, not on this tab. Surname, age, description, password, email change;
 * the name -- never (D-011). Logout revokes the session token.
 */

import { type FormEvent, useState } from "react";
import { Refusal, useActions, useLocale, useSession } from "../actions";
import type { Profile } from "../api";
import { DENSITIES, DENSITY_NAMES, setDensity, useDensity } from "../density";
import { t } from "../locale";
import { Rule } from "../Rule";
import { Secret } from "./Secret";

type Props = {
  profile: Profile;
  onLogout: () => void;
};

/** How short the new password may be. The same floor as registration's. */
const PASSWORD_MIN = 8;

/** The tabs of this window, each by the message that names it. */
const TABS = [
  { id: "who", label: "ui-account-tab-who" },
  { id: "password", label: "ui-account-tab-password" },
  { id: "email", label: "ui-account-tab-email" },
  //: Display density lives with the account rather than in the game: it is how
  //: this person reads a screen, not anything the world knows about them.
  { id: "view", label: "ui-account-tab-view" },
] as const;
type Tab = (typeof TABS)[number]["id"];

export function Account({ profile, onLogout }: Props) {
  const session = useSession();
  const { locale } = useLocale();
  //: The tab's own waiting and refusal: saving a surname must not grey out
  //: the map or the chat (same rule as every sidebar panel).
  const acting = useActions();
  const { busy, act } = acting;
  const [tab, setTab] = useState<Tab>("who");
  const [surname, setSurname] = useState(profile.surname);
  const [age, setAge] = useState(profile.age == null ? "" : String(profile.age));
  const [about, setAbout] = useState(profile.about);
  const [old, setOld] = useState("");
  const [fresh, setFresh] = useState("");
  const [again, setAgain] = useState("");
  const [email, setEmail] = useState(profile.email ?? "");
  const [confirm, setConfirm] = useState("");
  const [done, setDone] = useState<string | null>(null);

  const saveWho = (e: FormEvent) => {
    e.preventDefault();
    setDone(null);
    void act(async () => {
      await session.send("account.update", {
        surname: surname.trim(),
        age: age === "" ? null : Number(age),
        about: about.trim(),
      });
      setDone(t("ui-account-saved"));
    });
  };

  const savePassword = (e: FormEvent) => {
    e.preventDefault();
    setDone(null);
    void act(async () => {
      await session.send("account.password", { old, new: fresh, new_again: again });
      setOld("");
      setFresh("");
      setAgain("");
      setDone(t("ui-account-password-saved"));
    });
  };

  const saveEmail = (e: FormEvent) => {
    e.preventDefault();
    setDone(null);
    void act(async () => {
      await session.send("account.email", { email: email.trim(), password: confirm });
      setConfirm("");
      setDone(t("ui-account-email-saved"));
    });
  };

  //: The class line as a word. The same pair stands on somebody's card, and
  //: the engine has its own display names for the lines: three sources, one
  //: word (see the report of this wave).
  const line = t(profile.line === "human" ? "ui-line-human" : "ui-line-nymph");
  const s = new Date(profile.since);

  return (
    <div className="account">
      <Refusal of={acting} />
      <p className="sign">
        {profile.name}
        {profile.surname ? ` ${profile.surname}` : ""}
        <Rule>{t("ui-account-rule")}</Rule>
      </p>
      <p className="note">
        {t("ui-account-who", {
          line,
          aged: String(profile.age != null),
          //: The screen printed the age as it stood; a real number would come
          //: back through `Intl` with a thousands separator.
          age: profile.age == null ? "" : String(profile.age),
          since: s.toLocaleDateString(locale),
        })}
      </p>

      <nav className="row tabs">
          {TABS.map((each) => (
            <button
              key={each.id}
              className={tab === each.id ? "" : "quiet"}
              aria-current={tab === each.id || undefined}
              onClick={() => {
                setTab(each.id);
                setDone(null);
              }}
            >
              {t(each.label)}
            </button>
          ))}
        </nav>

        {tab === "who" && (
          <form onSubmit={saveWho} className="card flat">
            <label>
              <span>{t("ui-account-name")}</span>
              <input value={profile.name} disabled title={t("ui-account-name-fixed")} />
            </label>
            <p className="note">{t("ui-account-name-rule")}</p>
            <label>
              <span>{t("ui-account-surname")}</span>
              <input
                value={surname}
                onChange={(e) => setSurname(e.target.value)}
                maxLength={32}
                disabled={busy}
              />
            </label>
            <label>
              <span>{t("ui-account-age")}</span>
              <input
                type="number"
                min={16}
                max={120}
                value={age}
                onChange={(e) => setAge(e.target.value)}
                disabled={busy}
              />
            </label>
            <label>
              <span>{t("ui-account-about")}</span>
              <textarea
                value={about}
                onChange={(e) => setAbout(e.target.value)}
                rows={5}
                maxLength={600}
                disabled={busy}
              />
            </label>
            <div className="row">
              <button type="submit" disabled={busy}>
                {t("ui-account-save")}
              </button>
              {done && <span className="note">{done}</span>}
            </div>
          </form>
        )}

        {tab === "password" && (
          <form onSubmit={savePassword} className="card flat">
            <label>
              <span>{t("ui-account-password-old")}</span>
              <Secret value={old} onChange={setOld} disabled={busy} />
            </label>
            <label>
              <span>{t("ui-account-password-new")}</span>
              <Secret
                value={fresh}
                onChange={setFresh}
                autoComplete="new-password"
                placeholder={t("ui-account-password-hint", { min: PASSWORD_MIN })}
                disabled={busy}
              />
            </label>
            <label>
              <span>{t("ui-account-password-again")}</span>
              <Secret
                value={again}
                onChange={setAgain}
                autoComplete="new-password"
                placeholder={t("ui-account-password-repeat")}
                disabled={busy}
                invalid={again.length > 0 && again !== fresh}
              />
            </label>
            <div className="row">
              <button
                type="submit"
                disabled={busy || !old || fresh.length < PASSWORD_MIN || fresh !== again}
              >
                {t("ui-account-password-submit")}
              </button>
              {done && <span className="note">{done}</span>}
            </div>
          </form>
        )}

        {tab === "email" && (
          <form onSubmit={saveEmail} className="card flat">
            <label>
              <span>{t("ui-account-email")}</span>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="username"
                disabled={busy}
              />
            </label>
            <label>
              <span>{t("ui-account-password")}</span>
              <Secret value={confirm} onChange={setConfirm} disabled={busy} />
            </label>
            <p className="note">{t("ui-account-email-rule")}</p>
            <div className="row">
              <button type="submit" disabled={busy || !email.trim() || !confirm}>
                {t("ui-account-email-submit")}
              </button>
              {done && <span className="note">{done}</span>}
            </div>
          </form>
        )}

        {tab === "view" && <View />}

        <footer className="row">
          <button className="quiet" onClick={onLogout} disabled={busy}>
            {t("ui-account-logout")}
          </button>
          <span className="note">{t("ui-account-logout-note")}</span>
        </footer>
    </div>
  );
}

/** How dense a screen this person wants. Switches freely: a setting, not a reward. */
function View() {
  const density = useDensity();
  return (
    <div className="card flat">
      <Language />
      <label>
        <span>
          {t("ui-account-density")}
          <Rule>{t("ui-account-density-rule")}</Rule>
        </span>
        <div className="row" role="group" aria-label={t("ui-account-density-label")}>
          {DENSITIES.map((mode) => (
            <button
              key={mode}
              type="button"
              className={density === mode ? "" : "quiet"}
              aria-pressed={density === mode}
              onClick={() => setDensity(mode)}
            >
              {DENSITY_NAMES[mode].label}
            </button>
          ))}
        </div>
      </label>
      <p className="note">{DENSITY_NAMES[density].about}</p>
    </div>
  );
}

/**
 * The language the world is read in (D-249, D-251 wave III).
 *
 * It stands with the density rather than in the game: which words a person
 * reads is how they read a screen, not anything the world knows about them.
 * The choice reaches the account, so the next login on another device opens in
 * the same language.
 *
 * The list comes from the server -- it is the one that knows which languages
 * exist. While there is only one there is nothing to choose, and a row with a
 * single pressed button in it is a control that lies about being a control:
 * the block hides itself until a second language appears, and needs no code
 * change when it does.
 */
function Language() {
  const { locale, locales, setLocale } = useLocale();
  const acting = useActions();
  if (locales.length < 2) return null;
  return (
    <>
      <Refusal of={acting} />
      <label>
        <span>
          {t("ui-account-language")}
          <Rule>{t("ui-account-language-rule")}</Rule>
        </span>
        <div className="row" role="group" aria-label={t("ui-account-language")}>
          {locales.map((code) => (
            <button
              key={code}
              type="button"
              className={locale === code ? "" : "quiet"}
              aria-pressed={locale === code}
              disabled={acting.busy}
              onClick={() => void acting.act(() => setLocale(code))}
            >
              {languageName(code, locale)}
            </button>
          ))}
        </div>
      </label>
    </>
  );
}

/**
 * A language code as a word, written in the language of whoever is reading it.
 *
 * `Intl` already knows every name in every language, so a table of ours would
 * only be a second source of truth going stale one language at a time. A
 * browser that cannot answer gets the bare code, which is still a choice a
 * person can make.
 */
function languageName(code: string, reader: string): string {
  try {
    return new Intl.DisplayNames([reader], { type: "language" }).of(code) ?? code;
  } catch {
    return code;
  }
}
