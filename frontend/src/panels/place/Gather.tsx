// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useState } from "react";
import { Refusal, useActions, useBook, useSession } from "../../actions";
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
      <h2>{PLACES[sign] ?? sign}</h2>
      <div className="row">
        <input
          type="number"
          min={1}
          value={qty}
          onChange={(e) => setQty(Number(e.target.value))}
          title="сколько добыть"
        />
        {ways.flatMap((operation) =>
          (operation.gives as string[]).map((exit) => {
            const needs = operation.requires as string[];
            const fits = needs.every(hasMeans);
            return (
              <button
                key={`${operation.name}:${exit}`}
                onClick={() =>
                  act(() =>
                    session.send("craft.start", {
                      output: exit,
                      units: qty,
                      //: The button names the operation: one thing may
                      //: come from several ways (D-196).
                      way: operation.name,
                    }),
                  )
                }
                disabled={busy || qty <= 0 || !fits}
                title={
                  fits
                    ? needs.length > 0
                      ? `нужен ${needs.join(", ")}; готовое — в «делах»`
                      : "голыми руками, потому и дольше; готовое — в «делах»"
                    : `нужен: ${needs.join(", ")}`
                }
              >
                {operation.name}: {exit}
              </button>
            );
          }),
        )}
        <span className="note">
          Партия идёт временем, готовое забирается в «делах». Валежник и
          прочее лежащее — в «Собирательстве».
        </span>
      </div>
    </section>
  );
}
