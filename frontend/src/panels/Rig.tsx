// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

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
import { firstOfClass } from "../classes";
import type { Look } from "../api";
import { Rule } from "../Rule";
import { Refusal, useActions, useBook, useEdition, useNames, useSession } from "../actions";
import { t } from "../locale";
import { goodsName } from "../names";

/** The thing class of a drilling machine, the word the engine binds to (`rig.RIG`). */
const RIG = "rig";

type Props = {
  look: Look;
  /** The vault catalog: the rig is known by its class, not by name (D-215). */
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

export function Rig({ look }: Omit<Props, "busy" | "act">) {
  const session = useSession();
  const book = useBook();
  const names = useNames();
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const [rigs, setRigs] = useState<RigRow[]>([]);
  const rigName = firstOfClass(book, look.inventory.map((t) => t.goods), RIG);
  const machine = look.inventory.find((t) => t.goods === rigName);
  const vein = look.veins?.[0];

  const reload = useCallback(async () => {
    const answer = await session.send("rig.status");
    setRigs(answer.rigs as RigRow[]);
  }, [session]);
  //: Reread when the world says so (D-226), not on every look.
  const edition = useEdition("energy.", "station.");

  useEffect(() => {
    void reload();
  }, [reload, edition]);

  const go = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await reload();
    });

  if (rigs.length === 0 && !machine) return null;

  return (
    <section>
      <Refusal of={acting} />
      <h2>
        {t("ui-rig-title")}
        <Rule>{t("ui-rig-rule")}</Rule>
      </h2>

      {rigs.map((u) => (
        <div key={u.id}>
          <p className="sign">
            {t("ui-rig-hopper", {
              resource: u.resource == null ? "—" : goodsName(names, u.resource),
              hopper: u.hopper.toFixed(0),
              capacity: u.capacity.toFixed(0),
            })}
            {u.full && <b> · {t("ui-rig-full")}</b>}
          </p>
          <p className="note">
            {t("ui-rig-state", {
              hours: u.hours_of_fuel.toFixed(1),
              fuel: u.fuel.toFixed(0),
              condition: u.condition.toFixed(0),
              left: u.vein_left.toFixed(0),
            })}
            {u.fuel <= 0 && <b> · {t("ui-rig-no-fuel")}</b>}
          </p>
          <button
            onClick={() => go(() => session.send("rig.empty", { rig: u.id }))}
            disabled={busy || u.hopper <= 0}
          >
            {t("ui-rig-empty")}
          </button>
        </div>
      ))}

      {machine && rigs.length === 0 && (
        <>
          <p className="note">{t("ui-rig-in-hands")}</p>
          <button
            onClick={() =>
              go(() =>
                session.send("rig.place", { item: machine.id, vein: vein!.id }),
              )
            }
            disabled={busy || !vein}
          >
            {t("ui-rig-place")}
          </button>
        </>
      )}
    </section>
  );
}
