// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useState } from "react";
import { Amount } from "../../Amount";
import { chosen, tally } from "../../amounts";
import { Rule } from "../../Rule";
import { useBook, useSession } from "../../actions";
import { DropZone } from "../../DragMove";
import { CHEST_ANY, chestOf, chestZone, grip, noDrag } from "../../drag";
import { isVessel } from "../../liquids";
import type { Props } from "./shared";


/** Node storages: a chest, a shelf and everything with capacity in the vault.
 *
 * The chest itself is visible to anyone -- it stands in the room. Whoever
 * disposes of the node may open it: the owner, and on civic land the
 * authority (D-181). The limit is the same as for hands and hold -- kilograms.
 */
export function Storages({ look, busy, act }: Props) {
  const session = useSession();
  const book = useBook();
  //: How much of a stack to move, per item. Empty means the whole of it.
  const [parts, setParts] = useState<Record<string, number | null>>({});
  const chests = look.storages ?? [];
  if (chests.length === 0) return null;

  const setPart = (id: string, value: number | null) =>
    setParts((before) => ({ ...before, [id]: value }));
  //: Everything in the hands makes sense to put away: nothing is weightless in this world.
  const inHands = look.inventory;
  //: A liquid moves by the hose (D-230): a tank is filled from the canisters
  //: in the hands and emptied back into them, never taken by the handful.
  const canisters = inHands.filter((thing) => isVessel(book, thing.goods));

  return (
    <>
      {chests.map((chest) =>
        isVessel(book, chest.goods) ? (
          <section key={chest.id}>
            <h2>
              {chest.goods}
              <Rule>
                Тара берёт только жидкость, и жидкость живёт только в таре: в бак
                переливают из канистры и из бака — в канистру.
              </Rule>
            </h2>
            <p className="note">
              налито {chest.mass.toFixed(1)} из {chest.capacity.toFixed(0)} кг
            </p>
            {!chest.mine ? (
              <p className="note">Чужой бак: что внутри — не ваше дело.</p>
            ) : (
              <>
                {chest.content.length === 0 && <p className="note">пусто</p>}
                {chest.content.map((thing) => (
                  <p key={thing.id}>
                    {thing.goods} <span className="note">{tally(thing.goods, thing.amount)}</span>
                    {canisters.map((canister) => (
                      <button
                        key={canister.id}
                        className="quiet"
                        onClick={() =>
                          act(() =>
                            session.send("liquid.pour", {
                              from: chest.id,
                              to: canister.id,
                              goods: thing.goods,
                            }),
                          )
                        }
                        disabled={busy}
                        title="слить в канистру — сколько войдёт и сколько унесёте"
                      >
                        В {canister.goods.toLowerCase()}
                      </button>
                    ))}
                  </p>
                ))}
                {canisters.length === 0 ? (
                  <p className="note">Нужна канистра в руках: жидкость не носят в ладонях.</p>
                ) : (
                  canisters.map((canister) => (
                    <button
                      key={canister.id}
                      className="quiet"
                      onClick={() =>
                        act(() =>
                          session.send("liquid.pour", { from: canister.id, to: chest.id }),
                        )
                      }
                      disabled={busy || (canister.content ?? []).length === 0}
                    >
                      Перелить из «{canister.goods}»
                    </button>
                  ))
                )}
              </>
            )}
          </section>
        ) : (
        <section key={chest.id}>
          <h2>
            {chest.goods}
            <Rule>
              Дом хранит то, что не увезти в руках; полный сундук не уносят.
            </Rule>
          </h2>
          <p className="note">
            занято {chest.mass.toFixed(1)} из {chest.capacity.toFixed(0)} кг
          </p>
          {!chest.mine ? (
            <p className="note">Чужое хранилище: что внутри — не ваше дело.</p>
          ) : (
            <>
              {/* The drag pair (D-238): hands rows drop into the chest, chest
                  rows drop into the hands below -- same commands as the
                  buttons, which stay the path for keyboards and touch. */}
              <DropZone
                zone={chestZone(chest.id)}
                accepts={["hands"]}
                disabled={busy}
                hint="перетащите сюда предмет, чтобы убрать в хранилище"
                onMove={(stack, amount) =>
                  act(() =>
                    session.send("storage.put", {
                      storage: chest.id,
                      item: stack.item,
                      amount,
                    }),
                  )
                }
              >
                {chest.content.length > 0 ? (
                  <table>
                    <tbody>
                      {chest.content.map((thing) => (
                        <tr
                          key={thing.id}
                          {...grip({
                            item: thing.id,
                            goods: thing.goods,
                            label: thing.goods,
                            amount: thing.amount,
                            zone: chestZone(chest.id),
                          })}
                        >
                          <td>{thing.goods}</td>
                          <td className="note">{tally(thing.goods, thing.amount)}</td>
                          <td {...noDrag}>
                            <Amount
                              goods={thing.goods}
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
                                  session.send("storage.take", {
                                    storage: chest.id,
                                    item: thing.id,
                                    amount: chosen(parts[thing.id] ?? null, thing.amount),
                                  }),
                                )
                              }
                              disabled={busy}
                              title="забрать в руки — сколько унесёте; строку можно и перетащить вниз"
                            >
                              Забрать
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <p className="note">пусто</p>
                )}
              </DropZone>
              <DropZone
                zone="hands"
                //: Any chest, not only the one above: three hands-tables may
                //: stand in one window, and the player drops on the nearest.
                //: The exact chest rides in the stack's own zone name.
                accepts={[CHEST_ANY]}
                disabled={busy}
                hint="перетащите сюда предмет, чтобы забрать в руки"
                onMove={(stack, amount) =>
                  act(() =>
                    session.send("storage.take", {
                      storage: chestOf(stack.zone),
                      item: stack.item,
                      amount,
                    }),
                  )
                }
              >
                {inHands.length > 0 ? (
                  <table>
                    <tbody>
                      {inHands.map((thing) => (
                        <tr
                          key={thing.id}
                          {...grip({
                            item: thing.id,
                            goods: thing.goods,
                            label: thing.goods,
                            amount: thing.amount,
                            zone: "hands",
                          })}
                        >
                          <td>{thing.goods}</td>
                          <td className="note">
                            {tally(thing.goods, thing.amount)} ·{" "}
                            {(thing.mass * thing.amount).toFixed(1)} кг
                          </td>
                          <td {...noDrag}>
                            <Amount
                              goods={thing.goods}
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
                                  session.send("storage.put", {
                                    storage: chest.id,
                                    item: thing.id,
                                    amount: chosen(parts[thing.id] ?? null, thing.amount),
                                  }),
                                )
                              }
                              disabled={busy}
                            >
                              Положить
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <p className="note">руки пусты</p>
                )}
              </DropZone>
            </>
          )}
        </section>
        ),
      )}
    </>
  );
}
