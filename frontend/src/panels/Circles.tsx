/**
 * Кружки — таб основного окна (D-043).
 *
 * В одной мастерской восемь человек обсуждают разное: кружок позволяет
 * говорить о своём, не мешая остальным. **Группы видны, их содержание — нет**:
 * снаружи видно, кто с кем шепчется, и это сильный социальный сигнал — «эти
 * о чём-то договариваются».
 *
 * Таб присутственный, как и сам разговор: в пути кружков нет, потому что нет
 * и комнаты, в которой они собираются.
 */

import { useCallback, useEffect, useState } from "react";
import type { Circle, Session } from "../api";

type Props = {
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  /** Ключ локации: сменилась — кружки другие. */
  place: string;
};

export function Circles({ session, busy, act, place }: Props) {
  const [circles, setCircles] = useState<Circle[]>([]);
  const [name, setName] = useState("");

  const listen = useCallback(async () => {
    try {
      const answer = await session.send("chat.hear");
      setCircles(answer.circles as Circle[]);
    } catch {
      //: В пути слушать нечего — таб всё равно недоступен.
    }
  }, [session]);

  useEffect(() => {
    setCircles([]);
    void listen();
    const timer = setInterval(() => void listen(), 4000);
    return () => clearInterval(timer);
  }, [listen, place]);

  const mine = circles.find((circle) => circle.mine);

  return (
    <section>
      <h2>Кружки</h2>

      {circles.length === 0 && (
        <p className="note">
          Никто не шепчется: весь разговор локации — общий.
        </p>
      )}

      {circles.map((circle) => (
        <div className={`row circle ${circle.mine ? "mine" : ""}`} key={circle.id}>
          <span>
            <b>{circle.name ?? "кружок без имени"}</b> · {circle.members.join(", ")}
          </span>
          {circle.mine ? (
            <button
              className="quiet"
              onClick={() => act(() => session.send("chat.leave"))}
              disabled={busy}
            >
              отойти
            </button>
          ) : (
            <button
              className="quiet"
              onClick={() => act(() => session.send("chat.join", { circle: circle.id }))}
              disabled={busy}
            >
              подойти
            </button>
          )}
        </div>
      ))}

      {!mine && (
        <div className="row">
          <input
            value={name}
            placeholder="имя кружка (можно без)"
            onChange={(e) => setName(e.target.value)}
          />
          <button
            onClick={() =>
              act(async () => {
                await session.send("chat.gather", { name: name.trim() || undefined });
                setName("");
                await listen();
              })
            }
            disabled={busy}
          >
            Собрать кружок
          </button>
        </div>
      )}

      <p className="note">
        Пока вы в кружке, ваши реплики слышат только его участники — но с шансом
        утечки: людность, размер кружка и тип места решают, долетит ли фраза до
        чужих ушей. Вполголоса — тише, но и своих слышно хуже (D-043).
      </p>
      <p className="note">
        Подошедшего к кружку видно всем. Вход свободный: закрытых кружков нет —
        приватность в этом мире стоит места, а не кнопки (D-081).
      </p>
    </section>
  );
}
