// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The field automaton's window (D-339): the programme as lines of commands,
 * the plots given to the machine, and the three storages it takes from and
 * reaps into.
 *
 * The machine is set whole with one command (`agro.program`); the window
 * keeps a draft until then. What the machine does with it comes back from
 * the world (D-226): the line it stands on and the word it stands with. The
 * beds themselves are the garden's window -- the survey is read here only to
 * offer the plots.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import * as api from "../api";
import type { Look, RecipeBook } from "../api";
import type { Bench, Storage } from "../wire/thing";
import {
  COMMANDS,
  FEED_STAGES,
  type Command,
  type FieldMachine,
  type Fields,
  type Line,
} from "../wire/agro";
import { classOf, membersOf } from "../classes";
import { Refusal, useActions, useBook, useEdition, useNames, useSession } from "../actions";
import { Rule } from "../Rule";
import { t } from "../locale";
import { goodsName, plantName, type Names } from "../names";

/** The field automaton thing class (D-339): the window, like the engine, binds to it. */
const FIELD_AUTOMATON = "field_automaton";

const DO: Record<Command, string> = {
  plow: "ui-agro-do-plow",
  sow: "ui-agro-do-sow",
  moisture: "ui-agro-do-moisture",
  feed: "ui-agro-do-feed",
  weed: "ui-agro-do-weed",
  thin: "ui-agro-do-thin",
  harvest: "ui-agro-do-harvest",
  fallow: "ui-agro-do-fallow",
};

const TROUBLE: Record<string, string> = {
  no_plots: "ui-agro-trouble-no-plots",
  no_power: "ui-agro-trouble-no-power",
  no_lube: "ui-agro-trouble-no-lube",
  no_water: "ui-agro-trouble-no-water",
  no_seeds: "ui-agro-trouble-no-seeds",
  no_fertilizer: "ui-agro-trouble-no-fertilizer",
  store_full: "ui-agro-trouble-store-full",
  no_store: "ui-agro-trouble-no-store",
  not_plowed: "ui-agro-trouble-not-plowed",
  unfit: "ui-agro-trouble-unfit",
  not_entitled: "ui-agro-trouble-not-entitled",
  fault: "ui-agro-trouble-fault",
};

const STAGE: Record<string, string> = {
  sprout: "ui-farm-stage-sprout",
  leaf: "ui-farm-stage-leaf",
  bloom: "ui-farm-stage-bloom",
  fill: "ui-farm-stage-fill",
};

/** A plot of the survey, as much of it as the window offers. */
type PlotRow = { id: string; name: string; node_key: string; area: number };

type Plant = { id: string };

type Props = {
  look: Look;
  values: Record<string, any> | null;
};

/** What the owner set, as one string: the key a new draft starts from. */
function settingsOf(row: FieldMachine | null): string {
  if (row === null) return "";
  const { program, plots, seeds, fertilizer, harvest } = row;
  return JSON.stringify({ program, plots, seeds, fertilizer, harvest });
}

/** A storage holding liquids is a vessel (D-230): no seed or grain goes in. */
function dry(book: RecipeBook | null, store: Storage): boolean {
  const recipe = book?.recipes?.find((one) => (one.id ?? one.name) === store.goods);
  return recipe?.holds !== "liquid";
}

/**
 * A fresh line of a command. The parameters start where they teach nothing:
 * the moisture of a fresh sowing, the first stage, one day -- the norms are
 * the agrotech text's to tell (D-296), not the window's.
 */
function lineOf(command: Command, plants: Plant[], fertilizers: string[], sown: number): Line {
  switch (command) {
    case "sow":
      return { do: command, culture: plants[0]?.id };
    case "moisture":
      return { do: command, target: sown };
    case "feed":
      return { do: command, goods: fertilizers[0], stage: FEED_STAGES[0] };
    case "weed":
    case "fallow":
      return { do: command, days: 1 };
    default:
      return { do: command };
  }
}

