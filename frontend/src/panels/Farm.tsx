// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Plots -- the location scene (D-118, D-296).
 *
 * Everything here is in-person: land is surveyed, ploughed, sown, watered, fed
 * and harvested on foot. Somebody else's land shows the owner; nobody's land
 * outside a city is farmed by whoever comes -- it is never privatized, and the
 * field on it is open to all (D-198).
 *
 * A growing bed lives by three scales the server keeps -- moisture, health,
 * growth -- and shows two words and one curve: the stage, the word of health,
 * and the moisture drawn forward from the point the server gave, at the pace
 * it gave (D-226: no timer of the client's own). No norm is drawn on it: what
 * the culture wants is the Library's text, not the window's mark (D-057).
 */

import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import { tally } from "../amounts";
import { busyWith } from "../busy";
import type { Look, RecipeBook } from "../api";
import { varietyText, type VarietyRef } from "../api";
import { Refusal, useActions, useBook, useEdition, useNames, useSession } from "../actions";
import { membersOf } from "../classes";
import { t } from "../locale";
import { className, goodsName, plantName, type Names } from "../names";
import { Doing } from "../Deadline";
import { Gauge } from "../Gauge";
import { Glyph } from "../Glyph";
import { ownOrWild } from "./place/shared";

type Props = {
  look: Look;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

/** The stages of growth in order, common to every crop (D-296). */
const STAGES = ["sprout", "leaf", "bloom", "fill", "ripe"] as const;
type Stage = (typeof STAGES)[number];
type Health = "strong" | "weak" | "sick" | "dying";

type Row = {
  id: string;
  name: string;
  node_key: string;
  area: number;
  state: "idle" | "plowing" | "plowed" | "sown";
  fertility: number;
  culture: string | null;
  variety?: VarietyRef;
  /** The two words of a growing bed (D-296): where it is and how it stands. */
  stage?: Stage;
  health?: Health;
  /** The one curve: the moisture at `moisture_at`, leaving at `dry_per_day`
   *  per cent of itself a Terran day. The client draws it forward. */
  moisture?: number;
  moisture_at?: string;
  dry_per_day?: number;
  /** Present where water is carried by hand: no river here (D-126). */
  carried?: true;
  /** Present when this stage was already fed: a second feeding runs to leaf. */
  fed?: true;
  /** Present once the stand was thinned (D-297): a fact of the sowing, once. */
  thinned?: true;
  /** What a treatment still holds and until when (D-299): class id -> ISO
   *  moment. Not derivable -- the window did not see the bottle opened. */
  guard?: Record<string, string>;
  /** What everybody sees -- a sign, never a norm (D-057). */
  symptoms?: string[];
  /** The plough's progress (D-277): its share, and -- while a run is
   *  under way -- the run's start and end for the bar. Paused is the
   *  absence of a run: under the plough with no `plow_since`. */
  plow_share?: number;
  plow_since?: string;
  plow_ready_at?: string;
};

/** Under the plough, nobody at it (D-277). Derived, not sent (D-225). */
const pausedPlough = (row: Row) => row.state === "plowing" && !row.plow_since;

//: Symptoms are common to all crops, norms differ. So an experienced person
//: reads a bed at a glance even for an unfamiliar cultivar, while the exact
//: numbers they still have to know or derive (D-057).
const SYMPTOM: Record<string, string> = {
  thirst: "ui-farm-symptom-thirst",
  soaked: "ui-farm-symptom-soaked",
  pale: "ui-farm-symptom-pale",
  burn: "ui-farm-symptom-burn",
  fat: "ui-farm-symptom-fat",
  weedy: "ui-farm-symptom-weedy",
  crowded: "ui-farm-symptom-crowded",
  //: The four pests (D-299): the sign says what the eye sees and never
  //: which bottle answers it -- that is the agrotech text's to teach.
  spots: "ui-farm-symptom-spots",
  web: "ui-farm-symptom-web",
  bitten: "ui-farm-symptom-bitten",
  rot: "ui-farm-symptom-rot",
};

/** The classes a bed is treated with (D-299): the vault couples every pest
 *  to the class that puts it out, and this reads that table rather than
 *  keeping a second copy of it. A fifth class is a row there and a recipe. */
function protectants(book: RecipeBook | null): string[] {
  const cure = book?.constants?.["farm.pest_cure"];
  if (!cure || typeof cure !== "object") return [];
  return [...new Set(Object.values(cure as Record<string, unknown>).map(String))];
}

const STATE: Record<Row["state"], string> = {
  idle: "ui-farm-state-idle",
  plowing: "ui-farm-state-plowing",
  plowed: "ui-farm-state-plowed",
  sown: "ui-farm-state-sown",
};

const STAGE: Record<Stage, string> = {
  sprout: "ui-farm-stage-sprout",
  leaf: "ui-farm-stage-leaf",
  bloom: "ui-farm-stage-bloom",
  fill: "ui-farm-stage-fill",
  ripe: "ui-farm-stage-ripe",
};

const HEALTH: Record<Health, string> = {
  strong: "ui-farm-health-strong",
  weak: "ui-farm-health-weak",
  sick: "ui-farm-health-sick",
  dying: "ui-farm-health-dying",
};

/** How far ahead the moisture curve looks, in Terran days. A display span. */
const CURVE_DAYS = 4;
/** Points the curve is drawn with. Display resolution, nothing of the world's. */
const CURVE_POINTS = 48;
/** The step the target slider moves by, in points of moisture. */
const TARGET_STEP = 5;

/**
 * The moisture curve (D-296): the point the server gave, drawn forward at the
 * pace it gave. `moisture(t) = m0 * exp(-k * days)` -- the same exponential
 * the engine walks, so the picture and the bed agree. Drawn at render time
 * and redrawn when the world says so (D-226), never by a timer.
 */
function MoistureCurve({ row, dayHours }: { row: Row; dayHours: number }) {
  if (row.moisture == null || !row.moisture_at || row.dry_per_day == null) return null;
  const since = new Date(row.moisture_at).getTime();
  const elapsedDays = Math.max(0, (Date.now() - since) / 1000 / 3600 / dayHours);
  const rate = row.dry_per_day / 100;
  const at = (days: number) => row.moisture! * Math.exp(-rate * days);
  const width = 100;
  const height = 100;
  const x = (days: number) => (days / CURVE_DAYS) * width;
  const y = (value: number) => height - (Math.max(0, Math.min(100, value)) / 100) * height;
  const points: string[] = [];
  for (let i = 0; i <= CURVE_POINTS; i += 1) {
    const days = (i / CURVE_POINTS) * CURVE_DAYS;
    points.push(`${x(days).toFixed(2)},${y(at(days)).toFixed(2)}`);
  }
  const now = Math.min(CURVE_DAYS, elapsedDays);
  const reading = at(now);
  return (
    <div className="moist">
      <span className="moist-label">{t("ui-farm-moisture")}</span>
      <svg
        className="moist-curve"
        viewBox={`0 0 ${width} ${height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={t("ui-farm-moisture-reading", { value: reading.toFixed(0) })}
      >
        {Array.from({ length: CURVE_DAYS }, (_, day) => (
          <line
            key={day}
            className="moist-day"
            x1={x(day + 1)}
            x2={x(day + 1)}
            y1={0}
            y2={height}
            vectorEffect="non-scaling-stroke"
          />
        ))}
        <polyline className="moist-line" points={points.join(" ")} vectorEffect="non-scaling-stroke" />
        <line
          className="moist-now"
          x1={x(now)}
          x2={x(now)}
          y1={0}
          y2={height}
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <span className="moist-reading">{reading.toFixed(0)}</span>
    </div>
  );
}

/** One fact of the bed per chip; nothing to say -- no container either. */
function PlotChips({ row, names }: { row: Row; names: Names | null }) {
  const chips: React.ReactNode[] = [];
  //: All four words, the good one too (D-296): a bed that stands strong says
  //: so, and the absence of a warning is not a word.
  if (row.health) {
    const tone = { strong: "good", weak: "dim", sick: "warn", dying: "warn" }[row.health];
    chips.push(
      <span className={`chip ${tone}`} key="health">
        {t(HEALTH[row.health])}
      </span>,
    );
  }
  if (row.stage === "ripe") {
    chips.push(<span className="chip good" key="ripe">{t("ui-farm-ripe")}</span>);
  }
  for (const code of row.symptoms ?? []) {
    chips.push(
      <span className="chip dim" key={code}>
        {SYMPTOM[code] ? t(SYMPTOM[code]) : code}
      </span>,
    );
  }
  if (row.fed) chips.push(<span className="chip dim" key="fed">{t("ui-farm-fed-stage")}</span>);
  if (row.thinned) chips.push(<span className="chip dim" key="thinned">{t("ui-farm-thinned")}</span>);
  for (const klass of Object.keys(row.guard ?? {})) {
    //: One chip per class that still holds (D-299), named: two guards are two
    //: chips, and the farmer sees which trouble is already answered for.
    chips.push(
      <span className="chip" key={`guard-${klass}`}>
        {t("ui-farm-guarded", { guard: className(names, klass) })}
      </span>,
    );
  }
  if (row.carried) {
    chips.push(
      <span className="chip" key="carried">
        <Glyph name="water" />
        {t("ui-farm-carried")}
      </span>,
    );
  }
  if (chips.length === 0) return null;
  return <div className="chips">{chips}</div>;
}

/** The cultivar in brackets after the crop -- unless it *is* the crop: the
 *  base line would only echo the culture's own name twice. */
function cultivarNote(names: Names | null, row: Row): string {
  if (!row.variety || ("key" in row.variety && row.variety.key === row.culture)) return "";
  const text = varietyText(names, row.variety);
  return text ? ` (${text})` : "";
}

export function Farm({ look }: Omit<Props, "busy" | "act">) {
  const session = useSession();
  const names = useNames();
  //: The book: the fertilizer class (D-291) and the Terran day the curve counts in.
  const book = useBook();
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  //: Nobody's land outside a city is farmed by whoever comes (D-198): the
  //: ground has no owner and never will, but the crop on it is somebody's.
  const mine = ownOrWild(look);
  const owner = look.node?.owner ?? null;
  const [rows, setRows] = useState<Row[]>([]);
  const [plants, setPlants] = useState<
    { id: string; name: string; gives: string; seed: string }[]
  >([]);
  //: One sows with a batch of seeds, not a crop: the batch has its own cultivar and strength.
  const [batch, setBatch] = useState("");
  //: The target of the next watering per bed (D-296): the slider's own
  //: position, kept until the world redraws the card.
  const [targets, setTargets] = useState<Record<string, number>>({});
  //: The fertilizer picked for the next feeding per bed.
  const [feeds, setFeeds] = useState<Record<string, string>>({});
  //: The preparation picked for the next treatment per bed (D-299).
  const [cures, setCures] = useState<Record<string, string>>({});

  const current_ = look.node?.key;
  //: The curve counts Terran days (D-008): the farm's day is the same
  //: everywhere, whatever planet the bed stands on -- so the length comes
  //: from the catalog's constant, not from the planet's clock in `look`.
  const dayHours = Number(book?.constants?.["time.day_terra"] ?? look.clock?.day_hours ?? 24);

  //: Work on a plot is an occupation (D-211), and a busy body has no hands for
  //: it -- including its own plough on the neighbouring strip. The buttons go
  //: grey with the reason written beside them rather than collecting refusals.
  const occupied = busyWith(look);

  //: Seeds are recognised by name from vault data, not by the client's guess.
  const seedNames = new Set(plants.map((p) => p.seed));
  const seeds = look.inventory.filter((thing) => seedNames.has(thing.goods));
  //: The fertilizers are a class (D-291): a third one is a row in the vault,
  //: and this button knows it by the class, not by its name. One entry per kind in hand.
  const fertilizers = new Set(membersOf(book, "fertilizer"));
  const dung = [
    ...new Map(
      look.inventory
        .filter((thing) => fertilizers.has(thing.goods))
        .map((thing) => [thing.goods, thing]),
    ).values(),
  ];
  //: The preparations are four classes (D-299), asked of the catalog the
  //: same way: what is in hand of any of them can be put on a bed, and the
  //: window never says which sign it answers -- the text does.
  const bottlesOf = new Set(protectants(book).flatMap((klass) => membersOf(book, klass)));
  const bottles = [
    ...new Map(
      look.inventory
        .filter((thing) => bottlesOf.has(thing.goods))
        .map((thing) => [thing.goods, thing]),
    ).values(),
  ];

  const reload = useCallback(async () => {
    const answer = await session.send("farm.survey");
    setRows((answer.plots as Row[]).filter((row) => row.node_key === current_));
  }, [session, current_]);
  //: Reread when the world says so (D-226), not on every look.
  const edition = useEdition("farm.", "body.");

  //: The summary is reread together with the general poll: ploughing is
  //: finished by the worker, and its completion comes from the world, not a click.
  useEffect(() => {
    void reload();
  }, [reload, edition]);

  useEffect(() => {
    void api.plants().then((p) => setPlants(p.plants));
  }, []);

  const go = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await reload();
    });

  //: The slider starts a step above what the ground holds: a watering that
  //: changes nothing is refused, and the first press should not be one.
  const targetOf = (row: Row) =>
    targets[row.id] ??
    Math.min(100, Math.ceil(((row.moisture ?? 0) + TARGET_STEP) / TARGET_STEP) * TARGET_STEP);
  const feedOf = (row: Row) => feeds[row.id] || dung[0]?.goods || "";
  const cureOf = (row: Row) => cures[row.id] || bottles[0]?.goods || "";
  //: Thinning is open up to the vault's stage (D-297): the catalog's constant,
  //: read like the day length, so the button and the engine agree. No
  //: fallback: without the key there is no button, not a guessed one.
  const thinUntil = book?.constants?.["farm.thin_until"];
  const thinningOpen = (row: Row) =>
    !row.thinned &&
    !!row.stage &&
    typeof thinUntil === "string" &&
    STAGES.indexOf(row.stage) <= STAGES.indexOf(thinUntil as Stage);

  //: The holder runs the estate: civic land is bought first (06-farming).
  if (!mine) {
    return (
      <section>
        <Refusal of={acting} />
        <h2>{t("ui-farm-land")}</h2>
        {owner ? (
          <p className="note">{t("ui-farm-owned", { owner })}</p>
        ) : (
          <p className="note">{t("ui-farm-civic")}</p>
        )}
      </section>
    );
  }

  return (
    <section>
      {/* The window's refusals belong to the window with the buttons in it:
          every "not enough seed", "wetter than that already" and "hands are
          busy" is shown where the button was pressed. */}
      <Refusal of={acting} />
      <h2>{t("ui-farm-title")}</h2>

      {rows.length === 0 && <p className="note">{t("ui-farm-unmarked")}</p>}
      {/* The reason the buttons are grey is written once, above the cards, not
          hidden in a hint: a hint is invisible on a touch screen and easy to
          miss on any. */}
      {occupied && rows.length > 0 && <p className="note">{occupied}</p>}

      {/* A bed is a card of instruments (D-238): the fertility on a track,
          the moisture as a curve, one fact per chip -- and the two words of
          the growing bed in its head. */}
      {rows.map((row) => (
        <div className="state-card" key={row.id}>
          <div className="card-head">
            <b>{row.name}</b>
            <span className="note">
              {t("ui-farm-area", { area: String(row.area) })} · {t(STATE[row.state])}
              {row.state === "sown" && row.culture && (
                <> · {plantName(names, row.culture)}{cultivarNote(names, row)}</>
              )}
              {row.state === "sown" && row.stage && <> · {t(STAGE[row.stage])}</>}
              {/* The share stands in the head running or paused: the bar below
                  draws only the current run, and a strip taken up at ninety
                  per cent would otherwise look untouched. */}
              {row.state === "plowing" && (
                <>
                  {" · "}
                  {t(pausedPlough(row) ? "ui-farm-plow-paused" : "ui-farm-plow-share", {
                    share: String(Math.round((row.plow_share ?? 0) * 100)),
                  })}
                </>
              )}
            </span>
          </div>

          {/* A bare track: the culture's norm is the Library's text, not a
              notch of the window's (D-296). */}
          <Gauge label={t("ui-farm-fertility")} value={row.fertility} />

          {row.state === "sown" && <MoistureCurve row={row} dayHours={dayHours} />}

          {row.state === "plowing" && !pausedPlough(row) && row.plow_ready_at && (
            <Doing
              what={t("ui-farm-state-plowing")}
              until={row.plow_ready_at}
              since={row.plow_since}
            />
          )}

          {row.state === "sown" && <PlotChips row={row} names={names} />}

          <div className="card-act">
          {row.state === "idle" && (
            <button
              onClick={() => go(() => session.send("farm.plow", { plot: row.id }))}
              disabled={busy || occupied !== null}
            >
              {t("ui-farm-plow")}
            </button>
          )}
          {/* A running plough is paused from the strip as well as from the
              "activities" column -- the same command, this one naming the
              strip. Not greyed by `occupied`: the occupation is this very
              plough. Dropping the progress is a button of its own, and only
              on a paused strip (D-277): never a side effect of stopping. */}
          {row.state === "plowing" && !pausedPlough(row) && (
            <button
              className="quiet"
              onClick={() => go(() => session.send("farm.plow_pause", { plot: row.id }))}
              disabled={busy}
              title={t("ui-farm-plow-pause-why")}
            >
              {t("ui-farm-plow-pause")}
            </button>
          )}
          {pausedPlough(row) && (
            <>
              <button
                onClick={() => go(() => session.send("farm.plow", { plot: row.id }))}
                disabled={busy || occupied !== null}
              >
                {t("ui-farm-plow-resume")}
              </button>
              <button
                className="quiet"
                onClick={() => go(() => session.send("farm.plow_reset", { plot: row.id }))}
                disabled={busy}
                title={t("ui-farm-plow-reset-why")}
              >
                {t("ui-farm-plow-reset")}
              </button>
            </>
          )}
          {(row.state === "idle" || row.state === "plowed") &&
            dung.map((heap) => (
              <button
                key={heap.goods}
                onClick={() =>
                  go(() =>
                    session.send("farm.fertilize", { plot: row.id, goods: heap.goods }),
                  )
                }
                disabled={busy || occupied !== null}
              >
                {t("ui-farm-fertilize", { goods: goodsName(names, heap.goods) })}
              </button>
            ))}
          {row.state === "plowed" && (
            <>
              <select
                value={batch || seeds[0]?.id || ""}
                onChange={(e) => setBatch(e.target.value)}
              >
                {seeds.length === 0 && <option value="">{t("ui-farm-no-seeds")}</option>}
                {seeds.map((seed) => (
                  <option key={seed.id} value={seed.id}>
                    {goodsName(names, seed.goods)} · {tally(seed.goods, seed.amount)}
                    {seed.vigor != null
                      ? ` · ${t("ui-farm-vigor", { vigor: seed.vigor.toFixed(0) })}`
                      : ""}
                  </option>
                ))}
              </select>
              <button
                onClick={() =>
                  go(() =>
                    session.send("farm.sow", {
                      plot: row.id,
                      seeds: batch || seeds[0]?.id,
                    }),
                  )
                }
                disabled={busy || seeds.length === 0 || occupied !== null}
              >
                {t("ui-farm-sow")}
              </button>
            </>
          )}
          {row.state === "sown" && row.stage !== "ripe" && (
            <>
              {/* Watering to a target (D-296): the slider is the decision, the
                  water is the difference, and the target may run past what the
                  culture wants -- overwatering is the player's mistake, and
                  the bed will show it. */}
              <label className="slider">
                <input
                  type="range"
                  min={0}
                  max={100}
                  step={TARGET_STEP}
                  value={targetOf(row)}
                  onChange={(e) =>
                    setTargets({ ...targets, [row.id]: Number(e.target.value) })
                  }
                  aria-label={t("ui-farm-target")}
                />
              </label>
              <button
                onClick={() =>
                  go(async () => {
                    await session.send("farm.water", { plot: row.id, target: targetOf(row) });
                    //: The target was spent: the slider starts anew from the
                    //: watered ground, or a second press would only be refused.
                    setTargets((all) => {
                      const rest = { ...all };
                      delete rest[row.id];
                      return rest;
                    });
                  })
                }
                disabled={busy || occupied !== null}
              >
                {t("ui-farm-water-to", { target: String(targetOf(row)) })}
              </button>
              <button
                className="quiet"
                onClick={() => go(() => session.send("farm.weed", { plot: row.id }))}
                disabled={busy || occupied !== null}
              >
                {t("ui-farm-weed")}
              </button>
              {thinningOpen(row) && (
                <button
                  className="quiet"
                  onClick={() => go(() => session.send("farm.thin", { plot: row.id }))}
                  disabled={busy || occupied !== null}
                  title={t("ui-farm-thin-why")}
                >
                  {t("ui-farm-thin")}
                </button>
              )}
              {bottles.length > 0 && (
                <>
                  {bottles.length > 1 && (
                    <select
                      value={cureOf(row)}
                      onChange={(e) => setCures({ ...cures, [row.id]: e.target.value })}
                    >
                      {bottles.map((jar) => (
                        <option key={jar.goods} value={jar.goods}>
                          {goodsName(names, jar.goods)}
                        </option>
                      ))}
                    </select>
                  )}
                  <button
                    className="quiet"
                    onClick={() =>
                      go(() =>
                        session.send("farm.treat", { plot: row.id, goods: cureOf(row) }),
                      )
                    }
                    disabled={busy || occupied !== null}
                    title={t("ui-farm-treat-why")}
                  >
                    {t("ui-farm-treat", { goods: goodsName(names, cureOf(row)) })}
                  </button>
                </>
              )}
              {dung.length > 0 && (
                <>
                  {dung.length > 1 && (
                    <select
                      value={feedOf(row)}
                      onChange={(e) => setFeeds({ ...feeds, [row.id]: e.target.value })}
                    >
                      {dung.map((heap) => (
                        <option key={heap.goods} value={heap.goods}>
                          {goodsName(names, heap.goods)}
                        </option>
                      ))}
                    </select>
                  )}
                  <button
                    className="quiet"
                    onClick={() =>
                      go(() =>
                        session.send("farm.feed", { plot: row.id, goods: feedOf(row) }),
                      )
                    }
                    disabled={busy || occupied !== null}
                  >
                    {t("ui-farm-feed", { goods: goodsName(names, feedOf(row)) })}
                  </button>
                </>
              )}
            </>
          )}
          {row.state === "sown" && row.stage === "ripe" && (
            <>
              <button
                onClick={() =>
                  go(() =>
                    session.send("farm.harvest", { plot: row.id, select: true }),
                  )
                }
                disabled={busy || occupied !== null}
                title={t("ui-farm-harvest-select-hint")}
              >
                {t("ui-farm-harvest-select")}
              </button>
              <button
                className="quiet"
                onClick={() => go(() => session.send("farm.harvest", { plot: row.id }))}
                disabled={busy || occupied !== null}
                title={t("ui-farm-harvest-hint")}
              >
                {t("ui-farm-harvest")}
              </button>
            </>
          )}
          </div>
        </div>
      ))}

      <p className="note">{t("ui-farm-new-plot")}</p>

      <p className="note">{t("ui-farm-rule")}</p>
      <p className="note">{t("ui-farm-seeds-rule")}</p>
    </section>
  );
}
