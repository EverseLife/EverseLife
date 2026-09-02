// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov


/** One window of the location; what they share is in `shared.ts`. */

import { useState } from "react";
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
 * At founding the land goes to the city: from then on the authority hands it
 * out, not the yard owner (D-089), and that is said right here rather than found out later.
 */
export function Foundation({ look, busy, act }: Props) {
  const session = useSession();
  const names = useNames();
  const ground = look.foundation ?? null;
  const [name, setName] = useState("");
  if (!ground) return null;

  const ready = ground.missing.length === 0;

  return (
    <section>
      <h2>{t("ui-place-foundation-title")}</h2>
      <table>
        <tbody>
          {ground.needs.map((need) => (
            <tr key={need.role}>
              <td>{ground.missing.includes(need.role) ? "—" : "✓"}</td>
              <td>{need.role}</td>
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
