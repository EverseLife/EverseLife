// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useState } from "react";
import * as api from "../../api";
import { tally } from "../../amounts";
import { useSession } from "../../actions";
import type { Props } from "./shared";


/** Demolishing one's own house: the term, the return and what is in the way (D-205).
 *
 * The estimate is asked for by the button, not on every poll: it is a question
 * about a decision, and the decision is rare. Everything that blocks the work is
 * shown as reasons -- the engine names them, the window does not guess.
 */
export function Demolition({ look, busy, act }: Props) {
  const session = useSession();
  const [plan, setPlan] = useState<any>(null);
  const going = api.houseOf(look.node).sites.length > 0;

  const count = async () => {
    setPlan(await session.send("build.demolish_estimate"));
  };
  const blocking: string[] = plan?.blocking ?? [];

  return (
    <>
      <div className="row">
        <button className="quiet" onClick={() => act(count)} disabled={busy || going}>
          Посчитать снос
        </button>
        <span className="note">
          {going
            ? "Пока идёт стройка, сносить нечего: дождитесь её конца."
            : "Снос — работа: часть материалов вернётся, остальное сломается при разборе."}
        </span>
      </div>

      {plan && (
        <>
          {plan.back.length > 0 && (
            <table>
              <tbody>
                {plan.back.map((m: any) => (
                  <tr key={m.goods}>
                    <td>{m.goods}</td>
                    <td className="note">вернётся {tally(m.goods, m.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="row">
            <button
              onClick={() =>
                act(async () => {
                  await session.send("build.demolish");
                  setPlan(null);
                })
              }
              disabled={busy || blocking.length > 0 || !plan.mine}
              title={
                blocking.length > 0
                  ? "двор пустеет до сноса, а не после"
                  : "работы идут временем, материалы придут в конце"
              }
            >
              Снести {plan.area.toFixed(0)} м²
            </button>
            <span className="note">
              {blocking.length > 0
                ? `Сначала: ${blocking.join("; ")}`
                : `Работы на ${(plan.minutes / 60).toFixed(1)} ч. Участок станет пустым.`}
            </span>
          </div>
        </>
      )}
    </>
  );
}
