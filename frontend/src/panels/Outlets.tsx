// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Where a batch's liquids go, and the room they find there (D-340).
 *
 * A master has no backlog, so the room on a manual batch's outlet is not
 * reserved: somebody may fill the tank during the hours, and then the surplus
 * spills at the finish. The owner accepted that on one condition -- that the
 * player sees it. So the same lines stand under the forecast and under the
 * running batch: per liquid, where it pours, the room there now against what
 * the batch gives, and a warning when the room falls short. A vent gas with a
 * way out of the place says where it goes instead.
 *
 * The place and the room are the engine's (`craft.plan`, `orders`); what the
 * batch gives is counted here from its size and the book (D-225). Reread with
 * the plan and the orders, never by a timer (D-226).
 */

import type { Outlet } from "../api";
import { useBook, useNames } from "../actions";
import { tally } from "../amounts";
import { outletNeed, outletShort } from "../liquids";
import { t } from "../locale";
import { goodsName } from "../names";

type Props = {
  output: string;
  units: number;
  outlets?: Outlet[];
};

export function Outlets({ output, units, outlets }: Props) {
  const book = useBook();
  const names = useNames();
  if (!outlets || outlets.length === 0) return null;
  return (
    <>
      {outlets.map((one) => {
        const goods = goodsName(names, one.goods);
        if (one.room === undefined) {
          return (
            <p className="note" key={one.goods}>
              {t("ui-workshop-outlet-gone", { goods, where: one.where })}
            </p>
          );
        }
        const short = outletShort(book, output, units, one);
        const words = {
          goods,
          where: one.where,
          room: tally(one.goods, one.room),
          need: tally(one.goods, outletNeed(book, output, units, one)),
        };
        return (
          <p className={short ? "trouble" : "note"} key={one.goods}>
            {t(short ? "ui-workshop-outlet-short" : "ui-workshop-outlet", words)}
          </p>
        );
      })}
    </>
  );
}
