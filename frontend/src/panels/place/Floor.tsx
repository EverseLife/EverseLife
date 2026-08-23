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
import { useSession } from "../../actions";
import type { Props } from "./shared";


export /** The floor itself: what lies here, and putting things on it (D-192, D-204).
 *
 * Putting a thing down is the first thing a person back from the mine does.
 * Cargo takes area, area is finite, and a chest saves it -- hence three honest
 * answers to "where do I keep this": build more, buy chests, haul away.
 *
 * A passer-by through a shut location is not inside, and for them the floor is
 * closed.
 */
function Floor({ look, busy, act }: Omit<Props, "book">) {
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

      {floor.things.length > 0 ? (
        <table>
          <tbody>
            {floor.things.map((thing) => (
              <tr key={thing.id}>
                <td>{thing.flavor ?? thing.goods}</td>
                <td className="note">
                  {tally(thing.goods, thing.amount)} ·{" "}
                  {(thing.mass * thing.amount).toFixed(1)} кг
                </td>
                <td>
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
                      title="взять в руки — сколько унесёте"
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

      {open && inHands.length > 0 && (
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
                        session.send("ground.drop", {
                          item: thing.id,
                          amount: chosen(parts[thing.id] ?? null, thing.amount),
                        }),
                      )
                    }
                    disabled={busy}
                    title="положить здесь — сколько поместится"
                  >
                    Положить
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
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
