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
import { useSession } from "../../actions";
import type { Props } from "./shared";


export /** Mending one's own house: what it costs and how long it takes (D-218).
 *
 * A house stands at full strength right up to nothing and then falls, so the
 * only warning is the condition itself -- and it must be read here, next to the
 * button that answers it. The bill is asked for by the button, like the
 * building one: it is a question about a decision, and the decision is rare.
 */
function Repair({ look, busy, act }: Omit<Props, "book">) {
  const session = useSession();
  const [plan, setPlan] = useState<any>(null);
  const home = look.node?.building;
  const worn = home?.condition ?? 100;
  const short = (plan?.materials ?? []).filter((m: any) => m.have < m.need);

  const count = async () => {
    setPlan(await session.send("build.repair_estimate"));
  };

  return (
    <>
      <div className="row">
        <button className="quiet" onClick={() => act(count)} disabled={busy || worn >= 100}>
          Посчитать ремонт
        </button>
        <span className="note">
          {worn >= 100
            ? "Дом целёхонек: чинить в нём нечего."
            : `Состояние ${worn.toFixed(0)}%. На нуле дом обрушится вместе с тем, что стоит во дворе.`}
        </span>
      </div>

      {plan && plan.materials.length > 0 && (
        <>
          <table>
            <tbody>
              {plan.materials.map((m: any) => (
                <tr key={m.goods}>
                  <td>{m.goods}</td>
                  <td className={m.have < m.need ? "note" : undefined}>
                    {m.have.toFixed(1)} из {m.need.toFixed(1)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="row">
            <button
              onClick={() =>
                act(async () => {
                  await session.send("build.repair");
                  setPlan(null);
                })
              }
              disabled={busy || short.length > 0 || !plan.mine || plan.going}
            >
              Чинить
            </button>
            <span className="note">
              {plan.going
                ? "Ремонт уже идёт."
                : short.length > 0
                  ? `Не хватает: ${short.map((m: any) => m.goods).join(", ")}`
                  : `Работы на ${(plan.minutes / 60).toFixed(1)} ч; чинят тем же, чем построено.`}
            </span>
          </div>
        </>
      )}
    </>
  );
}
