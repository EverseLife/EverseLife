// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useState } from "react";
import { Amount } from "../../Amount";
import { chosen, tally } from "../../amounts";
import { useSession } from "../../actions";
import { DropZone } from "../../DragMove";
import { grip, noDrag } from "../../drag";
import type { Props, Surface } from "./shared";


/** One of the node's two surfaces: what lies on it, and putting things there
 * (D-192, D-204, D-244).
 *
 * Putting a thing down is the first thing a person back from the mine does.
 * Cargo takes area, area is finite, and a chest saves it -- hence three honest
 * answers to "where do I keep this": build more, buy chests, haul away.
 *
 * A passer-by through a shut location is not inside, and for them the floor is
 * closed.
 */
export function Floor({ look, busy, act, where = "floor" }: Props & { where?: Surface }) {
  const session = useSession();
  const [parts, setParts] = useState<Record<string, number | null>>({});
  const indoors = where === "floor";
  const floor = indoors ? look.floor : look.ground;
  //: The door and the right belong to the **place**, not to a surface of it:
  //: whoever was let in reaches both, and the answer is sent once (D-225).
  const rights = look.floor;
  if (!floor || !rights) return null;
  //: A surface with no metres is not a place: a node with no building has no
  //: floor, and a house covering the whole plot leaves no ground beside it.
  //: Things already lying there keep it on screen -- a list one cannot see is
  //: worse than an empty one (D-244).
  if (floor.space.area <= 0 && floor.things.length === 0) return null;

  const setPart = (id: string, value: number | null) =>
    setParts((before) => ({ ...before, [id]: value }));
  const room = floor.space;
  //: Machines stand on a floor and never on bare ground (D-244), so the count
  //: is read off the floor itself rather than off whichever surface this window
  //: happens to be: the two answers have different shapes, and only one of them
  //: has slots at all.
  const gear = indoors ? (look.floor?.space.slots_used ?? 0) : 0;
  const open = rights.open !== false;

  return (
    <section>
      <h2>{indoors ? "На полу" : "На земле"}</h2>
      <p className="note">
        занято {room.used.toFixed(1)} из {room.area.toFixed(0)} м²
        {room.cargo_mass > 0 && ` · груза ${room.cargo_mass.toFixed(1)} кг`}
        {gear > 0 && ` · оборудования ${gear}`}
      </p>

      {/* One inventory, not two (D-238). This window used to carry a copy of
          what is in the hands, so that a row had somewhere to be dragged to
          and from; the sidebar's inventory is that surface now. Rows drag from
          it onto the floor and back out of it, and for a keyboard or a finger
          -- or a narrow screen, where the sidebar and the scene are different
          zones and never on screen together -- the same two moves are the
          "Взять" button here and "Положить…" in the sidebar's row menu.

          Somebody else's floor takes nothing: the engine refuses to pick up
          from one (D-192), so a drop there is one-way, and the interface does
          not offer a door that only opens outward. The row menu has always
          said so; the drop zone now says the same. */}
      <DropZone
        zone={where}
        accepts={["hands"]}
        disabled={!open || !rights.mine || busy}
        hint={
          indoors
            ? "перетащите сюда предмет, чтобы положить на пол"
            : "перетащите сюда предмет, чтобы положить на землю"
        }
        onMove={(stack, amount) =>
          act(() =>
            //: Which surface, said out loud: without it the engine would guess,
            //: and its guess is "indoors wherever there is a roof" -- which is
            //: exactly the two windows collapsing back into one (D-244).
            session.send("ground.drop", { item: stack.item, amount, indoors }),
          )
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
                        zone: where,
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


      <p className="note">
        {!open
          ? "Вы здесь проходом: чужая закрытая локация пола вам не отдаёт."
          : rights.mine
            ? indoors
              ? "Лежащее занимает площадь; в сундуке — не занимает. Обрушение дома" +
                " хоронит то, что лежит под крышей."
              : "Лежащее занимает площадь двора — того, что осталось от участка" +
                " вокруг дома. Дом упадёт — это уцелеет."
            : "Чужое место, но лежащее берёт всякий, кого сюда пустили."}
      </p>
    </section>
  );
}
