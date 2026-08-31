// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Fuel station: stock, draw and pouring coal in (D-082, D-189).
 *
 * The coal station eats fuel from the node it stands in and is dead without
 * supply -- that was the design from the start. What was missing is the hopper:
 * a player with coal had no way to hand it over, and city power rested on the
 * one time somebody seeded it.
 *
 * Anyone who came with coal may pour: hauling fuel is the supply mechanic, not
 * a privilege. There is no way back -- pouring in is a handover.
 */

import { useCallback, useEffect, useState } from "react";
import type { Look } from "../api";
import { duration } from "../clock";
import { Refusal, useActions, useNames, useSession } from "../actions";
import { t } from "../locale";
import { goodsName } from "../names";

type Props = {
  look: Look;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

/** What the station looks like from outside, as the server sends it. */
type Plant = {
  station: string;
  count: number;
  fuel: string;
  stock: number;
  draw: number;
  output: number;
  hours_left: number | null;
};

const SECONDS_PER_HOUR = 3600;

export function Plant({ look }: Omit<Props, "busy" | "act">) {
  const session = useSession();
  const names = useNames();
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const [plant, setPlant] = useState<Plant | null>(null);
  const [qty, setQty] = useState<number | null>(null);

  const reload = useCallback(async () => {
    try {
      const answer = await session.send("energy.plant");
      setPlant((answer.plant as Plant | null) ?? null);
    } catch {
      setPlant(null);
    }
  }, [session]);

  useEffect(() => {
    void reload();
  }, [reload, look.node?.key, look.inventory]);

  if (!plant) return null;

  //: The wire names the station and its fuel by id (D-251); the words below
  //: come from the renames bundle.
  const fuelWord = goodsName(names, plant.fuel);
  const inHands = look.inventory.filter((thing) => thing.goods === plant.fuel);
  const atHand = inHands.reduce((sum, thing) => sum + thing.amount, 0);
  const go = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await reload();
    });

  return (
    <section>
      <Refusal of={acting} />
      <h2>{goodsName(names, plant.station)}</h2>
      <p>
        {t("ui-plant-fuel")} <b>{plant.stock.toFixed(1)}</b> · {t("ui-plant-lasts")}{" "}
        <b>
          {plant.hours_left == null
            ? "—"
            : duration(plant.hours_left * SECONDS_PER_HOUR)}
        </b>
      </p>
      <p className="note">
        {t("ui-plant-burn", {
          draw: plant.draw.toFixed(1),
          fuel: fuelWord.toLowerCase(),
          output: plant.output.toFixed(0),
        })}
        {plant.count > 1 ? ` · ${t("ui-plant-count", { count: String(plant.count) })}` : ""}
      </p>

      {inHands.length > 0 ? (
        <div className="row">
          <input
            type="number"
            min={0}
            max={atHand}
            value={qty ?? atHand}
            onChange={(e) => setQty(Number(e.target.value))}
            title={t("ui-plant-at-hand", { amount: atHand.toFixed(1) })}
          />
          <button
            onClick={() =>
              go(async () => {
                await session.send("energy.fuel", {
                  item: inHands[0].id,
                  amount: Math.min(qty ?? atHand, inHands[0].amount),
                });
                setQty(null);
              })
            }
            disabled={busy || (qty ?? atHand) <= 0}
          >
            {t("ui-plant-pour", { fuel: fuelWord.toLowerCase() })}
          </button>
          <span className="note">{t("ui-plant-given")}</span>
        </div>
      ) : (
        <p className="note">{t("ui-plant-none", { fuel: fuelWord })}</p>
      )}
    </section>
  );
}
