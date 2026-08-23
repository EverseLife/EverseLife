// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import * as api from "../../api";
import type { Bench } from "../../api";
import { useBook, useSession } from "../../actions";
import type { Props } from "./shared";
import { placeable } from "./shared";


/** The common equipment section: machines and furniture differ only by kind.
 *
 * Both stand among the sections of the "Дом" window and go silent where there
 * is nothing to say: nothing placed and nothing in hands to place is not worth
 * a header. The house summary above already counts the slots.
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
                  {/* У аккумулятора состояние — это заряд, а не «занята»:
                      за ним не работают, он хранит энергию (D-179). */}
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

      {(mine || hasPower) && inHands.length > 0 && (
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
          {noRoom && (
            <span className="note">в здании нет свободных мест</span>
          )}
        </div>
      )}
      <p className="note">{note}</p>
    </section>
  );
}
