/**
 * Регистрация в четыре шага (D-187): почта и пароль → линия → персонаж →
 * дверь. Серверу всё уходит одной командой на последнем шаге: половины
 * аккаунта не бывает, и отказ на любом поле оставляет мир нетронутым.
 *
 * Порядок шагов не случаен: сперва то, что про аккаунт, потом то, что про мир.
 * Линия — раньше персонажа, потому что имя и описание пишутся уже кому-то.
 * Дверь — последней: это первое **игровое** решение (D-182), и до него игрок
 * должен знать, кто он.
 */

import { type FormEvent, useEffect, useState } from "react";
import * as api from "../api";
import type { Door, Enrollment, Line } from "../api";
import { Logo } from "../Logo";
import { Doors } from "./Doors";
import { Secret } from "./Secret";

const STEPS = ["аккаунт", "линия", "персонаж", "город"] as const;
type Step = 0 | 1 | 2 | 3;

//: Пределы те же, что у сервера (`runtime.py`): клиент подсказывает раньше,
//: сервер решает. Расхождение здесь — неудобство, а не дыра.
const PASSWORD_MIN = 8;
const NAME_LIMIT = 24;
const SURNAME_LIMIT = 32;
const ABOUT_LIMIT = 600;
const AGE = { min: 16, max: 120 };

type Props = {
  busy: boolean;
  trouble: string | null;
  onSubmit: (заявка: Enrollment) => Promise<void>;
  onBack: () => void;
};

