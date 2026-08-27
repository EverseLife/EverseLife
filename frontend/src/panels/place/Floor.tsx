// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useState } from "react";
import { Amount } from "../../Amount";
import { chosen, tally } from "../../amounts";
import { useSession } from "../../actions";
import { DropZone } from "../../DragMove";
import { grip, noDrag } from "../../drag";
import type { Props } from "./shared";


/** The floor itself: what lies here, and putting things on it (D-192, D-204).
 *
 * Putting a thing down is the first thing a person back from the mine does.
 * Cargo takes area, area is finite, and a chest saves it -- hence three honest
 * answers to "where do I keep this": build more, buy chests, haul away.
 *
 * A passer-by through a shut location is not inside, and for them the floor is
 * closed.
 */
export function Floor({ look, busy, act }: Props) {
  const session = useSession();
  const [parts, setParts] = useState<Record<string, number | null>>({});
  const floor = look.floor;
  if (!floor) return null;

  const setPart = (id: string, value: number | null) =>
    setParts((before) => ({ ...before, [id]: value }));
  const room = floor.space;
  const roofed = room.roofed > 0;
  const open = floor.open !== false;
  //: Everything in the hands can be put down: nothing here is weightless, and
  //: the area budget is what says whether it fits.
  const inHands = look.inventory;

  return (
    <section>
      <h2>{roofed ? "В здании" : "На земле"}</h2>
      <p className="note">
        занято {room.used.toFixed(1)} из {room.area.toFixed(0)} м²
        {room.cargo_mass > 0 && ` · груза ${room.cargo_mass.toFixed(1)} кг`}
        {room.slots_used > 0 && ` · оборудования ${room.slots_used}`}
      </p>

      {/* The drag pair (D-238): floor rows drag into the hands zone below,
          hands rows drag up here. The buttons stay the equal path -- same
          commands, and the only path for keyboards and touch. */}
      <DropZone
        zone="floor"
        accepts={["hands"]}
        disabled={!open || busy}
        onMove={(stack, amount) =>
          act(() => session.send("ground.drop", { item: stack.item, amount }))
        }
      >
        {floor.things.length > 0 ? (
          <table>
            <tbody>
              {floor.things.map((thing) => (
                <tr
                  key={thing.id}
                  {...(open
                    ? grip({
                        item: thing.id,
                        goods: thing.goods,
                        label: thing.flavor ?? thing.goods,
                        amount: thing.amount,
                        zone: "floor",
                      })
                    : {})}
                >
                  <td>{thing.flavor ?? thing.goods}</td>
                  <td className="note">
                    {tally(thing.goods, thing.amount)} ·{" "}
                    {(thing.mass * thing.amount).toFixed(1)} кг
                  </td>
                  <td {...noDrag}>
                    {open && (
                      <Amount
                        goods={thing.goods}
                        value={parts[thing.id] ?? null}
                        max={thing.amount}
                        onChange={(value) => setPart(thing.id, value)}
                      />
                    )}
                  </td>
                  <td>
                    {open && (
                      <button
                        className="quiet"
                        onClick={() =>
                          act(() =>
                            session.send("ground.pick", {
                              item: thing.id,
                              amount: chosen(parts[thing.id] ?? null, thing.amount),
                            }),
                          )
                        }
                        disabled={busy}
                        title="взять в руки — сколько унесёте; строку можно и перетащить вниз"
                      >
                        Взять
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="note">пусто</p>
        )}
      </DropZone>

      {open && (
        <DropZone
          zone="hands"
          accepts={["floor"]}
          disabled={busy}
          onMove={(stack, amount) =>
            act(() => session.send("ground.pick", { item: stack.item, amount }))
          }
        >
          <h3 className="drop-head">В руках</h3>
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
                            session.send("ground.drop", {
                              item: thing.id,
                              amount: chosen(parts[thing.id] ?? null, thing.amount),
                            }),
                          )
                        }
                        disabled={busy}
                        title="положить здесь — сколько поместится; строку можно и перетащить вверх"
                      >
                        Положить
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="note">руки пусты — сюда можно перетащить лежащее</p>
          )}
        </DropZone>
      )}

      <p className="note">
        {!open
          ? "Вы здесь проходом: чужая закрытая локация пола вам не отдаёт."
          : floor.mine
            ? "Лежащее занимает площадь; в сундуке — не занимает."
            : "Чужое место, но лежащее на земле берёт всякий, кого сюда пустили."}
      </p>
    </section>
  );
}
