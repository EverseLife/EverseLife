// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/**
 * The location and everything on it (D-089, D-106, D-116, D-150, D-204, D-205).
 *
 * The windows are cut by intent, not by where the code happened to grow, and
 * each stands on its own in the location's row (`Stand.tsx`):
 *
 * - **Участок** -- everything about the land itself: whose it is and what it is
 *   called, the door and the two lists (D-204), buying an empty plot, founding
 *   a city (D-159). Shut stops entry, never passage, so a neighbour is never
 *   cut off from their home;
 * - **Дом** -- build, then furnish: the walls and their demolition (D-205), and
 *   the machines and furniture that go into the house and take its slots
 *   (D-106, D-150). Working at somebody's machine is another matter: the
 *   machine has a row of its own;
 * - **На земле** -- storage, for everyone: the floor where whoever got in puts
 *   things down and picks them up (D-192, D-204), and the chests standing in
 *   the room (D-181). The door and the chest are the protection, not a rule;
 * - **Обоз** -- the wagon: harnessing, and the hold that carries what hands
 *   cannot (D-157);
 * - **Лес / Камни / Луг** -- extraction by the sign of the land (D-177), one
 *   row per sign, next to the other work of the place.
 *
 * Citizenship lives in the administration window (`Admin.tsx`): one joins a
 * city where the city makes its decisions (D-155, D-160). The former "Место"
 * window -- seven unrelated sections under one name -- is gone.
 */

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
  const ways = (book?.operations ?? []).filter((o: any) => o.place === sign);
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
        {ways.flatMap((operation: any) =>
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
