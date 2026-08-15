/**
 * Вход (D-187): знак, почта, пароль, и снизу — дорога к регистрации.
 *
 * Здесь намеренно ничего не рассказывается о мире: рассказ — дело регистрации
 * и вступления (D-182). Тот, кто входит, уже знает, куда идёт.
 */

import { type FormEvent, useState } from "react";
import { Logo } from "../Logo";
import { Secret } from "./Secret";

type Props = {
  busy: boolean;
  onLogin: (email: string, password: string) => void;
  onRegister: () => void;
  trouble: string | null;
};

export function Login({ busy, onLogin, onRegister, trouble }: Props) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (busy || !email.trim() || !password) return;
    onLogin(email.trim(), password);
  };

  return (
    <main className="entry auth">
      <div className="brand">
        <Logo height={44} />
        <p className="note">альфа</p>
      </div>

      <form className="card" onSubmit={submit}>
        <h1>Войти</h1>
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
          <Secret value={password} onChange={setPassword} disabled={busy} />
        </label>
        {trouble && <p className="trouble">{trouble}</p>}
        <div className="row">
          <button type="submit" disabled={busy || !email.trim() || !password}>
            Войти
          </button>
        </div>
      </form>

      <p className="note center">
        Ещё нет аккаунта?{" "}
        <button type="button" className="link" onClick={onRegister} disabled={busy}>
          Регистрация
        </button>
      </p>
    </main>
  );
}
