// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * One's own standing orders, and the one thing done to them: taking them back.
 *
 * The rows live in two places on purpose. The sidebar keeps the whole list --
 * an order is money, is read from the road and belongs to the identity, which
 * is why the tab is the finance one and not the market. The terminal keeps the
 * ones standing **in this node**: what is on the counter and what is promised
 * off it are one question, and answering it used to mean leaving the market
 * panel for a tab in the other zone.
 *
 * One component and not two lists, so a change to how an order reads -- the
 * quality floor beside the tier, say -- reaches both at once.
 */

import * as api from "../../api";
import type { Order } from "../../api";
import { useNames, useSession } from "../../actions";
import { t } from "../../locale";
import { goodsKeyName, tierName } from "../../names";

export function Orders({
  orders,
  none,
  busy,
  act,
}: {
  orders: readonly Order[];
  /** What to say where there are none: the sidebar and the counter mean
   *  different things by an empty list, and each says its own. */
  none: string;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
}) {
  const session = useSession();
  const names = useNames();
  if (orders.length === 0) return <p className="note">{none}</p>;
  return (
    <>
      {orders.map((order) => (
        <div className="row" key={order.id}>
          {/* `side` is the wire's own word, and the variant is keyed by it:
              a variant key is an identifier, never a sentence chosen here. */}
          <span>
            {t("ui-side-order", {
              side: order.side,
              goods: goodsKeyName(names, order.goods),
              //: A buy's floor named by hand stands beside the tier: the
              //: wire carries it only when the tier alone cannot say it
              //: (D-239, D-225), so a bare tier needs no suffix.
              tier:
                order.min_quality != null
                  ? t("ui-market-order-floor", {
                      tier: tierName(names, order.tier),
                      floor: String(order.min_quality),
                    })
                  : tierName(names, order.tier),
              left: String(order.left),
              price: api.tk(order.price),
            })}
          </span>
          <button
            className="quiet"
            onClick={() => act(() => session.send("market.cancel", { order: order.id }))}
            disabled={busy}
          >
            {t("ui-side-order-cancel")}
          </button>
        </div>
      ))}
    </>
  );
}
