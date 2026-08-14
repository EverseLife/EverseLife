/**
 * Делянки — сцена локации (D-118).
 *
 * Всё здесь присутственное: землю метят, пашут, сеют, обходят и убирают
 * ногами — и только на своём участке. Чужая земля показывает хозяина, дикая —
 * предлагает занять: земля не даётся объявлением (06-farming).
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
  /** Знает ли хозяин агротехнику сорта: от этого зависит всё ниже (D-057). */
  agrotech?: boolean;
  ripe_at?: string;
  asks_care?: boolean;
  missed_days?: number;
  fertility_required?: number;
  water_need?: number;
  /** Без агротехники видно только это — что с этим делать, догадывайся. */
  symptoms?: string[];
};

//: Симптомы общие для всех культур, нормы — разные. Поэтому опытный человек
//: читает грядку с одного взгляда даже у незнакомого сорта, а точные числа всё
//: равно должен знать или вывести (D-057).
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
  const мой = Boolean(look.node?.mine);
  const хозяин = look.node?.owner ?? null;
  const [rows, setRows] = useState<Row[]>([]);
  const [plants, setPlants] = useState<
    { id: string; name: string; gives: string; seed: string }[]
  >([]);
  const [имя, setИмя] = useState("");
  const [метров, setМетров] = useState(10);
  //: Сеют партией семян, а не культурой: у партии свой сорт и своя сила.
  const [партия, setПартия] = useState("");

  const текущий = look.node?.key;

  //: Семена узнаются по имени из данных вольта, а не по догадке клиента.
  const имена_семян = new Set(plants.map((p) => p.seed));
  const семена = look.inventory.filter((т) => имена_семян.has(т.goods));

  const reload = useCallback(async () => {
    const answer = await session.send("farm.survey");
    setRows((answer.plots as Row[]).filter((row) => row.node_key === текущий));
  }, [session, текущий]);

  //: Сводка перечитывается вместе с общим опросом: пахоту заканчивает воркер,
  //: и её завершение приходит миром, а не кликом.
  useEffect(() => {
    void reload();
  }, [reload, look]);

  useEffect(() => {
    void api.plants().then((p) => setPlants(p.plants));
  }, []);

  const го = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await reload();
    });

  //: Хозяйство ведёт владелец: сначала займи землю (06-farming).
  if (!мой) {
    return (
      <section>
        <h2>Земля</h2>
        {хозяин ? (
          <p className="note">
            Участок {хозяин}. Чужим хозяйством не управляют: наём — это доступ
            плюс доля через договор (D-116).
          </p>
        ) : (
          <>
            <p className="note">
              Дикий участок: плодородие {look.node?.fertility.toFixed(0)}.
              Занять может первый дошедший — земля не даётся объявлением.
            </p>
            <button
              onClick={() => act(() => session.send("land.claim"))}
              disabled={busy}
            >
              Занять участок
            </button>
          </>
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
                {row.symptoms!.map((код) => SYMPTOM[код] ?? код).join(", ")}
              </i>
            )}
          </span>
          {row.state === "idle" && (
            <button onClick={() => го(() => session.send("farm.plow", { plot: row.id }))}
                    disabled={busy}>
              Вспахать
            </button>
          )}
          {row.state === "plowed" && (
            <>
              <select
                value={партия || семена[0]?.id || ""}
                onChange={(e) => setПартия(e.target.value)}
              >
                {семена.length === 0 && <option value="">— семян нет —</option>}
                {семена.map((т) => (
                  <option key={т.id} value={т.id}>
                    {т.goods} · {т.amount.toFixed(0)}
                    {т.vigor != null ? ` · сила ${т.vigor.toFixed(0)}` : ""}
                  </option>
                ))}
              </select>
              <button
                onClick={() =>
                  го(() =>
                    session.send("farm.sow", {
                      plot: row.id,
                      seeds: партия || семена[0]?.id,
                    }),
                  )
                }
                disabled={busy || семена.length === 0}
              >
                Посеять
              </button>
            </>
          )}
          {row.state === "sown" && !row.ripe && (
            <button
              onClick={() => го(() => session.send("farm.care", { plot: row.id }))}
              //: Без агротехники «сегодня уже ухожено» игроку неизвестно —
              //: кнопка живая, а лишний обход движок отклонит сам.
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
                  го(() =>
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
                onClick={() => го(() => session.send("farm.harvest", { plot: row.id }))}
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
          value={имя}
          placeholder="имя делянки"
          onChange={(e) => setИмя(e.target.value)}
        />
        <input
          type="number"
          value={метров}
          onChange={(e) => setМетров(Number(e.target.value))}
          title="площадь, м²"
        />
        <button
          onClick={() =>
            го(async () => {
              await session.send("farm.mark", { name: имя, area: метров });
              setИмя("");
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
