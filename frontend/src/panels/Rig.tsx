/**
 * Буровая установка: капитал вместо труда (D-115).
 *
 * Машина не спит — в этом вся её сила, и во всём остальном она проигрывает
 * человеку: выход ниже, качество ограничено, жилу выедает вдвое быстрее.
 *
 * Показывать надо ровно три вещи, потому что ровно ими предприятие и держится
 * на людях: сколько в бункере, надолго ли угля и в каком станок состоянии.
 * Полный бункер и пустой угольный склад — это не ошибки, а обязательства.
 */

import { useCallback, useEffect, useState } from "react";
import type { Look, Session } from "../api";

type Props = {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

type RigRow = {
  id: string;
  resource: string | null;
  hopper: number;
  capacity: number;
  full: boolean;
  fuel: number;
  hours_of_fuel: number;
  condition: number;
  vein_left: number;
};

export function Rig({ look, session, busy, act }: Props) {
  const [установки, setУстановки] = useState<RigRow[]>([]);
  const станок = look.inventory.find((т) => т.goods === "Буровая установка");
  const жила = look.veins?.[0];

  const reload = useCallback(async () => {
    const ответ = await session.send("rig.status");
    setУстановки(ответ.rigs as RigRow[]);
  }, [session]);

  useEffect(() => {
    void reload();
  }, [reload, look]);

  const го = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await reload();
    });

  if (установки.length === 0 && !станок) return null;

  return (
    <section>
      <h2>Буровая</h2>

      {установки.map((у) => (
        <div key={у.id}>
          <p className="sign">
            {у.resource} · в бункере {у.hopper.toFixed(0)} из {у.capacity.toFixed(0)}
            {у.full && <b> · бункер полон, машина стоит</b>}
          </p>
          <p className="note">
            угля на {у.hours_of_fuel.toFixed(1)} ч ({у.fuel.toFixed(0)}) ·
            состояние {у.condition.toFixed(0)} · в жиле {у.vein_left.toFixed(0)}
            {у.fuel <= 0 && <b> · топливо кончилось, машина стоит</b>}
          </p>
          <button
            onClick={() => го(() => session.send("rig.empty", { rig: у.id }))}
            disabled={busy || у.hopper <= 0}
          >
            Вывезти бункер
          </button>
        </div>
      ))}

      {станок && установки.length === 0 && (
        <>
          <p className="note">
            Установка в руках. Поставьте её на жилу — дальше она работает без
            вас, пока есть уголь и место в бункере.
          </p>
          <button
            onClick={() =>
              го(() =>
                session.send("rig.place", { item: станок.id, vein: жила!.id }),
              )
            }
            disabled={busy || !жила}
          >
            Поставить на жилу
          </button>
        </>
      )}

      <p className="note">
        Машина не спит, но проигрывает человеку во всём остальном: выход ниже,
        качество ограничено настройкой, жилу выедает вдвое быстрее. Уголь возят
        люди, бункер вывозят люди, износ чинят люди — капитал нанимает
        общество, а не освобождает от него.
      </p>
    </section>
  );
}
