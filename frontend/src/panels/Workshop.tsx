/**
 * A workstation: what is made at it, who stands at it, what is repaired
 * (D-092, D-133, D-150). In the player's language it is «рабочая станция» --
 * it used to be «станок» until D-200.
 *
 * The panel is named after the station, not "workshop", and is shown **only
 * where this station stands**. The reason is not styling: the station sets
 * what a place is (D-106), and three of them in the yard are three different
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

import { useEffect, useState } from "react";
import * as api from "../api";
import type { Look, Plan, Session } from "../api";
import { craftableAt, stationOf } from "../recipes";
import { Rule } from "../Rule";
import { Refusal, useActions } from "../actions";

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

export function Workshop({ look, session, machine, book }: Omit<Props, "busy" | "act">) {
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const known = craftableAt(book, machine, look.knows);
  const [what, setWhat] = useState<string | null>(null);
  const [qty, setQty] = useState(1);
  const [forecast, setForecast] = useState<Plan | null>(null);
  //: Why there is no forecast. A batch that cannot be counted is nearly always
  //: a batch that cannot be started -- missing materials, a busy machine, a
  //: node cut off for debt -- and the player is owed the reason where they
  //: stand, not a grey button.
  const [refusal, setRefusal] = useState<string | null>(null);
  //: "Put on automatic" is a choice of mode: volume and an energy bill against
  //: quality and attention (D-035, D-058).
  const [automaton, setAutomaton] = useState(false);

  //: Computed before the early return below: a hook may not be called
  //: conditionally, and the forecast effect needs both of these.
  const selected = what && known.includes(what) ? what : (known[0] ?? null);
  const automated = machine === "Автоматическая станция";

  /**
   * The forecast counts itself while the player is still choosing.
   *
   * It used to sit behind a "Прогноз" button, and that click stood between the
   * intention and the number: comparing two recipes cost three presses each,
   * so nobody compared and nobody derived a proportion -- the very thing the
   * exact forecast exists for (D-092). Now the number simply follows the choice.
   *
   * The pause debounces the arrow keys on the quantity field; a stale answer
   * from a superseded request is dropped rather than shown.
   */
  useEffect(() => {
    if (selected === null) return;
    let dropped = false;
    const timer = setTimeout(() => {
      void session
        .send("craft.plan", {
          output: selected,
          units: qty,
          auto: automaton && automated,
        })
        .then((answer) => {
          if (dropped) return;
          setForecast(answer.plan as Plan);
          setRefusal(null);
        })
        //: A refusal is not shouted at the bottom of the screen: it belongs
        //: next to the machine it came from, and the engine's wording already
        //: explains what is missing.
        .catch((trouble: unknown) => {
          if (dropped) return;
          setForecast(null);
          setRefusal(trouble instanceof Error ? trouble.message : String(trouble));
        });
    }, 300);
    return () => {
      dropped = true;
      clearTimeout(timer);
    };
  }, [session, selected, qty, automaton, automated]);

  //: Nothing is made at this machine with what the player knows -- the panel
  //: stays silent rather than showing an empty list of recipes.
  if (selected === null) return null;
  const myMachine = (look.bench ?? []).filter((b) => b.goods === machine);

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
      <Refusal of={acting} />
      <h2>{machine ?? "Руками"}</h2>

      {look.node?.cut_off && machine !== null && (
        <p className="trouble">
          Узел отключён за неуплату: рабочие станции не работают, пока долг не закрыт.
          Счёт — в сайдбаре, во вкладке «хозяйство».
        </p>
      )}

      {myMachine.map((station) => (
        <p className="note" key={station.id}>
          {station.quality === null ? "" : `качество ${station.quality.toFixed(0)}`}
          {station.condition < 100 && ` · состояние ${station.condition.toFixed(0)}`}
          {" · "}
          {station.busy ? (station.mine ? "занята вами" : "занята другим") : "свободна"}
          {(look.node?.mine || look.city?.powers.includes("laws")) && (
            <>
              {" "}
              <button
                className="quiet"
                onClick={() => act(() => session.send("station.take", { item: station.id }))}
                disabled={busy || station.busy}
                title="забрать станцию в руки"
              >
                Забрать
              </button>
            </>
          )}
        </p>
      ))}

      <div className="row">
        <select value={selected} onChange={(e) => setWhat(e.target.value)}>
          {known.map((name) => (
            <option key={name}>{name}</option>
          ))}
        </select>
        <input
          type="number"
          min={1}
          value={qty}
          onChange={(e) => setQty(Number(e.target.value))}
        />
        {automated && (
          <label className="note">
            <input
              type="checkbox"
              checked={automaton}
              onChange={(e) => setAutomaton(e.target.checked)}
            />{" "}
            на автомате
          </label>
        )}
      </div>

      <div className="plan">
        {forecast ? (
          <>
            <p>
              качество <b className="num">{forecast.quality.toFixed(1)}</b> ±{" "}
              <span className="num">{forecast.spread.toFixed(1)}</span>
              {" · "}потолок <span className="num">{forecast.ceiling.toFixed(0)}</span>
              {" · "}<span className="num">{forecast.minutes.toFixed(1)}</span> мин
              {" · "}потери <span className="num">{forecast.waste.toFixed(1)}</span>%
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
          </>
        ) : refusal ? (
          <p className="reason">{refusal}</p>
        ) : (
          <p className="note">Прогноз считается сам, пока вы выбираете.</p>
        )}
        <button onClick={launch} disabled={busy || !forecast}>
          Запустить партию
        </button>
      </div>

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

      <Rule>        Партия идёт офлайн и видна в сайдбаре, в «делах». За рабочей станцией
        работает один: пока идёт партия, второму она не отдаётся.
      </Rule>
    </section>
  );
}
