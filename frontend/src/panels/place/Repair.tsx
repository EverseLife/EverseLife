// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useState } from "react";
import { useNames, useSession } from "../../actions";
import { t } from "../../locale";
import { goodsName } from "../../names";
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
  const names = useNames();
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
          {t("ui-place-repair-estimate")}
        </button>
        <span className="note">
          {worn >= 100
            ? t("ui-place-repair-whole")
            : t("ui-place-repair-condition", { condition: worn.toFixed(0) })}
        </span>
      </div>

      {plan && plan.materials.length > 0 && (
        <>
          <table>
            <tbody>
              {plan.materials.map((m: any) => (
                <tr key={m.goods}>
                  <td>{goodsName(names, m.goods)}</td>
                  <td className={m.have < m.need ? "note" : undefined}>
                    {t("ui-place-materials-have", {
                      have: m.have.toFixed(1),
                      need: m.need.toFixed(1),
                    })}
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
              {t("ui-place-repair-do")}
            </button>
            <span className="note">
              {plan.going
                ? t("ui-place-repair-going")
                : short.length > 0
                  ? t("ui-place-short", {
                      what: short.map((m: any) => goodsName(names, m.goods)).join(", "),
                    })
                  : t("ui-place-repair-term", {
                      hours: (plan.minutes / 60).toFixed(1),
                    })}
            </span>
          </div>
        </>
      )}
    </>
  );
}
