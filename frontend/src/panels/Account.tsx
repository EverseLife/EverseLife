/**
 * The account panel (D-187): opens from the header, where the bare name used to stand.
 *
 * **Nothing game-related** here: the account is payment and device, the
 * identity is the game. Surname, age, description, password, email change;
 * the name -- never (D-011). Logout revokes the session token.
 */


import { type FormEvent, useState } from "react";
import type { Profile, Session } from "../api";
import { DENSITIES, DENSITY_NAMES, setDensity, useDensity } from "../density";
import { Rule } from "../Rule";
import { Secret } from "./Secret";

type Props = {
  profile: Profile;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  onClose: () => void;
  onLogout: () => void;
};

const TABS = [
  { id: "who", label: "персонаж" },
  { id: "password", label: "пароль" },
  { id: "email", label: "почта" },
  //: Display density lives with the account rather than in the game: it is how
  //: this person reads a screen, not anything the world knows about them.
  { id: "view", label: "вид" },
] as const;
type Tab = (typeof TABS)[number]["id"];

export function Account({ profile, session, busy, act, onClose, onLogout }: Props) {
  const [tab, setTab] = useState<Tab>("who");
  const [surname, setSurname] = useState(profile.surname);
  const [age, setAge] = useState(profile.age === null ? "" : String(profile.age));
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
      setDone("сохранено");
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
      setDone("пароль сменён; другие сессии разлогинены");
    });
  };

  const saveEmail = (e: FormEvent) => {
    e.preventDefault();
    setDone(null);
    void act(async () => {
      await session.send("account.email", { email: email.trim(), password: confirm });
      setConfirm("");
      setDone("почта сменена");
    });
  };

  const line = profile.line === "human" ? "человек-киборг" : "нимфа";
  const s = new Date(profile.since);

  return (
    <div className="veil" role="dialog" aria-modal="true" aria-label="Аккаунт">
      <section className="intro account">
        <header className="row">
          <h2>
            {profile.name}
            {profile.surname ? ` ${profile.surname}` : ""}
          </h2>
          <span className="note">
            {line}
            {profile.age !== null ? ` · ${profile.age}` : ""} · в мире с{" "}
            {s.toLocaleDateString("ru-RU")}
          </span>
          <button className="quiet" onClick={onClose} title="закрыть" aria-label="закрыть">
            ×
          </button>
        </header>

        <nav className="row tabs">
          {TABS.map((t) => (
            <button
              key={t.id}
              className={tab === t.id ? "" : "quiet"}
              onClick={() => {
                setTab(t.id);
                setDone(null);
              }}
            >
              {t.label}
            </button>
          ))}
        </nav>

        {tab === "who" && (
          <form onSubmit={saveWho} className="card flat">
            <label>
              <span>имя</span>
              <input value={profile.name} disabled title="имя не меняется" />
            </label>
            <p className="note">Имя несменяемо: на нём держится репутация.</p>
            <label>
              <span>фамилия</span>
              <input
                value={surname}
                onChange={(e) => setSurname(e.target.value)}
                maxLength={32}
                disabled={busy}
              />
            </label>
            <label>
              <span>возраст</span>
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
              <span>описание</span>
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
                Сохранить
              </button>
              {done && <span className="note">{done}</span>}
            </div>
          </form>
        )}

        {tab === "password" && (
          <form onSubmit={savePassword} className="card flat">
            <label>
              <span>старый пароль</span>
              <Secret value={old} onChange={setOld} disabled={busy} />
            </label>
            <label>
              <span>новый пароль</span>
              <Secret
                value={fresh}
                onChange={setFresh}
                autoComplete="new-password"
                placeholder="не короче 8 знаков"
                disabled={busy}
              />
            </label>
            <label>
              <span>ещё раз</span>
              <Secret
                value={again}
                onChange={setAgain}
                autoComplete="new-password"
                placeholder="повторите"
                disabled={busy}
                invalid={again.length > 0 && again !== fresh}
              />
            </label>
            <div className="row">
              <button type="submit" disabled={busy || !old || fresh.length < 8 || fresh !== again}>
                Сменить пароль
              </button>
              {done && <span className="note">{done}</span>}
            </div>
          </form>
        )}

        {tab === "email" && (
          <form onSubmit={saveEmail} className="card flat">
            <label>
              <span>почта</span>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="username"
                disabled={busy}
              />
            </label>
            <label>
              <span>пароль</span>
              <Secret value={confirm} onChange={setConfirm} disabled={busy} />
            </label>
            <p className="note">Смена почты подтверждается паролем.</p>
            <div className="row">
              <button type="submit" disabled={busy || !email.trim() || !confirm}>
                Сменить почту
              </button>
              {done && <span className="note">{done}</span>}
            </div>
          </form>
        )}

        {tab === "view" && <View />}

        <footer className="row">
          <button className="quiet" onClick={onLogout} disabled={busy}>
            Выйти из аккаунта
          </button>
          <span className="note">Жетон этой сессии будет отозван.</span>
        </footer>
      </section>
    </div>
  );
}

/** How dense a screen this person wants. Switches freely: a setting, not a reward. */
function View() {
  const density = useDensity();
  return (
    <div className="card flat">
      <label>
        <span>
          плотность
          <Rule>
            Плотность меняет высоту строк и отступы. Размер шрифта и расположение
            элементов не меняются: плотный режим — это больше данных на экране, а не
            более мелкий текст. Переключается свободно и в любую сторону.
          </Rule>
        </span>
        <div className="row" role="group" aria-label="плотность экрана">
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
