/**
 * Экономика — государственная вкладка сайдбара (D-124, D-140).
 *
 * Видна только гос.должностям: цифры, которыми правят, — казна, панель
 * города, действующие законы и сводка мира. Чтение удалённое (D-140);
 * **менять** законы отсюда нельзя — власть присутственна и решает в
 * администрации (D-155).
 */

import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import type { CityPanel, CityView, Look, Session } from "../api";
import { Panel } from "./Admin";

type Props = { look: Look; session: Session; busy: boolean };

export function Economy({ look, session, busy }: Props) {
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

  const цены = Object.entries(мир).filter(([к]) => к.startsWith("price."));

  return (
    <div>
      <p className="sign">
        {город.name} · казна {api.tk(город.treasury)} ₭
      </p>
      <Panel панель={панель} />

      <h3>Деньги мира</h3>
      <table>
        <tbody>
          <tr>
            <td>масса ТК</td>
            <td className="num">{(мир["money.total"] ?? 0).toFixed(2)}</td>
          </tr>
          <tr>
            <td>медиана счёта</td>
            <td className="num">{(мир["money.median"] ?? 0).toFixed(2)}</td>
          </tr>
          <tr>
            <td>неравенство (Джини)</td>
            <td className="num">{(мир["money.gini"] ?? 0).toFixed(2)}</td>
          </tr>
          <tr>
            <td>сделок за сутки</td>
            <td className="num">{мир["trades.count"] ?? 0}</td>
          </tr>
        </tbody>
      </table>

      {цены.length > 0 && (
        <>
          <h3>Цены за сутки</h3>
          <table>
            <tbody>
              {цены.map(([ключ, цена]) => (
                <tr key={ключ}>
                  <td>{ключ.slice("price.".length)}</td>
                  <td className="num">{цена.toFixed(2)} ₭</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h3>По каким правилам живём</h3>
      <table>
        <tbody>
          {Object.entries(город.laws)
            .filter(([, закон]) => закон.value && закон.value !== "нет")
            .map(([ключ, закон]) => (
              <tr key={ключ}>
                <td title={закон.note ?? ""}>{закон.name}</td>
                <td className="num">
                  <b>{закон.value}</b>
                  {закон.unit && <span className="note"> {закон.unit}</span>}
                </td>
                <td className="note">{закон.own ? "решение города" : "умолчание"}</td>
              </tr>
            ))}
        </tbody>
      </table>

      <button className="quiet" onClick={() => void reload()} disabled={busy}>
        Пересчитать
      </button>
      <p className="note">
        Менять законы — в администрации: власть присутственна (D-155). Вкладка
        видна только должностям: это цифры, которыми правят.
      </p>
    </div>
  );
}