export function FieldAutomat({ look, values }: Props) {
  const session = useSession();
  const book = useBook();
  const names = useNames();
  const acting = useActions();
  const { busy, act } = acting;

  const [fields, setFields] = useState<FieldMachine[]>([]);
  const [plots, setPlots] = useState<PlotRow[]>([]);
  const [plants, setPlants] = useState<Plant[]>([]);
  const here = look.node?.key ?? null;

  const reload = useCallback(async () => {
    //: The wire's shape, as every panel reads `session.send` (`Factory`: `as Floor`).
    const [view, survey] = (await Promise.all([
      session.send("agro.view"),
      session.send("farm.survey"),
    ])) as [Fields, { plots?: PlotRow[] }];
    setFields(view.machines ?? []);
    setPlots((survey.plots ?? []).filter((row) => row.node_key === here));
  }, [session, here]);
  //: Reread when the world says so (D-226): a programme set, a bed the
  //: machine worked, a store filled or emptied.
  const edition = useEdition("agro.", "farm.", "storage.", "station.");
  useEffect(() => {
    void reload();
  }, [reload, edition]);
  useEffect(() => {
    void api.plants().then((answer) => setPlants(answer.plants));
  }, []);

  const machines = useMemo(
    () => (look.bench ?? []).filter((one) => classOf(book, one.goods) === FIELD_AUTOMATON),
    [look.bench, book],
  );
  if (machines.length === 0) return null;

  const go = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await reload();
    });
  const stores = (look.storages ?? []).filter((one) => one.mine && dry(book, one));
  const known = new Map(fields.map((row) => [row.item, row]));

  return (
    <section>
      <Refusal of={acting} />
      <h2>
        {t("ui-agro-title")}
        <Rule>{t("ui-agro-rule")}</Rule>
      </h2>
      {machines.map((bench) => {
        const row = known.get(bench.id) ?? null;
        return (
          <Machine
            //: A draft starts afresh only when the settings themselves changed:
            //: the cursor and the trouble move every few minutes, and a line
            //: half typed must not be thrown away by the machine's own work.
            key={`${bench.id}:${settingsOf(row)}`}
            bench={bench}
            row={row}
            plots={plots}
            stores={stores}
            plants={plants}
            book={book}
            names={names}
            values={values}
            busy={busy}
            go={go}
            send={(kind, message) => session.send(kind, message)}
          />
        );
      })}
    </section>
  );
}

type MachineProps = {
  bench: Bench;
  row: FieldMachine | null;
  plots: PlotRow[];
  stores: Storage[];
  plants: Plant[];
  book: RecipeBook | null;
  names: Names | null;
  values: Record<string, any> | null;
  busy: boolean;
  go: (what: () => Promise<unknown>) => Promise<void>;
  send: (kind: string, message?: Record<string, unknown>) => Promise<any>;
};

