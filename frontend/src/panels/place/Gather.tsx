// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useState } from "react";
import { Refusal, useActions, useBook, useNames, useSession } from "../../actions";
import { t } from "../../locale";
import { goodsName, propertyName, requirementName } from "../../names";
import type { Props } from "./shared";
import { PLACES } from "./shared";


/** Place extraction (D-177): felling without a machine.
 *
 * One window per sign, opened from its row. The batch runs as ordinary
 * craft -- time and tool from the vault, the finished product is seen in "jobs".
 * What lies on the ground -- deadwood, stones, flax -- is not gathered
 * here: that is foraging on empty land, a window of its own (D-210).
 */
export function Gather({
  look,
  sign,
}: Omit<Props, "busy" | "act"> & { sign: string }) {
  const session = useSession();
  const book = useBook();
  const names = useNames();
  //: Own waiting and own refusal: a window of its own in the row.
  const acting = useActions();
  const { busy, act } = acting;
  const [qty, setQty] = useState(10);
  const ways = (book?.operations ?? []).filter((o) => o.place === sign);
  if (ways.length === 0) return null;

  //: What satisfies the requirement: the item itself or any of the class ("Axe").
  const inHands = new Set(look.inventory.map((thing) => thing.goods));
  const hasMeans = (withWhat: string) =>
    inHands.has(withWhat) ||
    ((book?.tool_classes?.[withWhat] ?? []) as string[]).some((i) => inHands.has(i));

  return (
    <section>
      <Refusal of={acting} />
      <h2>{PLACES[sign] ? t(PLACES[sign]) : propertyName(names, sign)}</h2>
      <div className="row">
        <input
          type="number"
          min={1}
          value={qty}
          onChange={(e) => setQty(Number(e.target.value))}
          title={t("ui-place-gather-qty")}
        />
        {ways.flatMap((operation) =>
          (operation.gives as string[]).map((exit) => {
            const needs = operation.requires as string[];
            const needWords = needs.map((one) => requirementName(names, one));
            const fits = needs.every(hasMeans);
            return (
              <button
                key={`${operation.id ?? operation.name}:${exit}`}
                onClick={() =>
                  act(() =>
                    session.send("craft.start", {
                      output: exit,
                      units: qty,
                      //: The button names the operation by its id (D-251): one
                      //: thing may come from several ways (D-196).
                      way: operation.id ?? operation.name,
                    }),
                  )
                }
                disabled={busy || qty <= 0 || !fits}
                title={
                  fits
                    ? needWords.length > 0
                      ? t("ui-place-gather-needs", { needs: needWords.join(", ") })
                      : t("ui-place-gather-barehanded")
                    : t("ui-place-gather-missing", { needs: needWords.join(", ") })
                }
              >
                {operation.name}: {goodsName(names, exit)}
              </button>
            );
          }),
        )}
        <span className="note">{t("ui-place-gather-rule")}</span>
      </div>
    </section>
  );
}
