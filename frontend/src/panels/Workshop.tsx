// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

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
 *
 * Below the recipe list is the door for those who have no recipe (D-064,
 * D-209): lay out a composition from the hands -- so much of each per unit --
 * and try. Right, and the recipe is theirs with the first batch under way;
 * wrong, and what was laid out is gone. No hints of closeness, by design.
 */

import { useEffect, useState } from "react";
import * as api from "../api";
import type { Invention, Look, Plan, Session, Thing } from "../api";
import { anyOfClass, membersOf } from "../classes";
import { tally } from "../amounts";
import { busyWith, CRAFT } from "../busy";
import { craftableAt, inputsOf, stationOf } from "../recipes";
import { Rule } from "../Rule";
import { Refusal, useActions } from "../actions";
import { TierPick } from "../Tier";
import { tiersOf } from "../tiers";

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

/**
 * The knowledge carrier: made by hand, and it needs to be told what to carry
 * (D-209). A thing class (D-215) -- `craft.CARRIER` in the engine -- and the
 * automatic bench likewise (`craft.AUTO_BENCH`).
 */
const CARRIER = "Носитель";
const AUTO_BENCH = "Автомат";

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
  //: For a carrier: which of the known recipes goes onto it (D-209).
  const [onto, setOnto] = useState<string | null>(null);
  //: Which quality tier feeds each input -- the master's choice (D-058);
  //: nothing said means the engine's own order, worst first.
  const [tiers, setTiers] = useState<Record<string, string | null>>({});

  //: Computed before the early return below: a hook may not be called
  //: conditionally, and the forecast effect needs both of these.
  const selected = what && known.includes(what) ? what : (known[0] ?? null);
  const automated = machine !== null && anyOfClass(book, [machine], AUTO_BENCH);
  //: What may be written: everything known except the carrier itself -- a
  //: recipe for writing recipes on a carrier is a loop nobody needs.
  const carriers = new Set(membersOf(book, CARRIER));
  const writable = look.knows.filter((name) => !carriers.has(name));
  const writing = selected !== null && carriers.has(selected);
  const recipe = writing ? (onto && writable.includes(onto) ? onto : (writable[0] ?? null)) : null;
  const inputs = selected ? inputsOf(book, selected) : [];
  //: Only the tiers said for this recipe's inputs travel: a choice made for
  //: another recipe's ore must not silently narrow this one.
  const chosenTiers = Object.fromEntries(
    inputs.filter((name) => tiers[name]).map((name) => [name, tiers[name]]),
  );
  const tiersKey = JSON.stringify(chosenTiers);

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
          recipe: recipe ?? undefined,
          tiers: chosenTiers,
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
    //: Presence is part of the forecast: a refusal earned on the road must
    //: give way to a number once the body is back at the bench.
    //: `tiersKey` stands for `chosenTiers`: a fresh object every render would
    //: refire the effect endlessly, its string does not.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, selected, qty, automaton, automated, recipe, tiersKey, look.node?.key, look.travel, look.survey]);

  const myMachine = (look.bench ?? []).filter((b) => b.goods === machine);

  const launch = () =>
    act(async () => {
      await session.send("craft.start", {
        output: selected,
        units: qty,
        auto: automaton && automated,
        recipe: recipe ?? undefined,
        tiers: chosenTiers,
      });
      setForecast(null);
    });

  //: Things are repaired and taken apart where they are made: at the machine the thing was made at.
  const repair = look.inventory.filter(
    (thing) => thing.condition < 100 && stationOf(book, thing.goods) === machine,
  );

  //: The queue is one per body (D-209): if something of theirs already runs,
  //: a new batch will wait its turn -- said before the button, not after.
  const running = (look.batches ?? []).find((b) => b.state === "running");

  //: Another occupation -- a search on the land, a plot under the plough, a
  //: face -- has the hands, and work is not begun with them (D-211). A batch
  //: of one's own is not in the way: it is a queue, and that is said above.
  const occupied = busyWith(look, [CRAFT]);

  return (
    <section>
      <Refusal of={acting} />
      <h2>
        {machine ?? "Руками"}
        <Rule>
          Партия идёт, только пока вы стоите здесь: ушли — замерла, вернулись —
          продолжилась. У одного человека идёт одна работа, остальные ждут очереди в
          «делах». За рабочей станцией работает один.
        </Rule>
      </h2>

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
          {(api.isMine(look) || look.city?.powers.includes("laws")) && (
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

      {selected !== null && (
        <>
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

          {/* What goes in and which quality of it (D-058): a row per input,
              always -- the choice is part of the batch, and it is seen even
              when the hands hold one tier or none. */}
          {inputs.length > 0 && (
            <div className="inputs">
              {inputs.map((name) => {
                const have = tiersOf(look.inventory, name).reduce((s, t) => s + t.amount, 0);
                return (
                  <div className="row" key={name}>
                    <span className="note">
                      {name}
                      {" "}· в руках {tally(name, have)}
                    </span>
                    <TierPick
                      things={look.inventory}
                      goods={name}
                      value={tiers[name]}
                      onChange={(tier) => setTiers((was) => ({ ...was, [name]: tier }))}
                    />
                  </div>
                );
              })}
            </div>
          )}

          {writing && (
            <div className="row">
              <span className="note">записать рецепт:</span>
              {writable.length === 0 ? (
                <span className="note">вы пока ничего не знаете, кроме самого носителя</span>
              ) : (
                <select value={recipe ?? ""} onChange={(e) => setOnto(e.target.value)}>
                  {writable.map((name) => (
                    <option key={name}>{name}</option>
                  ))}
                </select>
              )}
            </div>
          )}

          <div className="plan">
            {forecast ? (
              <>
                <p>
                  качество <b className="num">{forecast.quality.toFixed(1)}</b> ±{" "}
                  <span className="num">{forecast.spread.toFixed(1)}</span>
                  {" · "}потолок <span className="num">{forecast.ceiling.toFixed(0)}</span>
                  {" · "}
                  {forecast.minutes < 1 ? (
                    <>
                      <span className="num">{(forecast.minutes * 60).toFixed(1)}</span> с
                    </>
                  ) : (
                    <>
                      <span className="num">{forecast.minutes.toFixed(1)}</span> мин
                    </>
                  )}
                  {" · "}потери <span className="num">{forecast.waste.toFixed(1)}</span>%
                </p>
                <p className="note">
                  уйдёт:{" "}
                  {Object.entries(forecast.consumes)
                    .map(([name, qty]) => `${name} ${tally(name, qty)}`)
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
            <button
              onClick={launch}
              disabled={busy || !forecast || occupied !== null}
              title={occupied ?? ""}
            >
              {running ? "В очередь" : "Запустить партию"}
            </button>
            {running && (
              <span className="note">
                {" "}сейчас идёт «{running.output}»: новая партия встанет за ней
              </span>
            )}
          </div>
        </>
      )}

      <Invent look={look} session={session} machine={machine} book={book} />

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
                disabled={busy || occupied !== null}
                title={occupied ?? ""}
              >
                Починить
              </button>
              <button
                className="quiet"
                onClick={() => act(() => session.send("craft.recycle", { item: thing.id }))}
                disabled={busy || occupied !== null}
                title={occupied ?? ""}
              >
                Разобрать
              </button>
            </div>
          ))}
        </>
      )}
    </section>
  );
}

