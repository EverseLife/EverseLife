// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import * as api from "../../api";
import type { Bench } from "../../api";
import { useBook, useNames, useSession } from "../../actions";
import { isBuilt } from "../../classes";
import { DropZone } from "../../DragMove";
import { t } from "../../locale";
import { goodsName } from "../../names";
import type { Props } from "./shared";
import { placeable } from "./shared";


/** The common equipment section: machines and furniture differ only by kind.
 *
 * Both stand among the sections of the "Дом" window and go silent where there
 * is nothing to say: nothing placed and nothing in hands to place is not worth
 * a header. The house summary above already counts the slots.
 *
 * **Its own drop zone, and that is the point of it** (D-238, amendment 4). A
 * machine in the hands has two futures and they are not the same thing: laid on
 * the floor it is cargo that takes area and can be picked up by whoever walks
 * in; **installed** it takes a place in the building and becomes something one
 * works at. The floor and the chests already took a drag; without a zone here
 * the only gesture that meant "install" was a button, so the hand had to know
 * in advance which of the two it wanted. Now the surface decides: drop it on
 * the floor to leave it lying, drop it here to put it up.
 */
export function Equipment({
  title,
  things,
  kind,
  look,
  busy,
  act,
  note,
}: Props & {
  title: string;
  things: Bench[];
  kind: "station" | "furniture";
  note: string;
}) {
  const session = useSession();
  const book = useBook();
  const names = useNames();
  const mine = api.isMine(look);
  //: The owner places and removes, and on civic land the authority (`station.may_build`).
  //: In somebody else's house neither is entitled.
  const hasPower = Boolean(
    api.isCivic(look.node) && !look.node?.owner && look.city?.powers.includes("laws"),
  );

  const inHands = placeable(look, book, kind);

  if (things.length === 0 && !((mine || hasPower) && inHands.length > 0)) {
    return null;
  }

  const home = api.houseOf(look.node);
  const noRoom = home.used >= home.slots;
  //: What one machine costs the building, in square metres (D-106). A place is
  //: `build.slots_per_area` of the house, and it is the same for every machine:
  //: the vault charges by the place, not by the thing. The summary above counts
  //: the places; this says what a place **is**, so "мест 3 из 4" turns into a
  //: number one can compare with the plan of the next storey.
  const perThing = Number(book?.constants?.["build.slots_per_area"] ?? 0);

  return (
    <section>
      <h2>{title}</h2>
      {things.length > 0 && (
        <table>
          <tbody>
            {things.map((thing) => (
              <tr key={thing.id}>
                <td>{goodsName(names, thing.goods)}</td>
                <td className="note">
                  {perThing > 0 && t("ui-place-area", { area: perThing.toFixed(0) })}
                  {thing.quality == null
                    ? ""
                    : t("ui-place-equipment-quality", {
                        quality: thing.quality.toFixed(0),
                      })}
                  {thing.condition < 100 &&
                    t("ui-place-equipment-condition", {
                      condition: thing.condition.toFixed(0),
                    })}
                </td>
                <td className="note">
                  {/* A battery's state is its charge, not "busy": one does not
                      work at it, it holds energy (D-179). */}
                  {thing.charge != null
                    ? t("ui-place-equipment-charge", { charge: thing.charge.toFixed(0) })
                    : kind === "station"
                      ? thing.busy
                        ? thing.mine
                          ? t("ui-place-equipment-busy-mine")
                          : t("ui-place-equipment-busy")
                        : t("ui-place-equipment-free")
                      : ""}
                </td>
                <td>
                  {(mine || hasPower) && !isBuilt(book, thing.goods) && (
                    <button
                      className="quiet"
                      onClick={() =>
                        act(() => session.send("station.take", { item: thing.id }))
                      }
                      disabled={busy || thing.busy}
                      title={t("ui-place-equipment-take-hint")}
                    >
                      {t("ui-place-equipment-take")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* The section already appears the moment something placeable is in the
          hands, which is exactly when a drag could start -- so the zone is
          there to receive it, with its invitation showing. */}
      {(mine || hasPower) && (
        <DropZone
          zone={kind === "station" ? "stations" : "furniture"}
          accepts={["hands"]}
          disabled={busy || noRoom}
          //: The whole stack, and no question: `station.place` is one command
          //: over one stack, and the engine has no "how much" to answer with.
          whole
          hint={
            noRoom
              ? undefined
              : kind === "station"
                ? t("ui-place-equipment-drop-station")
                : t("ui-place-equipment-drop-furniture")
          }
          onMove={(stack) =>
            act(() => session.send("station.place", { item: stack.item }))
          }
        >
          {inHands.length > 0 && (
            <div className="row">
              {inHands.map((thing) => (
                <button
                  key={thing.id}
                  onClick={() => act(() => session.send("station.place", { item: thing.id }))}
                  disabled={busy || noRoom}
                  title={
                    noRoom
                      ? t("ui-place-equipment-no-room-hint")
                      : t("ui-place-equipment-place-hint")
                  }
                >
                  {t("ui-place-equipment-place")} {goodsName(names, thing.goods)}
                </button>
              ))}
            </div>
          )}
          {noRoom && <p className="note">{t("ui-place-equipment-no-room")}</p>}
        </DropZone>
      )}
      <p className="note">
        {note}
        {perThing > 0 &&
          t("ui-place-equipment-slots", {
            area: perThing.toFixed(0),
            used: home.used,
            slots: home.slots,
          })}
      </p>
    </section>
  );
}
