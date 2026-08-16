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
import type { Look, Session } from "../api";

type Props = {
  look: Look;
  session: Session;
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

export function Farm({ look, session, busy, act }: Props) {
  //: Nobody's land outside a city is farmed by whoever comes (D-198): the
  //: ground has no owner and never will, but the crop on it is somebody's.
  const nobodys = Boolean(look.node?.wild);
  const mine = Boolean(look.node?.mine) || nobodys;
  const owner = look.node?.owner ?? null;
  const [rows, setRows] = useState<Row[]>([]);
  const [plants, setPlants] = useState<
    { id: string; name: string; gives: string; seed: string }[]
  >([]);
  const [name, setName] = useState("");
  const [metres, setMetres] = useState(10);
  //: One sows with a batch of seeds, not a crop: the batch has its own cultivar and strength.
  const [batch, setBatch] = useState("");

  const current_ = look.node?.key;

  //: Seeds are recognised by name from vault data, not by the client's guess.
  const seedNames = new Set(plants.map((p) => p.seed));
  const seeds = look.inventory.filter((t) => seedNames.has(t.goods));

  const reload = useCallback(async () => {
    const answer = await session.send("farm.survey");
    setRows((answer.plots as Row[]).filter((row) => row.node_key === current_));
  }, [session, current_]);

  //: The summary is reread together with the general poll: ploughing is
  //: finished by the worker, and its completion comes from the world, not a click.
  useEffect(() => {
    void reload();
  }, [reload, look]);

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
        <h2>Земля</h2>
        {owner ? (
          <p className="note">
            Участок {owner}. Чужим хозяйством не управляют: наём — это доступ
            плюс доля через договор (D-116).
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
      <h2>Делянки</h2>

      {rows.length === 0 && (
        <p className="note">Земля не размечена. Сто метров — это столько делянок,
          сколько вы нарежете.</p>
      )}

      {rows.map((row) => (
        <div className="row plot" key={row.id}>
          <span>
            <b>{row.name}</b> · {row.area} м² · {STATE[row.state]}
            {row.state === "sown" && row.culture_name && (
              <> · {row.culture_name}{row.variety ? ` (${row.variety})` : ""}</>
            )}
            {" · плодородие "}{row.fertility.toFixed(0)}
            {/* С агротехникой — норма и остаток до неё; без неё — симптом.
                Знание превращает угадайку в решённую задачу (D-057). */}
            {row.state === "sown" && row.agrotech && (
              <>
                {row.fertility_required != null && (
                  <> из {row.fertility_required.toFixed(0)} нужных</>
                )}
                {row.asks_care && row.water_need != null && (
                  <> · полить сегодня, {row.water_need.toFixed(0)} л</>
                )}
                {(row.missed_days ?? 0) > 0 && (
                  <b> · пропущено {row.missed_days} сут.</b>
                )}
              </>
            )}
            {row.state === "sown" && !row.agrotech && (row.symptoms?.length ?? 0) > 0 && (
              <i>
                {" · "}
                {row.symptoms!.map((code) => SYMPTOM[code] ?? code).join(", ")}
              </i>
            )}
          </span>
          {row.state === "idle" && (
            <button onClick={() => go(() => session.send("farm.plow", { plot: row.id }))}
                    disabled={busy}>
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
                    {t.goods} · {t.amount.toFixed(0)}
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
                disabled={busy || seeds.length === 0}
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

              disabled={busy || (row.agrotech === true && !row.asks_care)}
              title={row.agrotech && !row.asks_care ? "сегодня уже ухожено" : ""}
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
                disabled={busy}
                title="отобрать лучшие растения на семена: фонд держит силу"
              >
                Убрать с отбором
              </button>
              <button
                className="quiet"
                onClick={() => go(() => session.send("farm.harvest", { plot: row.id }))}
                disabled={busy}
                title="убрать не глядя: семенной фонд потеряет силу"
              >
                Убрать
              </button>
            </>
          )}
        </div>
      ))}

      <div className="row">
        <input
          value={name}
          placeholder="имя делянки"
          onChange={(e) => setName(e.target.value)}
        />
        <input
          type="number"
          value={metres}
          onChange={(e) => setMetres(Number(e.target.value))}
          title="площадь, м²"
        />
        <button
          onClick={() =>
            go(async () => {
              await session.send("farm.mark", { name: name, area: metres });
              setName("");
            })
          }
          disabled={busy}
        >
          Разметить
        </button>
      </div>

      <p className="note">
        Рост идёт офлайн, уход — раз в сутки и только ногами: пропущенные сутки
        режут урожай, но не обнуляют его. Монокультура истощает землю,
        чередование и пар лечат — межа помнит, что на ней росло (D-118).
      </p>
      <p className="note">
        Сеют семенами: у партии свой сорт и своя сила, и урожай считается по
        ним. Часть урожая остаётся своим семенем — с отбором фонд держится, без
        отбора вырождается, а гибрид ещё и расщепляется (D-057, D-067).
      </p>
      {rows.some((row) => row.state === "sown" && row.agrotech === false) && (
        <p className="note">
          Агротехники этого сорта вы не знаете: видно симптом, а не норма.
          Базовые восемь лежат в Библиотеке бесплатно; агротехнику выведенного
          сорта знает только его автор (D-057).
        </p>
      )}
    </section>
  );
}