/** One line of a laid-out composition: a thing from the hands, how much per unit,
 *  and which quality of it (D-058). */
type Laid = { goods: string; amount: number; tier: string | null };

/**
 * Making without a recipe (D-064, D-209).
 *
 * The composition is laid out per unit of output -- "3 wood" is a handle,
 * whatever the batch size -- and the batch size is entered apart. What is
 * laid out must be in the hands in full for the whole batch: the engine
 * refuses up front otherwise, so a guess never costs less than it says.
 *
 * The answer is shown here, once, and it is deliberately short: opened or
 * burned. There is nothing "warmer" or "colder" to show, and the panel does
 * not pretend otherwise.
 */
function Invent({
  look,
  session,
  machine,
  book,
}: {
  look: Look;
  session: Session;
  machine: string | null;
  book: any;
}) {
  const acting = useActions();
  const { busy, act } = acting;
  const [laid, setLaid] = useState<Laid[]>([]);
  const [units, setUnits] = useState(1);
  const [answer, setAnswer] = useState<Invention | null>(null);

  //: Kinds of things in the hands, one line each: the same wood twice is one
  //: input with a bigger amount, not two.
  const kinds = [...new Set((look.inventory ?? []).map((t: Thing) => t.goods))].sort();
  const cap: number = Number(book?.constants?.["invent.max_ingredients"] ?? 5);
  const free = kinds.filter((name) => !laid.some((row) => row.goods === name));

  const add = () => {
    if (free.length === 0 || laid.length >= cap) return;
    setLaid([...laid, { goods: free[0], amount: 1, tier: null }]);
  };
  const change = (i: number, patch: Partial<Laid>) =>
    setLaid(laid.map((row, k) => (k === i ? { ...row, ...patch } : row)));
  const drop = (i: number) => setLaid(laid.filter((_, k) => k !== i));

  const attempt = () =>
    act(async () => {
      const composition = Object.fromEntries(
        laid.filter((row) => row.amount > 0).map((row) => [row.goods, row.amount]),
      );
      const tiers = Object.fromEntries(
        laid.filter((row) => row.amount > 0 && row.tier).map((row) => [row.goods, row.tier]),
      );
      const result = (await session.send("craft.invent", {
        composition,
        units,
        station: machine,
        tiers,
      })) as unknown as Invention;
      setAnswer(result);
      //: Burned or made into a batch -- either way the hands changed, and the
      //: laid-out lines are of the past.
      if (result.success || Object.keys(result.burned).length > 0) setLaid([]);
    });

  return (
    <>
      <h3>
        Без рецепта
        <Rule>
          Выложите состав на единицу изделия — до {cap} видов вещей из рук — и сколько
          единиц делаете. Совпало с тем, что здесь делают, — рецепт ваш и партия пошла.
          Не совпало — сгорает случайная часть выложенного: цена попытки. Подсказок
          «теплее — холоднее» нет.
        </Rule>
      </h3>
      <Refusal of={acting} />
      {kinds.length === 0 && (
        <p className="note">В руках пусто: выкладывать нечего.</p>
      )}
      {laid.map((row, i) => (
        <div className="row" key={i}>
          <select
            value={row.goods}
            onChange={(e) => change(i, { goods: e.target.value, tier: null })}
          >
            {[row.goods, ...free].map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
          <input
            type="number"
            min={0}
            step="any"
            value={row.amount}
            onChange={(e) => change(i, { amount: Number(e.target.value) })}
            title="сколько на единицу изделия"
          />
          <TierPick
            things={look.inventory}
            goods={row.goods}
            value={row.tier}
            onChange={(tier) => change(i, { tier })}
          />
          <button className="quiet" onClick={() => drop(i)} disabled={busy} title="убрать">
            ×
          </button>
        </div>
      ))}
      <div className="row">
        <button
          className="quiet"
          onClick={add}
          disabled={busy || free.length === 0 || laid.length >= cap}
        >
          + вещь
        </button>
        <label className="note">
          единиц{" "}
          <input
            type="number"
            min={1}
            value={units}
            onChange={(e) => setUnits(Number(e.target.value))}
          />
        </label>
        <button
          onClick={attempt}
          disabled={busy || laid.length === 0 || laid.every((row) => row.amount <= 0)}
        >
          Попробовать
        </button>
      </div>
      {answer && (
        <p className={answer.success ? "note" : "reason"}>
          {answer.success ? (
            <>
              Сложилось: «{answer.learned.join("», «")}» теперь в ваших знаниях
              {answer.batch ? " — и первая партия пошла." : "."}
              {answer.note && ` ${answer.note}`}
            </>
          ) : (
            <>
              {answer.note}
              {Object.keys(answer.burned).length > 0 && (
                <>
                  {" "}Сгорело:{" "}
                  {Object.entries(answer.burned)
                    .map(([name, qty]) => `${name} ${qty}`)
                    .join(", ")}
                  .
                </>
              )}
            </>
          )}
        </p>
      )}
    </>
  );
}
