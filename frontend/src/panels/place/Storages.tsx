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
import { Amount } from "../../Amount";
import { chosen, tally } from "../../amounts";
import { Rule } from "../../Rule";
import { useSession } from "../../actions";
import type { Props } from "./shared";


export /** Node storages: a chest, a shelf and everything with capacity in the vault.
 *
 * The chest itself is visible to anyone -- it stands in the room. Whoever
 * disposes of the node may open it: the owner, and on civic land the
 * authority (D-181). The limit is the same as for hands and hold -- kilograms.
 */
function Storages({ look, busy, act }: Omit<Props, "book">) {
  const session = useSession();
  //: How much of a stack to move, per item. Empty means the whole of it.
  const [parts, setParts] = useState<Record<string, number | null>>({});
  const chests = look.storages ?? [];
  if (chests.length === 0) return null;

  const setPart = (id: string, value: number | null) =>
    setParts((before) => ({ ...before, [id]: value }));
  //: Everything in the hands makes sense to put away: nothing is weightless in this world.
  const inHands = look.inventory;

  return (
    <>
      {chests.map((chest) => (
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
              {chest.content.length > 0 && (
                <table>
                  <tbody>
                    {chest.content.map((thing) => (
                      <tr key={thing.id}>
                        <td>{thing.goods}</td>
                        <td className="note">{tally(thing.goods, thing.amount)}</td>
                        <td>
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
                            title="забрать в руки — сколько унесёте"
                          >
                            Забрать
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {chest.content.length === 0 && <p className="note">пусто</p>}
              {inHands.length > 0 && (
                <table>
                  <tbody>
                    {inHands.map((thing) => (
                      <tr key={thing.id}>
                        <td>{thing.goods}</td>
                        <td className="note">
                          {tally(thing.goods, thing.amount)} ·{" "}
                          {(thing.mass * thing.amount).toFixed(1)} кг
                        </td>
                        <td>
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
              )}
            </>
          )}
        </section>
      ))}
    </>
  );
}
