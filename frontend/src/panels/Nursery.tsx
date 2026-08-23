// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The breeding nursery: crossing and cultivars (D-057, D-067).
 *
 * Here one sees what the whole branch was started for: **a farmer's advantage
 * is property and knowledge, not a character level**. Two batches of seeds, a
 * full cycle of waiting -- and either a new cultivar or an empty bed, if what
 * came out is too similar to what already grows.
 *
 * The refusal comes from the field, not a window: the engine does not say
 * "too similar", it says "did not sprout". The gate is built into biology.
 */


import { useCallback, useEffect, useState } from "react";
import { tally } from "../amounts";
import type { Look, Thing } from "../api";
import { when } from "../clock";
import { Rule } from "../Rule";
import { Refusal, useActions, useEdition, useSession } from "../actions";

type Props = {
  look: Look;
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

export function Nursery({ look }: Omit<Props, "busy" | "act">) {
  const session = useSession();
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const [cultivars, setCultivars] = useState<Variety[]>([]);
  const [beds, setBeds] = useState<Bed[]>([]);
  const [first, setFirst] = useState("");
  const [second, setSecond] = useState("");
  const [name, setName] = useState("");
  const [notice, setNotice] = useState<string | null>(null);

  const seeds: Thing[] = look.inventory.filter((t) => t.vigor != null);

  const reload = useCallback(async () => {
    const answer = await session.send("breed.varieties");
    setCultivars(answer.varieties as Variety[]);
    setBeds(answer.nurseries as Bed[]);
  }, [session]);
  //: Reread when the world says so (D-226), not on every look.
  const edition = useEdition("farm.", "knowledge.");

  useEffect(() => {
    void reload();
  }, [reload, edition]);

  const go = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await reload();
    });

  return (
    <section>
      <Refusal of={acting} />
      <h2>Селекционный питомник</h2>

      <div className="row">
        <select value={first} onChange={(e) => setFirst(e.target.value)}>
          <option value="">— первый родитель —</option>
          {seeds.map((t) => (
            <option key={t.id} value={t.id}>
              {t.goods} · {t.variety ?? "сорт"} · {tally(t.goods, t.amount)}
            </option>
          ))}
        </select>
        <select value={second} onChange={(e) => setSecond(e.target.value)}>
          <option value="">— второй родитель —</option>
          {seeds.map((t) => (
            <option key={t.id} value={t.id}>
              {t.goods} · {t.variety ?? "сорт"} · {tally(t.goods, t.amount)}
            </option>
          ))}
        </select>
        <button
          onClick={() =>
            go(async () => {
              await session.send("breed.cross", { a: first, b: second });
              setNotice(null);
            })
          }
          disabled={busy || !first || !second || first === second}
        >
          Скрестить
        </button>
      </div>
      <p className="note">
        Скрещивают сорта одной культуры. Одна попытка стоит семян, места и
        полного цикла роста: селекция — занятие на недели, а не на вечер.
      </p>

      {beds.length > 0 && (
        <>
          <h3>В питомнике</h3>
          {beds.map((bed) => (
            <div className="row" key={bed.id}>
              <span>всходы {when(bed.ready_at)}</span>
              <button
                onClick={() =>
                  go(async () => {
                    const answer = await session.send("breed.gather", {
                      nursery: bed.id,
                    });
                    setNotice(
                      answer.sprouted
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
      {notice && <p className="sign">{notice}</p>}

      {cultivars.length > 0 && (
        <>
          <h3>
            Свои сорта
            <Rule>
              Гибрид даёт отличный урожай один раз — его семена расщепляются. Поколения
              отбора доводят его до постоянного сорта, и тогда автор даёт ему имя
              навсегда.
            </Rule>
          </h3>
          <table>
            <tbody>
              {cultivars.map((cultivar) => (
                <tr key={cultivar.id}>
                  <td>{cultivar.name ?? `гибрид, поколение ${cultivar.generation}`}</td>
                  <td className="note">
                    {cultivar.stable ? "постоянный" : "расщепляется"} · урожай{" "}
                    {cultivar.traits.yield_per_m2?.toFixed(2)} · цикл{" "}
                    {cultivar.traits.cycle_days?.toFixed(1)} сут
                  </td>
                  <td>
                    {cultivar.stable && !cultivar.name && (
                      <span className="row">
                        <input
                          value={name}
                          placeholder="имя сорта"
                          onChange={(e) => setName(e.target.value)}
                        />
                        <button
                          onClick={() =>
                            go(async () => {
                              await session.send("breed.name", {
                                variety: cultivar.id,
                                name: name,
                              });
                              setName("");
                            })
                          }
                          disabled={busy || !name.trim()}
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
        </>
      )}
    </section>
  );
}