export function Register({ busy, trouble, onSubmit, onBack }: Props) {
  const [step, setStep] = useState<Step>(0);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [line, setLine] = useState<Line["id"] | null>(null);
  const [name, setName] = useState("");
  const [surname, setSurname] = useState("");
  const [age, setAge] = useState("");
  const [about, setAbout] = useState("");

  const [lines, setLines] = useState<Line[] | null>(null);
  const [двери, setДвери] = useState<Door[] | null>(null);
  const [local, setLocal] = useState<string | null>(null);

  //: Справочники читаются, когда до них дошли: линии — на втором шаге, двери
  //: — на четвёртом. Тот, кто бросил на первом, ничего лишнего не грузил.
  useEffect(() => {
    if (step === 1 && lines === null) {
      api.lines().then((r) => setLines(r.lines)).catch((e) => setLocal(String(e)));
    }
    if (step === 3 && двери === null) {
      api.doors().then((r) => setДвери(r.doors)).catch((e) => setLocal(String(e)));
    }
  }, [step, lines, двери]);

  const go = (to: Step) => {
    setLocal(null);
    setStep(to);
  };

  const credentials = (e: FormEvent) => {
    e.preventDefault();
    const адрес = email.trim();
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(адрес)) return setLocal("почта выглядит неправильно");
    if (password.length < PASSWORD_MIN) return setLocal(`пароль короче ${PASSWORD_MIN} знаков`);
    if (password !== again) return setLocal("пароли не совпадают");
    go(1);
  };

  const character = (e: FormEvent) => {
    e.preventDefault();
    const имя = name.trim().replace(/\s+/g, " ");
    if (!имя) return setLocal("имя не названо");
    if (имя.length > NAME_LIMIT) return setLocal(`имя длиннее ${NAME_LIMIT} знаков`);
    if (surname.trim().length > SURNAME_LIMIT) {
      return setLocal(`фамилия длиннее ${SURNAME_LIMIT} знаков`);
    }
    if (age !== "") {
      const n = Number(age);
      if (!Number.isInteger(n) || n < AGE.min || n > AGE.max) {
        return setLocal(`возраст от ${AGE.min} до ${AGE.max}`);
      }
    }
    if (about.length > ABOUT_LIMIT) return setLocal(`описание длиннее ${ABOUT_LIMIT} знаков`);
    setName(имя);
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

  const ошибка = local ?? trouble;

  return (
    <main className="entry auth">
      <div className="brand">
        <Logo height={36} />
      </div>

      <ol className="steps" aria-label="шаги регистрации">
        {STEPS.map((label, i) => (
          <li
            key={label}
            className={i === step ? "now" : i < step ? "done" : ""}
            aria-current={i === step ? "step" : undefined}
          >
            <span className="n">{i + 1}</span> {label}
          </li>
        ))}
      </ol>

      {step === 0 && (
        <form className="card" onSubmit={credentials}>
          <h1>Аккаунт</h1>
          <label>
            <span>почта</span>
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
            <span>пароль</span>
            <Secret
              value={password}
              onChange={setPassword}
              autoComplete="new-password"
              placeholder={`не короче ${PASSWORD_MIN} знаков`}
              disabled={busy}
            />
          </label>
          <label>
            <span>ещё раз</span>
            <Secret
              value={again}
              onChange={setAgain}
              autoComplete="new-password"
              placeholder="повторите пароль"
              disabled={busy}
              invalid={again.length > 0 && again !== password}
            />
          </label>
          {ошибка && <p className="trouble">{ошибка}</p>}
          <div className="row">
            <button type="button" className="quiet" onClick={onBack} disabled={busy}>
              ← ко входу
            </button>
            <button type="submit" disabled={busy}>
              Дальше →
            </button>
          </div>
        </form>
      )}

      {step === 1 && (
        <section className="wide">
          <h1>Линия</h1>
          <p className="note center">
            Кем вы напечатаны. В альфе играбельна одна линия; вторая видна как
            обещание, а не заглушка (D-104).
          </p>
          {lines === null ? (
            <p className="note center">…</p>
          ) : (
            <div className="lines">
              {lines.map((l) => (
                <section
                  key={l.id}
                  className={`line${l.playable ? "" : " soon"}${line === l.id ? " picked" : ""}`}
                >
                  <h2>{l.name}</h2>
                  <p className="note">{l.world}</p>
                  <p>{l.summary}</p>
                  <ul>
                    {l.traits.map((t) => (
                      <li key={t}>{t}</li>
                    ))}
                  </ul>
                  <table>
                    <tbody>
                      <tr>
                        <td>играют</td>
                        <td className="num">{l.playable ? l.players : "—"}</td>
                      </tr>
                      <tr>
                        <td>мир</td>
                        <td className="num">{l.world}</td>
                      </tr>
                    </tbody>
                  </table>
                  <div className="row">
                    {l.playable ? (
                      <button
                        type="button"
                        onClick={() => {
                          setLine(l.id);
                          go(2);
                        }}
                        disabled={busy}
                      >
                        Выбрать
                      </button>
                    ) : (
                      <span className="soon-tag">Ещё в разработке</span>
                    )}
                  </div>
                </section>
              ))}
            </div>
          )}
          {ошибка && <p className="trouble">{ошибка}</p>}
          <div className="row">
            <button type="button" className="quiet" onClick={() => go(0)} disabled={busy}>
              ← назад
            </button>
          </div>
        </section>
      )}

      {step === 2 && (
        <form className="card" onSubmit={character}>
          <h1>Персонаж</h1>
          <label>
            <span>имя</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="как вас будут звать"
              maxLength={NAME_LIMIT}
              disabled={busy}
              autoFocus
            />
          </label>
          <p className="note">
            Имя уникально и не меняется никогда: на нём держится репутация
            (D-011). Всё остальное можно поправить потом в кабинете.
          </p>
          <label>
            <span>фамилия</span>
            <input
              value={surname}
              onChange={(e) => setSurname(e.target.value)}
              maxLength={SURNAME_LIMIT}
              disabled={busy}
            />
          </label>
          <label>
            <span>возраст</span>
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
            <span>описание</span>
            <textarea
              value={about}
              onChange={(e) => setAbout(e.target.value)}
              rows={4}
              maxLength={ABOUT_LIMIT}
              placeholder="внешность, характер, откуда родом — как хотите"
              disabled={busy}
            />
          </label>
          {ошибка && <p className="trouble">{ошибка}</p>}
          <div className="row">
            <button type="button" className="quiet" onClick={() => go(1)} disabled={busy}>
              ← назад
            </button>
            <button type="submit" disabled={busy || !name.trim()}>
              Дальше →
            </button>
          </div>
        </form>
      )}

      {step === 3 &&
        (двери === null ? (
          <p className="note center">…</p>
        ) : (
          <Doors
            двери={двери}
            имя={name}
            busy={busy}
            trouble={ошибка}
            onPick={finish}
            onBack={() => go(2)}
          />
        ))}
    </main>
  );
}
