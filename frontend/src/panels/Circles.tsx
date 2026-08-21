// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Circles -- a tab of the main window (D-043).
 *
 * In one workshop eight people discuss different things: a circle lets one
 * talk about one's own without disturbing the rest. **Groups are visible,
 * their content is not**: from outside one sees who whispers with whom, and
 * that is a strong social signal -- "these ones are arranging something".
 *
 * The tab is in-person, like the conversation itself: en route there are no
 * circles, because there is no room in which they gather.
 */

import { useCallback, useEffect, useState } from "react";
import type { Circle, Session } from "../api";
import { Rule } from "../Rule";
import { Refusal, useActions } from "../actions";

type Props = {
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  /** The location key: changed -- the circles are different. */
  place: string;
};

export function Circles({ session, place }: Omit<Props, "busy" | "act">) {
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

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
      <Refusal of={acting} />
      <h2>
        Кружки
        <Rule>
          Подошедшего к кружку видно всем. Вход свободный: закрытых кружков нет —
          приватность в этом мире стоит места, а не кнопки.
        </Rule>
      </h2>

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
        чужих ушей. Вполголоса — тише, но и своих слышно хуже.
      </p>
    </section>
  );
}