function Machine(props: MachineProps) {
  const { bench, row, plots, stores, plants, book, names, values, busy, go, send } = props;
  const fertilizers = membersOf(book, "fertilizer");
  const [lines, setLines] = useState<Line[]>(row?.program ?? []);
  const [given, setGiven] = useState<string[]>(row?.plots ?? []);
  const [seeds, setSeeds] = useState<string>(row?.seeds ?? "");
  const [fertilizer, setFertilizer] = useState<string>(row?.fertilizer ?? "");
  const [harvest, setHarvest] = useState<string>(row ? (row.harvest ?? "") : bench.id);

  const smallest = Number(values?.["agro.plot_min_area"] ?? 0);
  const most = Number(values?.["agro.plots_max"] ?? 0);
  const longest = Number(values?.["agro.program_steps"] ?? 0);
  //: The vault's numbers, read where the window reads its others: until the
  //: constants arrive the fields simply start empty rather than guess a copy.
  const sown = Number(values?.["farm.sown_moisture"] ?? 0);
  const daysMax = Number(values?.["agro.days_max"] ?? 0);
  const saved = row !== null && JSON.stringify(row.program) === JSON.stringify(lines);

  const change = (index: number, line: Line) =>
    setLines(lines.map((one, at) => (at === index ? line : one)));
  const move = (index: number, by: number) => {
    const next = [...lines];
    const [line] = next.splice(index, 1);
    next.splice(index + by, 0, line);
    setLines(next);
  };
  const storeLabel = (store: Storage) =>
    store.id === bench.id
      ? t("ui-agro-bunker", { mass: String(Math.round(store.mass)), capacity: String(store.capacity) })
      : t("ui-agro-store", {
          goods: goodsName(names, store.goods),
          mass: String(Math.round(store.mass)),
          capacity: String(store.capacity),
        });
  const storeSelect = (label: string, value: string, set: (id: string) => void) => (
    <label className="agro-row">
      <span>{label}</span>
      <select value={value} onChange={(e) => set(e.target.value)} disabled={busy}>
        <option value="">{t("ui-agro-store-none")}</option>
        {stores.map((store) => (
          <option key={store.id} value={store.id}>
            {storeLabel(store)}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <div className="state-card">
      <div className="card-head">
        <b>{goodsName(names, bench.goods)}</b>
        <span className="note">
          {row === null
            ? t("ui-agro-idle")
            : t("ui-agro-standing", {
                line: String(row.cursor + 1),
                command: t(DO[row.program[row.cursor]?.do ?? "plow"]),
              })}
        </span>
      </div>
      {row?.trouble && <p className="note">{t(TROUBLE[row.trouble] ?? "ui-agro-trouble-other")}</p>}

      <p className="sign">{t("ui-agro-program")}</p>
      <ol className="agro-lines">
        {lines.map((line, index) => (
          <li key={index} className={saved && row?.cursor === index ? "agro-line current" : "agro-line"}>
            <select
              value={line.do}
              disabled={busy}
              onChange={(e) =>
                change(index, lineOf(e.target.value as Command, plants, fertilizers, sown))
              }
            >
              {COMMANDS.map((command) => (
                <option key={command} value={command}>
                  {t(DO[command])}
                </option>
              ))}
            </select>
            {line.do === "sow" && (
              <select
                value={line.culture ?? ""}
                disabled={busy}
                onChange={(e) => change(index, { ...line, culture: e.target.value })}
              >
                {plants.map((plant) => (
                  <option key={plant.id} value={plant.id}>
                    {plantName(names, plant.id)}
                  </option>
                ))}
              </select>
            )}
            {line.do === "moisture" && (
              <input
                type="number"
                min={1}
                max={100}
                value={line.target ?? sown}
                disabled={busy}
                aria-label={t("ui-agro-target")}
                onChange={(e) => change(index, { ...line, target: Number(e.target.value) })}
              />
            )}
            {line.do === "feed" && (
              <>
                <select
                  value={line.goods ?? ""}
                  disabled={busy}
                  onChange={(e) => change(index, { ...line, goods: e.target.value })}
                >
                  {fertilizers.map((goods) => (
                    <option key={goods} value={goods}>
                      {goodsName(names, goods)}
                    </option>
                  ))}
                </select>
                <select
                  value={line.stage ?? FEED_STAGES[0]}
                  disabled={busy}
                  onChange={(e) => change(index, { ...line, stage: e.target.value })}
                >
                  {FEED_STAGES.map((stage) => (
                    <option key={stage} value={stage}>
                      {t(STAGE[stage])}
                    </option>
                  ))}
                </select>
              </>
            )}
            {(line.do === "weed" || line.do === "fallow") && (
              <label className="agro-row">
                {line.do === "weed" && <span>{t("ui-agro-every")}</span>}
                <input
                  type="number"
                  min={1}
                  max={daysMax > 0 ? daysMax : undefined}
                  value={line.days ?? 1}
                  disabled={busy}
                  onChange={(e) => change(index, { ...line, days: Number(e.target.value) })}
                />
                <span>{t("ui-agro-days")}</span>
              </label>
            )}
            <span className="agro-line-act">
              <button className="quiet" disabled={busy || index === 0} onClick={() => move(index, -1)} title={t("ui-agro-up")}>
                ↑
              </button>
              <button
                className="quiet"
                disabled={busy || index === lines.length - 1}
                onClick={() => move(index, 1)}
                title={t("ui-agro-down")}
              >
                ↓
              </button>
              <button
                className="quiet"
                disabled={busy}
                onClick={() => setLines(lines.filter((_, at) => at !== index))}
                title={t("ui-agro-remove")}
              >
                ×
              </button>
            </span>
          </li>
        ))}
      </ol>
      <button
        className="quiet"
        disabled={busy || (longest > 0 && lines.length >= longest)}
        onClick={() => setLines([...lines, lineOf("plow", plants, fertilizers, sown)])}
      >
        {t("ui-agro-add")}
      </button>

      <p className="sign">{t("ui-agro-plots")}</p>
      {plots.length === 0 && <p className="note">{t("ui-agro-no-plots")}</p>}
      {plots.map((plot) => {
        const on = given.includes(plot.id);
        const small = plot.area < smallest;
        const full = most > 0 && given.length >= most && !on;
        return (
          <label key={plot.id} className="agro-row">
            <input
              type="checkbox"
              checked={on}
              disabled={busy || small || full}
              onChange={() => setGiven(on ? given.filter((id) => id !== plot.id) : [...given, plot.id])}
            />
            <span>
              {plot.name} · {t("ui-farm-area", { area: String(plot.area) })}
              {small && <> · {t("ui-agro-plot-small", { min: String(smallest) })}</>}
            </span>
          </label>
        );
      })}

      <p className="sign">{t("ui-agro-stores")}</p>
      {storeSelect(t("ui-agro-store-seeds"), seeds, setSeeds)}
      {storeSelect(t("ui-agro-store-fertilizer"), fertilizer, setFertilizer)}
      {storeSelect(t("ui-agro-store-harvest"), harvest, setHarvest)}

      <div className="card-act">
        <button
          disabled={busy || lines.length === 0}
          onClick={() =>
            go(() =>
              send("agro.program", {
                machine: bench.id,
                program: lines,
                plots: given,
                seeds: seeds || null,
                fertilizer: fertilizer || null,
                harvest: harvest || null,
              }),
            )
          }
        >
          {t("ui-agro-save")}
        </button>
        {row !== null && (
          <button className="quiet" disabled={busy} onClick={() => go(() => send("agro.stop", { machine: bench.id }))}>
            {t("ui-agro-stop")}
          </button>
        )}
      </div>
    </div>
  );
}
