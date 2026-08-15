/**
 * A machine: what is made at it, who stands at it, what is repaired (D-092, D-133, D-150).
 *
 * The panel is named after the machine, not "workshop", and is shown **only
 * where this machine stands**. The reason is not styling: the machine sets
 * what a place is (D-106), and three machines in the yard are three different
 * jobs, not one recipe list half of which will refuse anyway.
 *
 * A separate case is "By hand": it needs no place, and the panel appears
 * wherever the player knows at least one such recipe. Rope and a stone
 * pickaxe are made in an open field, and that is the start of the whole ladder (D-084).
 *
 * The main thing here is **the forecast as an exact number before materials
 * are spent**: without it the player will not connect action with result and
 * will not derive a single proportion (D-092). So "Start" stands next to the
 * forecast, not instead of it.
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
  /** The machine's name or `null` -- "By hand". */
  machine: string | null;
  /** The vault catalog: loaded once for the whole screen. */
  book: any;
};

export function Workshop({ look, session, busy, act, machine, book }: Props) {
  const known = craftableAt(book, machine, look.knows);
  const [what, setWhat] = useState<string | null>(null);
  const [qty, setQty] = useState(1);
  const [forecast, setForecast] = useState<Plan | null>(null);
  //: "Put on automatic" is a choice of mode: volume and an energy bill against
  //: quality and attention (D-035, D-058).
  const [automaton, setAutomaton] = useState(false);

  if (known.length === 0) return null;
  const selected = what && known.includes(what) ? what : known[0];
  const myMachine = (look.bench ?? []).filter((b) => b.goods === machine);
  const automated = machine === "Автоматический станок";

  const doForecast = () =>
    act(async () => {
      const answer = await session.send("craft.plan", {
        output: selected,
        units: qty,
        auto: automaton && automated,
      });
      setForecast(answer.plan as Plan);
    });

  const launch = () =>
    act(async () => {
      await session.send("craft.start", {
        output: selected,
        units: qty,
        auto: automaton && automated,
      });
      setForecast(null);
    });

  //: Things are repaired and taken apart where they are made: at the machine the thing was made at.
  const repair = look.inventory.filter(
    (thing) => thing.condition < 100 && stationOf(book, thing.goods) === machine,
  );

  return (
    <section>
      <h2>{machine ?? "Руками"}</h2>

      {look.node?.cut_off && machine !== null && (
        <p className="trouble">
          Узел отключён за неуплату: станки не работают, пока долг не закрыт.
          Счёт — в сайдбаре, во вкладке «хозяйство».
        </p>
      )}

      {myMachine.map((station) => (
        <p className="note" key={station.id}>
          {station.quality === null ? "" : `качество ${station.quality.toFixed(0)}`}
          {station.condition < 100 && ` · состояние ${station.condition.toFixed(0)}`}
          {" · "}
          {station.busy ? (station.mine ? "занят вами" : "занят другим") : "свободен"}
          {(look.node?.mine || look.city?.powers.includes("laws")) && (
            <>
              {" "}
              <button
                className="quiet"
                onClick={() => act(() => session.send("station.take", { item: station.id }))}
                disabled={busy || station.busy}
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
          value={selected}
          onChange={(e) => {
            setWhat(e.target.value);
            setForecast(null);
          }}
        >
          {known.map((name) => (
            <option key={name}>{name}</option>
          ))}
        </select>
        <input
          type="number"
          min={1}
          value={qty}
          onChange={(e) => {
            setQty(Number(e.target.value));
            setForecast(null);
          }}
        />
        <button onClick={doForecast} disabled={busy}>
          Прогноз
        </button>
        {automated && (
          <label className="note">
            <input
              type="checkbox"
              checked={automaton}
              onChange={(e) => {
                setAutomaton(e.target.checked);
                setForecast(null);
              }}
            />{" "}
            на автомате
          </label>
        )}
      </div>

      {forecast && (
        <div className="plan">
          <p>
            качество <b>{forecast.quality.toFixed(1)}</b> ± {forecast.spread.toFixed(1)}
            {" · "}потолок {forecast.ceiling.toFixed(0)}
            {" · "}{forecast.minutes.toFixed(1)} мин
            {" · "}потери {forecast.waste.toFixed(1)}%
          </p>
          <p className="note">
            уйдёт:{" "}
            {Object.entries(forecast.consumes)
              .map(([name, qty]) => `${name} ${qty.toFixed(2)}`)
              .join(", ")}
            {forecast.auto && forecast.energy > 0 && (
              <>
                {" "}· энергии {forecast.energy.toFixed(0)} на{" "}
                {api.tk(forecast.energy_cost)} ₭ по тарифу города
              </>
            )}
          </p>
          <button onClick={launch} disabled={busy}>
            Запустить партию
          </button>
        </div>
      )}

      {repair.length > 0 && (
        <>
          <h3>Починить или разобрать</h3>
          {repair.map((thing) => (
            <div className="row" key={thing.id}>
              <span>
                {thing.goods} · состояние {thing.condition.toFixed(0)}
              </span>
              <button
                onClick={() => act(() => session.send("craft.repair", { item: thing.id }))}
                disabled={busy}
              >
                Починить
              </button>
              <button
                className="quiet"
                onClick={() => act(() => session.send("craft.recycle", { item: thing.id }))}
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
