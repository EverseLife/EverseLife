// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useState } from "react";
import { useSession } from "../../actions";
import type { Props } from "./shared";


/** Mending one's own house: what it costs and how long it takes (D-218).
 *
 * A house stands at full strength right up to nothing and then falls, so the
 * only warning is the condition itself -- and it must be read here, next to the
 * button that answers it. The bill is asked for by the button, like the
 * building one: it is a question about a decision, and the decision is rare.
 */
export function Repair({ look, busy, act }: Props) {
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
