/**
 * Станок: что на нём делают, кто за ним стоит, что чинят (D-092, D-133, D-150).
 *
 * Панель называется именем станка, а не «мастерской», и показывается **только
 * там, где этот станок стоит**. Причина не в оформлении: станок задаёт, чем
 * место является (D-106), и три станка во дворе — это три разных дела, а не
 * один список рецептов, из которого половина всё равно откажет.
 *
 * Отдельный случай — «Руками»: этому месту не нужно, и панель появляется
 * везде, где игрок вообще знает хоть один такой рецепт. Верёвка и каменная
 * кирка делаются в чистом поле, и это начало всей лестницы (D-084).
 *
 * Главное здесь — **прогноз точным числом до того, как потрачены материалы**:
 * без него игрок не свяжет действие с результатом и не выведет ни одной
 * пропорции (D-092). Поэтому «Запустить» стоит рядом с прогнозом, а не вместо.
 */

import { useState } from "react";
import * as api from "../api";
import type { Look, Plan, Session } from "../api";
import { craftableAt, stationOf } from "../recipes";

type Props = {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  /** Имя станка либо `null` — «Руками». */
  станок: string | null;
  /** Справочник вольта: грузится один раз на весь экран. */
  книга: any;
};

export function Workshop({ look, session, busy, act, станок, книга }: Props) {
  const умею = craftableAt(книга, станок, look.knows);
  const [что, setЧто] = useState<string | null>(null);
  const [сколько, setСколько] = useState(1);
  const [прогноз, setПрогноз] = useState<Plan | null>(null);
  //: «Поставить на автомат» — выбор уклада: объём и счёт за энергию против
  //: качества и внимания (D-035, D-058).
  const [автомат, setАвтомат] = useState(false);

  if (умею.length === 0) return null;
  const выбрано = что && умею.includes(что) ? что : умею[0];
  const мой_станок = (look.bench ?? []).filter((б) => б.goods === станок);
  const автоматом = станок === "Автоматический станок";

  const прогнозировать = () =>
    act(async () => {
      const ответ = await session.send("craft.plan", {
        output: выбрано,
        units: сколько,
        auto: автомат && автоматом,
      });
      setПрогноз(ответ.plan as Plan);
    });

  const запустить = () =>
    act(async () => {
      await session.send("craft.start", {
        output: выбрано,
        units: сколько,
        auto: автомат && автоматом,
      });
      setПрогноз(null);
    });

  //: Чинят и разбирают там же, где делают: у станка, на котором вещь сделана.
  const чинить = look.inventory.filter(
    (вещь) => вещь.condition < 100 && stationOf(книга, вещь.goods) === станок,
  );

  return (
    <section>
      <h2>{станок ?? "Руками"}</h2>

      {look.node?.cut_off && станок !== null && (
        <p className="trouble">
          Узел отключён за неуплату: станки не работают, пока долг не закрыт.
          Счёт — в сайдбаре, во вкладке «хозяйство».
        </p>
      )}

      {мой_станок.map((станция) => (
        <p className="note" key={станция.id}>
          {станция.quality === null ? "" : `качество ${станция.quality.toFixed(0)}`}
          {станция.condition < 100 && ` · состояние ${станция.condition.toFixed(0)}`}
          {" · "}
          {станция.busy ? (станция.mine ? "занят вами" : "занят другим") : "свободен"}
          {(look.node?.mine || look.city?.powers.includes("laws")) && (
            <>
              {" "}
              <button
                className="quiet"
                onClick={() => act(() => session.send("station.take", { item: станция.id }))}
                disabled={busy || станция.busy}
                title="забрать станок в руки"
              >
                Забрать
              </button>
            </>
          )}
        </p>
      ))}

      <div className="row">
        <select
          value={выбрано}
          onChange={(e) => {
            setЧто(e.target.value);
            setПрогноз(null);
          }}
        >
          {умею.map((имя) => (
            <option key={имя}>{имя}</option>
          ))}
        </select>
        <input
          type="number"
          min={1}
          value={сколько}
          onChange={(e) => {
            setСколько(Number(e.target.value));
            setПрогноз(null);
          }}
        />
        <button onClick={прогнозировать} disabled={busy}>
          Прогноз
        </button>
        {автоматом && (
          <label className="note">
            <input
              type="checkbox"
              checked={автомат}
              onChange={(e) => {
                setАвтомат(e.target.checked);
                setПрогноз(null);
              }}
            />{" "}
            на автомате
          </label>
        )}
      </div>

      {прогноз && (
        <div className="plan">
          <p>
            качество <b>{прогноз.quality.toFixed(1)}</b> ± {прогноз.spread.toFixed(1)}
            {" · "}потолок {прогноз.ceiling.toFixed(0)}
            {" · "}{прогноз.minutes.toFixed(1)} мин
            {" · "}потери {прогноз.waste.toFixed(1)}%
          </p>
          <p className="note">
            уйдёт:{" "}
            {Object.entries(прогноз.consumes)
              .map(([имя, сколько]) => `${имя} ${сколько.toFixed(2)}`)
              .join(", ")}
            {прогноз.auto && прогноз.energy > 0 && (
              <>
                {" "}· энергии {прогноз.energy.toFixed(0)} на{" "}
                {api.tk(прогноз.energy_cost)} ₭ по тарифу города
              </>
            )}
          </p>
          <button onClick={запустить} disabled={busy}>
            Запустить партию
          </button>
        </div>
      )}

      {чинить.length > 0 && (
        <>
          <h3>Починить или разобрать</h3>
          {чинить.map((вещь) => (
            <div className="row" key={вещь.id}>
              <span>
                {вещь.goods} · состояние {вещь.condition.toFixed(0)}
              </span>
              <button
                onClick={() => act(() => session.send("craft.repair", { item: вещь.id }))}
                disabled={busy}
              >
                Починить
              </button>
              <button
                className="quiet"
                onClick={() => act(() => session.send("craft.recycle", { item: вещь.id }))}
                disabled={busy}
              >
                Разобрать
              </button>
            </div>
          ))}
        </>
      )}

      <p className="note">
        Партия идёт офлайн и видна в сайдбаре, в «делах». За станком работает
        один: пока идёт партия, второму он не отдаётся (D-150).
      </p>
    </section>
  );
}
