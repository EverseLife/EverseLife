/**
 * Население — государственная вкладка сайдбара (D-140, D-154).
 *
 * Видна только гос.должностям: кто живёт, кто правит, сколько людей в городе
 * и в мире. Назначать и снимать — в администрации (D-155); отсюда только
 * смотрят.
 */

import { useCallback, useEffect, useState } from "react";
import type { CityPanel, CityView, Look, Session } from "../api";

type Props = { look: Look; session: Session; busy: boolean };

export function Population({ look, session, busy }: Props) {
  const [мишень, setМишень] = useState("");
  const [город, setГород] = useState<CityView | null>(null);
  const [панель, setПанель] = useState<CityPanel | null>(null);
  const [мир, setМир] = useState<Record<string, number>>({});

  const reload = useCallback(async () => {
    try {
      const сводка = await session.send("city.survey");
      setГород((сводка.city as CityView) ?? null);
      const срез = await session.send("city.panel");
      setПанель((срез.panel as CityPanel) ?? null);
      const метрики = await session.send("world.metrics");
      setМир((метрики.metrics as Record<string, number>) ?? {});
    } catch {
      setГород(null);
      setПанель(null);
    }
  }, [session]);

  useEffect(() => {
    void reload();
  }, [reload, look.node?.key]);

  if (!город) {
    return <p className="note">Вы вне города: за стенами законов нет.</p>;
  }

  return (
    <div>
      <p className="sign">{город.name}</p>
      <table>
        <tbody>
          <tr>
            <td>личностей в мире</td>
            <td className="num">{мир["people"] ?? 0}</td>
          </tr>
          {панель && (
            <>
              <tr>
                <td>тел в городе</td>
                <td className="num">{панель.people.here}</td>
              </tr>
              <tr>
                <td>напечатано за окно</td>
                <td className="num">{панель.people.printed}</td>
              </tr>
            </>
          )}
        </tbody>
      </table>

      <h3>Должности</h3>
      {город.offices.length === 0 ? (
        <p className="note">должностей нет</p>
      ) : (
        город.offices.map((пост) => (
          <p key={пост.id}>
            <b>{пост.title}</b> · {пост.who}
            <span className="note"> · {пост.powers.join(", ")}</span>
          </p>
        ))
      )}

      <h3>Жители</h3>
      <p className="note">{город.citizens.join(" · ") || "пока никого"}</p>

      {/* Дефектная печать (D-173): по лору принтер иногда печатает людей без
          интеллекта. Репорт снижает доверие и кредит, а не убивает: необратимую
          переработку делает только внеигровой саппорт. */}
      <div className="row">
        <input
          value={мишень}
          onChange={(e) => setМишень(e.target.value)}
          placeholder="имя дефектной печати"
        />
        <button
          className="quiet"
          onClick={() => void session.send("person.report", { who: мишень })}
          disabled={busy || !мишень.trim()}
        >
          Сообщить
        </button>
        <button
          className="quiet"
          onClick={() => void session.send("person.unreport", { who: мишень })}
          disabled={busy || !мишень.trim()}
          title="отозвать свой репорт"
        >
          Отозвать
        </button>
        <span className="note">
          Репорт снижает доверие и кредитный лимит цели — не больше того. Ошиблись —
          отзовите.
        </span>
      </div>

      <button className="quiet" onClick={() => void reload()} disabled={busy}>
        Пересчитать
      </button>
      <p className="note">
        Назначать и снимать — в администрации: власть присутственна (D-155).
      </p>
    </div>
  );
}
