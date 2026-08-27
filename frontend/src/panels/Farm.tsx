// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Plots -- the location scene (D-118).
 *
 * Everything here is in-person: land is surveyed, ploughed, sown, tended and
 * harvested on foot. Somebody else's land shows the owner; nobody's land
 * outside a city is farmed by whoever comes -- it is never privatized, and the
 * field on it is open to all (D-198).
 */

import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import { tally } from "../amounts";
import { busyWith } from "../busy";
import type { Look } from "../api";
import { Refusal, useActions, useEdition, useSession } from "../actions";
import { Doing } from "../Deadline";
import { Gauge } from "../Gauge";
import { Glyph } from "../Glyph";
import { ownOrWild } from "./place/shared";

type Props = {
  look: Look;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

type Row = {
  id: string;
  name: string;
  node_key: string;
  area: number;
  state: "idle" | "plowing" | "plowed" | "sown";
  fertility: number;
  culture: string | null;
  culture_name?: string;
  variety?: string;
  ripe?: boolean;
  /** Whether the owner knows the cultivar's agrotech: everything below depends on it (D-057). */
  agrotech?: boolean;
  ripe_at?: string;
  asks_care?: boolean;
  missed_days?: number;
  fertility_required?: number;
  water_need?: number;
  /** Without agrotech only this is seen -- what to do about it, guess. */
  symptoms?: string[];
  /** The start of the growth term, named with agrotech (the bar's share). */
  sown_at?: string;
};

//: Symptoms are common to all crops, norms differ. So an experienced person
//: reads a bed at a glance even for an unfamiliar cultivar, while the exact
//: numbers they still have to know or derive (D-057).
const SYMPTOM: Record<string, string> = {
  thirst: "листья вялые",
  pale: "бледный лист",
  stunted: "угнетённый рост",
  ripe: "колос налился",
};

const STATE: Record<Row["state"], string> = {
  idle: "под паром",
  plowing: "пашется",
  plowed: "вспахана",
  sown: "растёт",
};

/** One fact of the bed per chip; nothing to say -- no container either. */
function PlotChips({ row }: { row: Row }) {
  const chips: React.ReactNode[] = [];
  if (row.agrotech && row.asks_care && row.water_need != null) {
    chips.push(
      <span className="chip warn" key="water">
        <Glyph name="water" />
        полить сегодня · <b>{row.water_need.toFixed(0)} л</b>
      </span>,
    );
  }
  if ((row.missed_days ?? 0) > 0) {
    chips.push(
      <span className="chip warn" key="missed">
        пропущено · <b>{row.missed_days} сут.</b>
      </span>,
    );
  }
  if (row.ripe) chips.push(<span className="chip good" key="ripe">созрело — пора убирать</span>);
  if (!row.agrotech) {
    //: The "ripe" symptom is the good chip's news said twice: the flag comes
    //: to everybody, the symptom only to those without agrotech.
    for (const code of (row.symptoms ?? []).filter((s) => s !== "ripe")) {
      chips.push(
        <span className="chip dim" key={code}>
          {SYMPTOM[code] ?? code}
        </span>,
      );
    }
  }
  if (chips.length === 0) return null;
  return <div className="chips">{chips}</div>;
}

export function Farm({ look }: Omit<Props, "busy" | "act">) {
  const session = useSession();
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

  const current_ = look.node?.key;

  //: Work on a plot is an occupation (D-211), and a busy body has no hands for
  //: it -- including its own plough on the neighbouring strip. The buttons go
  //: grey with the reason on them rather than collecting refusals.
  const occupied = busyWith(look);

  //: Seeds are recognised by name from vault data, not by the client's guess.
  const seedNames = new Set(plants.map((p) => p.seed));
  const seeds = look.inventory.filter((t) => seedNames.has(t.goods));

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

  //: The holder runs the estate: civic land is bought first (06-farming).
  if (!mine) {
    return (
      <section>
        <Refusal of={acting} />
        <h2>Земля</h2>
        {owner ? (
          <p className="note">
            Участок {owner}. Чужим хозяйством не управляют: наём — это доступ
            плюс доля через договор.
          </p>
        ) : (
          <p className="note">
            Городская земля: чтобы вести здесь хозяйство, участок надо выкупить
            в окне «Участок».
          </p>
        )}
      </section>
    );
  }

  return (
    <section>
      {/* The window's refusals belong to the window with the buttons in it.
          It used to stand only in the branch above -- the one that offers
          nothing and can be refused nothing -- so every "not enough seed",
          "already tended today" and "hands are busy" from the plough, the
          sowing, the round and the harvest was swallowed: the button clicked
          and the field simply did not change. */}
      <Refusal of={acting} />
      <h2>Делянки</h2>

      {rows.length === 0 && (
        <p className="note">Земля не размечена. Сто метров — это столько делянок,
          сколько вы нарежете.</p>
      )}

      {/* A bed is a card of instruments (D-238): the fertility on a track with
          the norm's notch, the term on the deadline bar, one fact per chip --
          instead of the comma sentence all of it used to be. */}
      {rows.map((row) => (
        <div className="state-card" key={row.id}>
          <div className="card-head">
            <b>{row.name}</b>
            <span className="note">
              {row.area} м² · {STATE[row.state]}
              {row.state === "sown" && row.culture_name && (
                <> · {row.culture_name}{row.variety ? ` (${row.variety})` : ""}</>
              )}
            </span>
          </div>

          {/* With agrotech the norm stands as a notch on the track; without
              it -- a bare track and symptoms as chips. Knowledge turns
              guesswork into a solved problem (D-057). */}
          <Gauge
            label="плодородие"
            value={row.fertility}
            mark={row.state === "sown" && row.agrotech ? row.fertility_required : undefined}
            markTitle={
              row.fertility_required != null
                ? `норма сорта: ${row.fertility_required.toFixed(0)}`
                : undefined
            }
            warn={
              row.state === "sown" &&
              row.agrotech === true &&
              row.fertility_required != null &&
              row.fertility < row.fertility_required
            }
            reading={
              row.state === "sown" && row.agrotech && row.fertility_required != null
                ? `${row.fertility.toFixed(0)} из ${row.fertility_required.toFixed(0)}`
                : row.fertility.toFixed(0)
            }
          />

          {row.state === "sown" && !row.ripe && row.ripe_at && (
            <Doing what="созреет" until={row.ripe_at} since={row.sown_at} />
          )}

          {row.state === "sown" && <PlotChips row={row} />}

          <div className="card-act">
          {row.state === "idle" && (
            <button
              onClick={() => go(() => session.send("farm.plow", { plot: row.id }))}
              disabled={busy || occupied !== null}
              title={occupied ?? ""}
            >
              Вспахать
            </button>
          )}
          {row.state === "plowed" && (
            <>
              <select
                value={batch || seeds[0]?.id || ""}
                onChange={(e) => setBatch(e.target.value)}
              >
                {seeds.length === 0 && <option value="">— семян нет —</option>}
                {seeds.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.goods} · {tally(t.goods, t.amount)}
                    {t.vigor != null ? ` · сила ${t.vigor.toFixed(0)}` : ""}
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
                title={occupied ?? ""}
              >
                Посеять
              </button>
            </>
          )}
          {row.state === "sown" && !row.ripe && (
            <button
              onClick={() => go(() => session.send("farm.care", { plot: row.id }))}
              //: Without agrotech "already tended today" is unknown to the
              //: player -- the button is live, and an extra round the engine rejects itself.

              disabled={busy || (row.agrotech === true && !row.asks_care) || occupied !== null}
              title={occupied ?? (row.agrotech && !row.asks_care ? "сегодня уже ухожено" : "")}
            >
              Обойти
            </button>
          )}
          {row.state === "sown" && row.ripe && (
            <>
              <button
                onClick={() =>
                  go(() =>
                    session.send("farm.harvest", { plot: row.id, select: true }),
                  )
                }
                disabled={busy || occupied !== null}
                title={occupied ?? "отобрать лучшие растения на семена: фонд держит силу"}
              >
                Убрать с отбором
              </button>
              <button
                className="quiet"
                onClick={() => go(() => session.send("farm.harvest", { plot: row.id }))}
                disabled={busy || occupied !== null}
                title={occupied ?? "убрать не глядя: семенной фонд потеряет силу"}
              >
                Убрать
              </button>
            </>
          )}
          </div>
        </div>
      ))}

      <p className="note">
        Новую делянку размечают в окне «Земля»: межевание — дело земли, а не
        земледелия.
      </p>

      <p className="note">
        Рост идёт офлайн, уход — раз в сутки и только ногами: пропущенные сутки
        режут урожай, но не обнуляют его. Монокультура истощает землю,
        чередование и пар лечат — межа помнит, что на ней росло.
      </p>
      <p className="note">
        Сеют семенами: у партии свой сорт и своя сила, и урожай считается по
        ним. Часть урожая остаётся своим семенем — с отбором фонд держится, без
        отбора вырождается, а гибрид ещё и расщепляется.
      </p>
      {rows.some((row) => row.state === "sown" && row.agrotech === false) && (
        <p className="note">
          Агротехники этого сорта вы не знаете: видно симптом, а не норма.
          Базовые восемь лежат в Библиотеке бесплатно; агротехнику выведенного
          сорта знает только его автор.
        </p>
      )}
    </section>
  );
}
