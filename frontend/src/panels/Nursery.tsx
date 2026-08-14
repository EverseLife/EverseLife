/**
 * Селекционный питомник: скрещивание и сорта (D-057, D-067).
 *
 * Здесь видно то, ради чего вся ветка затевалась: **преимущество фермера — это
 * имущество и знание, а не уровень персонажа**. Две партии семян, полный цикл
 * ожидания — и либо новый сорт, либо пустая грядка, если вышедшее слишком
 * похоже на уже растущее.
 *
 * Отказ приходит полем, а не окном: движок не говорит «слишком похоже», он
 * говорит «не взошло». Гейт встроен в биологию.
 */

import { useCallback, useEffect, useState } from "react";
import type { Look, Session, Thing } from "../api";

type Props = {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

type Variety = {
  id: string;
  name: string | null;
  culture: string;
  stable: boolean;
  generation: number;
  traits: Record<string, number>;
};

type Bed = { id: string; ready_at: string };

export function Nursery({ look, session, busy, act }: Props) {
  const [сорта, setСорта] = useState<Variety[]>([]);
  const [грядки, setГрядки] = useState<Bed[]>([]);
  const [один, setОдин] = useState("");
  const [другой, setДругой] = useState("");
  const [имя, setИмя] = useState("");
  const [весть, setВесть] = useState<string | null>(null);

  const семена: Thing[] = look.inventory.filter((т) => т.vigor != null);

  const reload = useCallback(async () => {
    const ответ = await session.send("breed.varieties");
    setСорта(ответ.varieties as Variety[]);
    setГрядки(ответ.nurseries as Bed[]);
  }, [session]);

  useEffect(() => {
    void reload();
  }, [reload, look]);

  const го = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await reload();
    });

  return (
    <section>
      <h2>Селекционный питомник</h2>

      <div className="row">
        <select value={один} onChange={(e) => setОдин(e.target.value)}>
          <option value="">— первый родитель —</option>
          {семена.map((т) => (
            <option key={т.id} value={т.id}>
              {т.goods} · {т.variety ?? "сорт"} · {т.amount.toFixed(0)}
            </option>
          ))}
        </select>
        <select value={другой} onChange={(e) => setДругой(e.target.value)}>
          <option value="">— второй родитель —</option>
          {семена.map((т) => (
            <option key={т.id} value={т.id}>
              {т.goods} · {т.variety ?? "сорт"} · {т.amount.toFixed(0)}
            </option>
          ))}
        </select>
        <button
          onClick={() =>
            го(async () => {
              await session.send("breed.cross", { a: один, b: другой });
              setВесть(null);
            })
          }
          disabled={busy || !один || !другой || один === другой}
        >
          Скрестить
        </button>
      </div>
      <p className="note">
        Скрещивают сорта одной культуры. Одна попытка стоит семян, места и
        полного цикла роста: селекция — занятие на недели, а не на вечер.
      </p>

      {грядки.length > 0 && (
        <>
          <h3>В питомнике</h3>
          {грядки.map((грядка) => (
            <div className="row" key={грядка.id}>
              <span>всходы к {new Date(грядка.ready_at).toLocaleString()}</span>
              <button
                onClick={() =>
                  го(async () => {
                    const ответ = await session.send("breed.gather", {
                      nursery: грядка.id,
                    });
                    setВесть(
                      ответ.sprouted
                        ? "взошло: новый гибрид у вас в руках"
                        : "не взошло: вышедшее слишком похоже на уже растущее",
                    );
                  })
                }
                disabled={busy}
              >
                Забрать всходы
              </button>
            </div>
          ))}
        </>
      )}
      {весть && <p className="sign">{весть}</p>}

      {сорта.length > 0 && (
        <>
          <h3>Свои сорта</h3>
          <table>
            <tbody>
              {сорта.map((сорт) => (
                <tr key={сорт.id}>
                  <td>{сорт.name ?? `гибрид, поколение ${сорт.generation}`}</td>
                  <td className="note">
                    {сорт.stable ? "постоянный" : "расщепляется"} · урожай{" "}
                    {сорт.traits.yield_per_m2?.toFixed(2)} · цикл{" "}
                    {сорт.traits.cycle_days?.toFixed(1)} сут
                  </td>
                  <td>
                    {сорт.stable && !сорт.name && (
                      <span className="row">
                        <input
                          value={имя}
                          placeholder="имя сорта"
                          onChange={(e) => setИмя(e.target.value)}
                        />
                        <button
                          onClick={() =>
                            го(async () => {
                              await session.send("breed.name", {
                                variety: сорт.id,
                                name: имя,
                              });
                              setИмя("");
                            })
                          }
                          disabled={busy || !имя.trim()}
                        >
                          Назвать
                        </button>
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="note">
            Гибрид даёт отличный урожай один раз — его семена расщепляются.
            Поколения отбора доводят его до постоянного сорта, и тогда автор даёт
            ему имя навсегда.
          </p>
        </>
      )}
    </section>
  );
}
