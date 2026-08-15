/**
 * Drilling rig: capital instead of labour (D-115).
 *
 * The machine does not sleep -- that is its whole strength, and in everything
 * else it loses to a human: lower output, bounded quality, eats the vein twice as fast.
 *
 * Exactly three things must be shown, because exactly these keep the
 * enterprise dependent on people: how much is in the hopper, how long the coal
 * lasts and what condition the machine is in. A full hopper and an empty coal
 * store are not errors but obligations.
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
  const [rigs, setRigs] = useState<RigRow[]>([]);
  const machine = look.inventory.find((t) => t.goods === "Буровая установка");
  const vein = look.veins?.[0];

  const reload = useCallback(async () => {
    const answer = await session.send("rig.status");
    setRigs(answer.rigs as RigRow[]);
  }, [session]);

  useEffect(() => {
    void reload();
  }, [reload, look]);

  const go = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await reload();
    });

  if (rigs.length === 0 && !machine) return null;

  return (
    <section>
      <h2>Буровая</h2>

      {rigs.map((u) => (
        <div key={u.id}>
          <p className="sign">
            {u.resource} · в бункере {u.hopper.toFixed(0)} из {u.capacity.toFixed(0)}
            {u.full && <b> · бункер полон, машина стоит</b>}
          </p>
          <p className="note">
            угля на {u.hours_of_fuel.toFixed(1)} ч ({u.fuel.toFixed(0)}) ·
            состояние {u.condition.toFixed(0)} · в жиле {u.vein_left.toFixed(0)}
            {u.fuel <= 0 && <b> · топливо кончилось, машина стоит</b>}
          </p>
          <button
            onClick={() => go(() => session.send("rig.empty", { rig: u.id }))}
            disabled={busy || u.hopper <= 0}
          >
            Вывезти бункер
          </button>
        </div>
      ))}

      {machine && rigs.length === 0 && (
        <>
          <p className="note">
            Установка в руках. Поставьте её на жилу — дальше она работает без
            вас, пока есть уголь и место в бункере.
          </p>
          <button
            onClick={() =>
              go(() =>
                session.send("rig.place", { item: machine.id, vein: vein!.id }),
              )
            }
            disabled={busy || !vein}
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
