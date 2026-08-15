/**
 * Economy -- a state tab of the sidebar (D-124, D-140).
 *
 * Visible only to state officials: the figures one governs by -- the
 * treasury, the city panel, laws in force and the world summary. Reading is
 * remote (D-140); **changing** laws from here is not allowed -- authority is
 * in-person and decides in the administration (D-155).
 */


import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import type { CityPanel, CityView, Look, Session } from "../api";
import { Panel } from "./Admin";

type Props = { look: Look; session: Session; busy: boolean };

export function Economy({ look, session, busy }: Props) {
  const [city, setCity] = useState<CityView | null>(null);
  const [panel, setPanel] = useState<CityPanel | null>(null);
  const [world, setWorld] = useState<Record<string, number>>({});

  const reload = useCallback(async () => {
    try {
      const summary = await session.send("city.survey");
      setCity((summary.city as CityView) ?? null);
      const snapshot = await session.send("city.panel");
      setPanel((snapshot.panel as CityPanel) ?? null);
      const metrics = await session.send("world.metrics");
      setWorld((metrics.metrics as Record<string, number>) ?? {});
    } catch {
      setCity(null);
      setPanel(null);
    }
  }, [session]);

  useEffect(() => {
    void reload();
  }, [reload, look.node?.key]);

  if (!city) {
    return <p className="note">Вы вне города: за стенами законов нет.</p>;
  }

  const prices = Object.entries(world).filter(([k]) => k.startsWith("price."));

  return (
    <div>
      <p className="sign">
        {city.name} · казна {api.tk(city.treasury)} ₭
      </p>
      <Panel panel={panel} />

      <h3>Деньги мира</h3>
      <table>
        <tbody>
          <tr>
            <td>масса ТК</td>
            <td className="num">{(world["money.total"] ?? 0).toFixed(2)}</td>
          </tr>
          <tr>
            <td>медиана счёта</td>
            <td className="num">{(world["money.median"] ?? 0).toFixed(2)}</td>
          </tr>
          <tr>
            <td>неравенство (Джини)</td>
            <td className="num">{(world["money.gini"] ?? 0).toFixed(2)}</td>
          </tr>
          <tr>
            <td>сделок за сутки</td>
            <td className="num">{world["trades.count"] ?? 0}</td>
          </tr>
        </tbody>
      </table>

      {prices.length > 0 && (
        <>
          <h3>Цены за сутки</h3>
          <table>
            <tbody>
              {prices.map(([key, price]) => (
                <tr key={key}>
                  <td>{key.slice("price.".length)}</td>
                  <td className="num">{price.toFixed(2)} ₭</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h3>По каким правилам живём</h3>
      <table>
        <tbody>
          {Object.entries(city.laws)
            .filter(([, law]) => law.value && law.value !== "нет")
            .map(([key, law]) => (
              <tr key={key}>
                <td title={law.note ?? ""}>{law.name}</td>
                <td className="num">
                  <b>{law.value}</b>
                  {law.unit && <span className="note"> {law.unit}</span>}
                </td>
                <td className="note">{law.own ? "решение города" : "умолчание"}</td>
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
