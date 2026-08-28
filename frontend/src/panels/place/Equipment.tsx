// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import * as api from "../../api";
import type { Bench } from "../../api";
import { useBook, useSession } from "../../actions";
import { DropZone } from "../../DragMove";
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

  return (
    <section>
      <h2>{title}</h2>
      {things.length > 0 && (
        <table>
          <tbody>
            {things.map((thing) => (
              <tr key={thing.id}>
                <td>{thing.goods}</td>
                <td className="note">
                  {thing.quality == null ? "" : `качество ${thing.quality.toFixed(0)}`}
                  {thing.condition < 100 && ` · сост. ${thing.condition.toFixed(0)}`}
                </td>
                <td className="note">
                  {/* A battery's state is its charge, not "busy": one does not
                      work at it, it holds energy (D-179). */}
                  {thing.charge != null
                    ? `заряд ${thing.charge.toFixed(0)} · заряжают в «хозяйстве»`
                    : kind === "station"
                      ? thing.busy
                        ? thing.mine
                          ? "занята вами"
                          : "занята"
                        : "свободна"
                      : ""}
                </td>
                <td>
                  {(mine || hasPower) && (
                    <button
                      className="quiet"
                      onClick={() =>
                        act(() => session.send("station.take", { item: thing.id }))
                      }
                      disabled={busy || thing.busy}
                      title="забрать в руки"
                    >
                      Забрать
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
                ? "перетащите сюда станок, чтобы поставить его в здание"
                : "перетащите сюда мебель, чтобы обставить здание"
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
                      ? "в здании нет места: стройте больше либо уносите лишнее"
                      : "поставить в здание"
                  }
                >
                  Поставить: {thing.goods}
                </button>
              ))}
            </div>
          )}
          {noRoom && <p className="note">в здании нет свободных мест</p>}
        </DropZone>
      )}
      <p className="note">{note}</p>
    </section>
  );
}
