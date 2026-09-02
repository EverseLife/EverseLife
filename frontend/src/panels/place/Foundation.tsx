// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useEffect, useState } from "react";
import * as api from "../../api";
import { useNames, useSession } from "../../actions";
import { t } from "../../locale";
import { goodsName } from "../../names";
import type { Props } from "./shared";


/** Founding a city: four buildings, not a coin (D-023, D-098, D-159).
 *
 * The window is shown only where founding is possible at all -- on a planet
 * node no city covers, by somebody who belongs to no city yet. Which of those
 * a place is, is the server's to answer: the window is here when `foundation`
 * came, and absent when it did not. The list of what is missing is visible
 * **before** the attempt: the entry threshold is buildings, and the person
 * must understand which ones exactly they lack.
 *
 * Two halves make that list, and they come from different places on purpose.
 * The roles and the machines that fill them are a catalog constant and are
 * read once from `/public/founding`; `look` carries only the keys of the ones
 * this node lacks (D-225). The tick beside a role is then a comparison of
 * keys -- it used to be a comparison of two translated sentences, which is a
 * thing that works until somebody edits the wording.
 *
 * At founding the land goes to the city: from then on the authority hands it
 * out, not the yard owner (D-089), and that is said right here rather than found out later.
 */
export function Foundation({ look, busy, act }: Props) {
  const session = useSession();
  const names = useNames();
  const ground = look.foundation ?? null;
  const [name, setName] = useState("");
  const [roles, setRoles] = useState<api.FoundingRole[]>([]);
  //: Asked for the first time the window is actually offered, and never
  //: again: the table is a constant, and somebody who never founds a city
  //: never fetches it.
  const wanted = ground !== null && roles.length === 0;
  useEffect(() => {
    if (!wanted) return;
    //: A failed read is not retried -- `wanted` never changes again -- so the
    //: rows below fall back to what `look` sent rather than the window going
    //: silent about the one thing it exists to say.
    void api.founding().then(
      (table) => setRoles(table.roles),
      (why) => console.warn("founding roles: " + String(why)),
    );
  }, [wanted]);
  if (!ground) return null;

  const ready = ground.missing.length === 0;
  //: Without the table there is still something true to show: the roles that
  //: are missing, all of them ticked off as missing. Fewer rows than the
  //: place has roles, and no machines named -- but the question "what do I
  //: lack" keeps its answer, which an empty table would not.
  const rows: api.FoundingRole[] = roles.length
    ? roles
    : ground.missing.map((role) => ({ role, any_of: [] }));

  return (
    <section>
      <h2>{t("ui-place-foundation-title")}</h2>
      <table>
        <tbody>
          {rows.map((need) => (
            <tr key={need.role}>
              <td>{ground.missing.includes(need.role) ? "—" : "✓"}</td>
              {/* The world's own word for the role -- the same message the
                  door's refusal quotes, rendered from the server's own FTL
                  that `/public/i18n` handed over (D-251). */}
              <td>{t(`city-role-${need.role}`)}</td>
              <td className="note">
                {need.any_of.map((one) => goodsName(names, one)).join(" · ")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={t("ui-place-foundation-name")}
          maxLength={api.CITY_NAME_LIMIT}
          disabled={!ready}
        />
        <button
          onClick={() => act(() => session.send("city.found", { name: name }))}
          disabled={busy || !ready || !name.trim()}
        >
          {t("ui-place-foundation-found")}
        </button>
      </div>
      <p className="note">
        {ready
          ? t("ui-place-foundation-ready")
          : t("ui-place-foundation-threshold")}
      </p>
    </section>
  );
}
