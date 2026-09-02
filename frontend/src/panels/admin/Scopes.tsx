// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The checklist an office is assembled from.
 *
 * A right in this city is not one of four broad ones: an office is a set, and
 * the city -- not the engine -- invents the title over it. So the picker has
 * to draw two rows from two sources at once, the broad rights the engine knows
 * and one narrow right per law the server sent, and grey out whatever the
 * appointer does not hold himself. That is the whole file: the arithmetic of
 * "what may I hand on", which has nothing to do with the appointment itself.
 */

import * as api from "../../api";
import type { CityView } from "../../api";
import { useNames } from "../../actions";
import { t } from "../../locale";
import { lawName } from "../../names";

/** The set of rights for a new office: broad ones plus one per law. */
export function Scopes({
  city,
  selected,
  setSelected,
  can,
}: {
  city: CityView;
  selected: string[];
  setSelected: (who: (before: string[]) => string[]) => void;
  can: (right: string) => boolean;
}) {
  const names = useNames();
  const toggle = (right: string) =>
    setSelected((before) =>
      before.includes(right) ? before.filter((p) => p !== right) : [...before, right],
    );

  return (
    <div>
      <p className="note">{t("ui-admin-scopes-note")}</p>
      <div className="row">
        {/* `POWERS` holds message keys, not words: it is built once at import. */}
        {Object.entries(api.POWERS).map(([key, word]) => (
          <label className="note" key={key} title={can(key) ? "" : t("ui-admin-scopes-lacking")}>
            <input
              type="checkbox"
              checked={selected.includes(key)}
              disabled={!can(key)}
              onChange={() => toggle(key)}
            />{" "}
            {t(word)}
          </label>
        ))}
      </div>
      <div className="row">
        {Object.entries(city.laws).map(([key, law]) => {
          const right = api.LAW_SCOPE + key;
          return (
            <label className="note" key={key} title={law.note ?? ""}>
              <input
                type="checkbox"
                checked={selected.includes(right)}
                disabled={!can(right)}
                onChange={() => toggle(right)}
              />{" "}
              {lawName(names, key)}
            </label>
          );
        })}
      </div>
    </div>
  );
}
