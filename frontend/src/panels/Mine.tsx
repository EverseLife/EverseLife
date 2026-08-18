/**
 * The face: "Roof" (D-143).
 *
 * Three buttons and a pace lever. Roof stability is never shown -- it is not
 * in the reply at all, and the sign lies by `mine.sign_noise` and **does not
 * change until you swing**: otherwise the average of readings would yield the hidden number.
 *
 * The session opens only after the device fee, and the browser computes it.
 */


import { useState } from "react";
import type { Look, Session, Sight } from "../api";
import { solve, type PowSettings } from "../pow";
import { Rule } from "../Rule";
import { Refusal, useActions } from "../actions";

type Props = {
  look: Look;
  session: Session;
  pow: PowSettings | null;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

export function Mine({ look, session, pow }: Omit<Props, "busy" | "act">) {
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const [computing, setComputing] = useState(false);
  const scene = look.mining as Sight | null | undefined;
  const vein = look.veins?.[0];

  const start = () =>
    act(async () => {
      if (!vein || !pow) throw new Error("здесь нет жилы");
      setComputing(true);
      try {
        const challenge = await session.send("pow.challenge");
        const answer = await solve(session.account, String(challenge.nonce), pow);
        await session.send("mine.start", {
          challenge: challenge.challenge,
          answer: answer,
          vein: vein.id,
          tool: look.inventory.find((t) => t.goods.includes("кирка"))?.id,
        });
      } finally {
        setComputing(false);
      }
    });

  return (
    <section>
      <Refusal of={acting} />
      <h2>
        Забой
        <Rule>
          Крепь стоит бруса и верёвки, быстрый темп даёт больше выхода и больше
          просадки. Заученной последовательности нет: оптимум двигается вместе с ценой
          крепи.
        </Rule>
      </h2>
      {!scene && (
        <>
          <p className="note">
            {vein
              ? `Жила: ${vein.resource}, богатство ${vein.richness.toFixed(0)}`
              : "В этом узле жилы нет"}
          </p>
          <button onClick={start} disabled={busy || computing || !vein}>
            {computing ? "считаю плату устройства…" : "Начать сессию"}
          </button>
          <p className="note">
            Одна оценка Argon2id на сессию: {pow?.memoryMib} МБ, {pow?.iterations} прохода.
            Считает ваше устройство — это налог на масштаб, а не на вас.
          </p>
        </>
      )}

      {scene && (
        <>
          <p className="sign">{scene.sign}</p>
          <table>
            <tbody>
              <tr>
                <td>добыто</td>
                <td className="num">{scene.mined.toFixed(3)}</td>
              </tr>
              <tr>
                <td>ударов</td>
                <td className="num">{scene.swings}</td>
              </tr>
              <tr>
                <td>крепей</td>
                <td className="num">{scene.timbers}</td>
              </tr>
            </tbody>
          </table>

          {scene.state === "active" ? (
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
                      pace: scene.pace === "fast" ? "steady" : "fast",
                    }),
                  )
                }
                disabled={busy}
              >
                темп: {scene.pace === "fast" ? "быстрый" : "ровный"}
              </button>
            </div>
          ) : (
            <p className="trouble">
              {scene.state === "collapsed"
                ? "Обрушение. Всё добытое за сессию потеряно."
                : "Сессия закрыта."}
            </p>
          )}
        </>
      )}
    </section>
  );
}
