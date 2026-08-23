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
import { Refusal, useActions, useSession } from "../actions";

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
      <h2>{plant.station}</h2>
      <p>
        топлива <b>{plant.stock.toFixed(1)}</b> · хватит на{" "}
        <b>
          {plant.hours_left == null
            ? "—"
            : duration(plant.hours_left * SECONDS_PER_HOUR)}
        </b>
      </p>
      <p className="note">
        жжёт {plant.draw.toFixed(1)} {plant.fuel.toLowerCase()} в час и даёт{" "}
        {plant.output.toFixed(0)} энергии
        {plant.count > 1 ? ` · станций ${plant.count}` : ""}
      </p>

      {inHands.length > 0 ? (
        <div className="row">
          <input
            type="number"
            min={0}
            max={atHand}
            value={qty ?? atHand}
            onChange={(e) => setQty(Number(e.target.value))}
            title={`в руках ${atHand.toFixed(1)}`}
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
            Засыпать {plant.fuel.toLowerCase()}
          </button>
          <span className="note">
            Засыпанное — городу: обратно топливо не берут.
          </span>
        </div>
      ) : (
        <p className="note">
          {plant.fuel} в руках нет. Станция стоит на подвозе: без топлива город
          сидит без энергии.
        </p>
      )}
    </section>
  );
}
