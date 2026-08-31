// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useState } from "react";
import type { Vehicle } from "../../api";
import { Amount } from "../../Amount";
import { chosen, tally } from "../../amounts";
import { Refusal, useActions, useNames, useSession } from "../../actions";
import { DropZone } from "../../DragMove";
import { grip, noDrag } from "../../drag";
import { t } from "../../locale";
import { goodsName } from "../../names";
import type { Props } from "./shared";


/** Convoy: what it is harnessed to, what it carries and what one can harness to here (D-157).
 *
 * Cargo rides **in the hold**, not in hands: that is the only way to carry
 * more than `inventory.carry_mass`. Moving from hands to hold and back is
 * in-person -- on the go the hold is closed.
 *
 * A wagon standing in the node is an object of the node, so it has a row of
 * its own -- and a separate one from machines on purpose: nobody stands at a
 * wagon to work, one harnesses to it, and these two must not be confused.
 */
export function Convoy({ look }: Omit<Props, "busy" | "act">) {
  const session = useSession();
  const names = useNames();
  //: Own waiting and own refusal: a window of its own in the row.
  const acting = useActions();
  const { busy, act } = acting;
  const convoy = look.convoy ?? null;
  const standing = (look.vehicles ?? []).filter((t) => !t.taken);
  //: How much of a stack to move, per item. Empty means the whole of it.
  const [parts, setParts] = useState<Record<string, number | null>>({});
  if (!convoy && standing.length === 0) return null;

  const setPart = (id: string, value: number | null) =>
    setParts((before) => ({ ...before, [id]: value }));

  return (
    <section>
      <Refusal of={acting} />
      <h2>{t("ui-place-convoy-title")}</h2>
      {convoy ? (
        <>
          <p>
            {t("ui-place-convoy-harnessed")} <b>{goodsName(names, convoy.type_key)}</b> ·{" "}
            {t("ui-place-convoy-hold")}{" "}
            <b>
              {t("ui-place-convoy-hold-amount", {
                mass: convoy.mass.toFixed(1),
                capacity: convoy.capacity.toFixed(0),
              })}
            </b>{" "}
            {t("ui-place-convoy-speed", {
              speed: convoy.speed_k,
              condition: convoy.condition.toFixed(0),
            })}
          </p>
          {/* One inventory, not two (D-238): the sidebar's is the hands. A
              stack drags from there into the hold and a cargo row drags back
              into it; for a keyboard or a finger -- or a narrow screen, where
              the sidebar and the scene are different zones and never on screen
              together -- the same two moves are "Выгрузить" here and "В трюм"
              in the sidebar's row menu. */}
          <DropZone
            zone="hold"
            accepts={["hands"]}
            disabled={busy}
            hint={t("ui-place-convoy-drop")}
            onMove={(stack, amount) =>
              act(() => session.send("transport.load", { item: stack.item, amount }))
            }
          >
            {convoy.cargo.length > 0 ? (
              <table>
                <tbody>
                  {convoy.cargo.map((thing) => (
                    <tr
                      key={thing.id}
                      {...grip({
                        item: thing.id,
                        goods: thing.type_key,
                        label: goodsName(names, thing.type_key),
                        amount: thing.amount,
                        zone: "hold",
                      })}
                    >
                      <td>{goodsName(names, thing.type_key)}</td>
                      <td className="note">{tally(thing.type_key, thing.amount)}</td>
                      <td {...noDrag}>
                        <Amount
                          goods={thing.type_key}
                          value={parts[thing.id] ?? null}
                          max={thing.amount}
                          onChange={(value) => setPart(thing.id, value)}
                        />
                      </td>
                      <td>
                        <button
                          className="quiet"
                          onClick={() =>
                            act(() =>
                              session.send("transport.unload", {
                                item: thing.id,
                                amount: chosen(parts[thing.id] ?? null, thing.amount),
                              }),
                            )
                          }
                          disabled={busy}
                          title={t("ui-place-convoy-unload-hint")}
                        >
                          {t("ui-place-convoy-unload")}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="note">{t("ui-place-convoy-empty")}</p>
            )}
          </DropZone>
          <div className="row">
            <button
              onClick={() => act(() => session.send("transport.unharness"))}
              disabled={busy}
            >
              {t("ui-place-convoy-unharness")}
            </button>
            <span className="note">{t("ui-place-convoy-unharness-rule")}</span>
          </div>
        </>
      ) : (
        <div className="row">
          {standing.map((cart: Vehicle) => (
            <button
              key={cart.id}
              onClick={() =>
                act(() => session.send("transport.harness", { item: cart.id }))
              }
              disabled={busy}
              title={
                cart.capacity == null
                  ? t("ui-place-convoy-no-capacity")
                  : t("ui-place-convoy-cart", {
                      capacity: cart.capacity.toFixed(0),
                      speed: cart.speed_k,
                    })
              }
            >
              {t("ui-place-convoy-harness")} {goodsName(names, cart.goods)}
            </button>
          ))}
          <span className="note">{t("ui-place-convoy-rule")}</span>
        </div>
      )}
    </section>
  );
}
