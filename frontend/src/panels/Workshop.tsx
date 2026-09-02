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
import type { Invention, Look, Plan, Thing } from "../api";
import { isBuilt, isRelic, membersOf } from "../classes";
import { ownOrWild } from "./place/shared";
import { tally } from "../amounts";
import { busyWith, CRAFT } from "../busy";
import { craftableAt, inputsOf, stationOf } from "../recipes";
import { Gauge } from "../Gauge";
import { Rule } from "../Rule";
import { Refusal, useActions, useBook, useCompare, useNames, useSession } from "../actions";
import { goodsName } from "../names";
import { t } from "../locale";
import { TierPick } from "../Tier";
import { stockOf } from "../tiers";

type Props = {
  look: Look;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  /** The machine's name or `null` -- "By hand". */
  machine: string | null;
  /** The vault catalog: loaded once for the whole screen. */
};

/**
 * The knowledge carrier: made by hand, and it needs to be told what to carry
 * (D-209). A thing class (D-215) -- `craft.CARRIER` in the engine.
 */
const CARRIER = "carrier";

export function Workshop({ look, machine }: Omit<Props, "busy" | "act">) {
  const session = useSession();
  const book = useBook();
  const names = useNames();
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const known = craftableAt(book, machine, look.knows, names);
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
  //: For a carrier: which of the known recipes goes onto it (D-209).
  const [onto, setOnto] = useState<string | null>(null);
  //: Which quality tier feeds each input -- the master's choice (D-058);
  //: nothing said means the engine's own order, worst first.
  const [tiers, setTiers] = useState<Record<string, string | null>>({});

  //: Computed before the early return below: a hook may not be called
  //: conditionally, and the forecast effect needs both of these.
  const selected = what && known.includes(what) ? what : (known[0] ?? null);
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

  //: A new recipe starts at one unit: the count chosen for the last recipe
  //: is no choice about this one, and carried over it turned a full stock
  //: into a puzzling «short by 0.316» -- two canisters asked of plastic for
  //: one, with nothing on the screen saying which batch was being counted.
  useEffect(() => {
    setQty(1);
  }, [selected]);

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
  }, [session, selected, qty, recipe, tiersKey, look.node?.key, look.travel, look.survey]);

  const myMachine = (look.bench ?? []).filter((b) => b.goods === machine);

  const launch = () =>
    act(async () => {
      await session.send("craft.start", {
        output: selected,
        units: qty,
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
        {machine === null ? t("ui-workshop-by-hand") : goodsName(names, machine)}
        <Rule>{t("ui-workshop-rule")}</Rule>
      </h2>

      {look.node?.cut_off && machine !== null && (
        <p className="trouble">{t("ui-workshop-cut-off")}</p>
      )}

      {myMachine.map((station) => (
        <p className="note" key={station.id}>
          {station.quality == null
            ? ""
            : t("ui-workshop-station-quality", { quality: station.quality.toFixed(0) })}
          {station.condition < 100 &&
            ` · ${t("ui-workshop-station-condition", { condition: station.condition.toFixed(0) })}`}
          {" · "}
          {station.busy
            ? station.mine
              ? t("ui-workshop-station-busy-mine")
              : t("ui-workshop-station-busy-other")
            : t("ui-workshop-station-free")}
          {/* A relic of the Forerunners is never taken down (D-232): offering
              "take it" would be promising a refusal. */}
          {(ownOrWild(look) || look.city?.powers.includes("laws")) &&
            !isRelic(book, station.goods) &&
            !isBuilt(book, station.goods) && (
            <>
              {" "}
              <button
                className="quiet"
                onClick={() => act(() => session.send("station.take", { item: station.id }))}
                disabled={busy || station.busy}
                title={t("ui-workshop-station-take-hint")}
              >
                {t("ui-workshop-station-take")}
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
                <option key={name} value={name}>
                  {goodsName(names, name)}
                </option>
              ))}
            </select>
            <input
              type="number"
              min={1}
              value={qty}
              onChange={(e) => setQty(Number(e.target.value))}
            />
          </div>

          {/* What goes in and which quality of it (D-058): a row per input,
              always -- the choice is part of the batch, and it is seen even
              when the hands hold one tier or none. */}
          {inputs.length > 0 && (
            <div className="inputs">
              {inputs.map((name) => {
                const have = stockOf(look.inventory, name);
                return (
                  <div className="row" key={name}>
                    <span className="note">
                      {t("ui-workshop-input", {
                        goods: goodsName(names, name),
                        amount: tally(name, have),
                      })}
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
              <span className="note">{t("ui-workshop-write")}</span>
              {writable.length === 0 ? (
                <span className="note">{t("ui-workshop-write-nothing")}</span>
              ) : (
                <select value={recipe ?? ""} onChange={(e) => setOnto(e.target.value)}>
                  {writable.map((name) => (
                    <option key={name} value={name}>
                      {goodsName(names, name)}
                    </option>
                  ))}
                </select>
              )}
            </div>
          )}

          <div className="plan">
            {forecast ? (
              <>
                {/* The forecast as an instrument (D-238): the quality on a
                    track with its spread band and the machine's ceiling as a
                    notch; the figures keep standing beside it. */}
                <Gauge
                  label={t("ui-workshop-quality")}
                  value={forecast.quality}
                  spread={forecast.spread}
                  mark={forecast.ceiling}
                  markTitle={t("ui-workshop-ceiling-hint", {
                    ceiling: forecast.ceiling.toFixed(0),
                  })}
                  reading={`${forecast.quality.toFixed(1)} ± ${forecast.spread.toFixed(1)}`}
                />
                <p>
                  {forecast.minutes < 1 ? (
                    <>
                      <span className="num">{(forecast.minutes * 60).toFixed(1)}</span>{" "}
                      {t("ui-workshop-seconds")}
                    </>
                  ) : (
                    <>
                      <span className="num">{forecast.minutes.toFixed(1)}</span>{" "}
                      {t("ui-workshop-minutes")}
                    </>
                  )}
                  {" · "}
                  {t("ui-workshop-waste")} <span className="num">{forecast.waste.toFixed(1)}</span>%
                  {" · "}
                  {t("ui-workshop-ceiling")}{" "}
                  <span className="num">{forecast.ceiling.toFixed(0)}</span>
                </p>
                <p className="note">
                  {t("ui-workshop-consumes")}{" "}
                  {Object.entries(forecast.consumes)
                    .map(([name, qty]) => `${goodsName(names, name)} ${tally(name, qty)}`)
                    .join(", ")}
                </p>
              </>
            ) : refusal ? (
              <p className="reason">{refusal}</p>
            ) : (
              <p className="note">{t("ui-workshop-forecast")}</p>
            )}
            <button
              onClick={launch}
              disabled={busy || !forecast || occupied !== null}
              title={occupied ?? ""}
            >
              {running ? t("ui-workshop-queue") : t("ui-workshop-start")}
            </button>
            {running && (
              <span className="note">
                {" "}
                {t("ui-workshop-running", { goods: goodsName(names, running.output) })}
              </span>
            )}
          </div>
        </>
      )}

      <Invent look={look} machine={machine} />

      {repair.length > 0 && (
        <>
          <h3>{t("ui-workshop-repair-title")}</h3>
          {repair.map((thing) => (
            <div className="row" key={thing.id}>
              <span>
                {t("ui-workshop-thing-condition", {
                  goods: goodsName(names, thing.goods),
                  condition: thing.condition.toFixed(0),
                })}
              </span>
              <button
                onClick={() => act(() => session.send("craft.repair", { item: thing.id }))}
                disabled={busy || occupied !== null}
                title={occupied ?? ""}
              >
                {t("ui-workshop-repair")}
              </button>
              <button
                className="quiet"
                onClick={() => act(() => session.send("craft.recycle", { item: thing.id }))}
                disabled={busy || occupied !== null}
                title={occupied ?? ""}
              >
                {t("ui-workshop-recycle")}
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
  machine,
  }: {
  look: Look;
  machine: string | null;
}) {
  const session = useSession();
  const book = useBook();
  const names = useNames();
  const acting = useActions();
  const order = useCompare();
  const { busy, act } = acting;
  const [laid, setLaid] = useState<Laid[]>([]);
  const [units, setUnits] = useState(1);
  const [answer, setAnswer] = useState<Invention | null>(null);

  //: Kinds of things in the hands, one line each: the same wood twice is one
  //: input with a bigger amount, not two. Ordered by the display word of the
  //: player's language (D-251): the options show it, and an ASCII order of ids
  //: reads as random.
  const kinds = [...new Set((look.inventory ?? []).map((one: Thing) => one.goods))].sort((a, b) =>
    order(goodsName(names, a), goodsName(names, b)),
  );
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
        {t("ui-workshop-invent-title")}
        <Rule>{t("ui-workshop-invent-rule", { cap: String(cap) })}</Rule>
      </h3>
      <Refusal of={acting} />
      {kinds.length === 0 && <p className="note">{t("ui-workshop-invent-empty")}</p>}
      {laid.map((row, i) => (
        <div className="row" key={i}>
          <select
            value={row.goods}
            onChange={(e) => change(i, { goods: e.target.value, tier: null })}
          >
            {[row.goods, ...free].map((name) => (
              <option key={name} value={name}>
                {goodsName(names, name)}
              </option>
            ))}
          </select>
          <input
            type="number"
            min={0}
            step="any"
            value={row.amount}
            onChange={(e) => change(i, { amount: Number(e.target.value) })}
            title={t("ui-workshop-invent-per-unit")}
          />
          <TierPick
            things={look.inventory}
            goods={row.goods}
            value={row.tier}
            onChange={(tier) => change(i, { tier })}
          />
          <button
            className="quiet"
            onClick={() => drop(i)}
            disabled={busy}
            title={t("ui-workshop-invent-drop")}
          >
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
          {t("ui-workshop-invent-add")}
        </button>
        <label className="note">
          {t("ui-workshop-invent-units")}{" "}
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
          {t("ui-workshop-invent-try")}
        </button>
      </div>
      {answer && (
        <p className={answer.success ? "note" : "reason"}>
          {answer.success ? (
            <>
              {/* Two whole sentences rather than one with a tail: a fragment
                  glued on after the fact is the shape a language other than
                  this one cannot re-order. */}
              {t(
                answer.batch ? "ui-workshop-invent-done-batch" : "ui-workshop-invent-done",
                { learned: answer.learned.map((one) => goodsName(names, one)).join("», «") },
              )}
              {answer.note && ` ${answer.note}`}
            </>
          ) : (
            <>
              {answer.note}
              {Object.keys(answer.burned).length > 0 && (
                <>
                  {" "}
                  {t("ui-workshop-invent-burned", {
                    burned: Object.entries(answer.burned)
                      .map(([name, qty]) => `${goodsName(names, name)} ${qty}`)
                      .join(", "),
                  })}
                </>
              )}
            </>
          )}
        </p>
      )}
    </>
  );
}
