// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useState } from "react";
import { Amount } from "../../Amount";
import { chosen, tally } from "../../amounts";
import { weightOf } from "../../arrange";
import { useBook, useNames, useSession } from "../../actions";
import { DropZone } from "../../DragMove";
import { grip, noDrag } from "../../drag";
import { t } from "../../locale";
import { flavorText, goodsName } from "../../names";
import { mayInstall } from "../../building";
import { isGear } from "../../classes";
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
  const names = useNames();
  const book = useBook();
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
  //: A machine lying here is cargo until it is put up (D-278): the holder of
  //: the place -- or anyone, on nobody's land -- stands it from the floor,
  //: while the building has a place for it.
  const standable = mayInstall(look);

  return (
    <section>
      <h2>{indoors ? t("ui-place-floor-title") : t("ui-place-ground-title")}</h2>
      <p className="note">
        {t("ui-place-floor-taken", {
          used: room.used.toFixed(1),
          area: room.area.toFixed(0),
        })}
        {room.cargo_mass > 0 &&
          t("ui-place-floor-cargo", { mass: room.cargo_mass.toFixed(1) })}
        {gear > 0 && t("ui-place-floor-gear", { count: gear })}
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
        hint={indoors ? t("ui-place-floor-drop") : t("ui-place-ground-drop")}
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
                        label: thing.flavor
                          ? flavorText(names, thing.flavor)
                          : goodsName(names, thing.goods),
                        amount: thing.amount,
                        zone: where,
                      })
                    : {})}
                >
                  <td>
                    {thing.flavor
                      ? flavorText(names, thing.flavor)
                      : goodsName(names, thing.goods)}
                  </td>
                  <td className="note">
                    {tally(thing.goods, thing.amount)}{" "}
                    {t("ui-place-floor-mass", {
                      mass: weightOf(thing).toFixed(1),
                    })}
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
                        title={t("ui-place-floor-pick-hint")}
                      >
                        {t("ui-place-floor-pick")}
                      </button>
                    )}
                    {standable && isGear(book, thing.goods) && (
                      <button
                        className="quiet"
                        onClick={() => act(() => session.send("station.place", { item: thing.id }))}
                        disabled={busy}
                        title={t("ui-place-floor-install-hint")}
                      >
                        {t("ui-place-floor-install")}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="note">{t("ui-place-empty")}</p>
        )}
      </DropZone>


      <p className="note">
        {!open
          ? t("ui-place-floor-passing")
          : rights.mine
            ? indoors
              ? t("ui-place-floor-rule")
              : t("ui-place-ground-rule")
            : t("ui-place-floor-guest")}
      </p>
    </section>
  );
}
