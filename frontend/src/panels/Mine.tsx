/**
 * Забой: «Свод» (D-143).
 *
 * Три кнопки и рычаг темпа. Устойчивость свода не показывается никогда — её
 * нет в ответе вовсе, а признак врёт на `mine.sign_noise` и **не меняется,
 * пока не ударишь**: иначе среднее по прочтениям выдало бы скрытое число.
 *
 * Сессия открывается только после платы устройства, и считает её браузер.
 */

import { useState } from "react";
import type { Look, Session, Sight } from "../api";
import { solve, type PowSettings } from "../pow";

type Props = {
  look: Look;
  session: Session;
  pow: PowSettings | null;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

export function Mine({ look, session, pow, busy, act }: Props) {
  const [считаю, setСчитаю] = useState(false);
  const сцена = look.mining as Sight | null | undefined;
  const жила = look.veins?.[0];

  const начать = () =>
    act(async () => {
      if (!жила || !pow) throw new Error("здесь нет жилы");
      setСчитаю(true);
      try {
        const задача = await session.send("pow.challenge");
        const ответ = await solve(session.account, String(задача.nonce), pow);
        await session.send("mine.start", {
          challenge: задача.challenge,
          answer: ответ,
          vein: жила.id,
          tool: look.inventory.find((t) => t.goods.includes("кирка"))?.id,
        });
      } finally {
        setСчитаю(false);
      }
    });

  return (
    <section>
      <h2>Забой</h2>
      {!сцена && (
        <>
          <p className="note">
            {жила
              ? `Жила: ${жила.resource}, богатство ${жила.richness.toFixed(0)}`
              : "В этом узле жилы нет"}
          </p>
          <button onClick={начать} disabled={busy || считаю || !жила}>
            {считаю ? "считаю плату устройства…" : "Начать сессию"}
          </button>
          <p className="note">
            Одна оценка Argon2id на сессию: {pow?.memoryMib} МБ, {pow?.iterations} прохода.
            Считает ваше устройство — это налог на масштаб, а не на вас (D-110).
          </p>
        </>
      )}

      {сцена && (
        <>
          <p className="sign">{сцена.sign}</p>
          <table>
            <tbody>
              <tr>
                <td>добыто</td>
                <td className="num">{сцена.mined.toFixed(3)}</td>
              </tr>
              <tr>
                <td>ударов</td>
                <td className="num">{сцена.swings}</td>
              </tr>
              <tr>
                <td>крепей</td>
                <td className="num">{сцена.timbers}</td>
              </tr>
            </tbody>
          </table>

          {сцена.state === "active" ? (
            <div className="row">
              <button onClick={() => act(() => session.send("mine.swing"))} disabled={busy}>
                Бить
              </button>
              <button onClick={() => act(() => session.send("mine.timber"))} disabled={busy}>
                Ставить крепь
              </button>
              <button onClick={() => act(() => session.send("mine.leave"))} disabled={busy}>
                Уйти
              </button>
              <button
                className="quiet"
                onClick={() =>
                  act(() =>
                    session.send("mine.pace", {
                      pace: сцена.pace === "fast" ? "steady" : "fast",
                    }),
                  )
                }
                disabled={busy}
              >
                темп: {сцена.pace === "fast" ? "быстрый" : "ровный"}
              </button>
            </div>
          ) : (
            <p className="trouble">
              {сцена.state === "collapsed"
                ? "Обрушение. Всё добытое за сессию потеряно."
                : "Сессия закрыта."}
            </p>
          )}
          <p className="note">
            Крепь стоит бруса и верёвки, быстрый темп даёт больше выхода и больше
            просадки. Заученной последовательности нет: оптимум двигается вместе
            с ценой крепи.
          </p>
        </>
      )}
    </section>
  );
}
