// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The one thing a city says about itself in its own words.
 *
 * Everything else the administration shows is a figure the engine keeps: the
 * treasury, the laws, the tally of a poll. This is the only line nobody
 * verifies, and it is a whole file because that is the point -- a free
 * sentence needs its own draft state, its own limit and its own publish, and
 * mixing it into the window that edits enforced values would suggest the
 * engine stands behind it.
 */

import { useState } from "react";
import * as api from "../../api";
import type { CityView } from "../../api";
import { t } from "../../locale";
import { useSession } from "../../actions";

/** The city's word to newcomers -- what stands on the door card (D-183).
 *
 * It is edited by whoever admits citizens: the announcement is recruitment.
 * The engine does not enforce what is written -- the promise here binds people, not code. */
export function Word({
  city,
  can,
  go,
  busy,
}: {
  city: CityView;
  can: boolean;
  go: (what: () => Promise<unknown>) => void;
  busy: boolean;
}) {
  const session = useSession();
  const [text, setText] = useState<string | null>(null);
  const tally = text ?? city.about;

  return (
    <div>
      <h3>{t("ui-admin-word")}</h3>
      {city.about ? (
        <p className="say">«{city.about}»</p>
      ) : (
        <p className="note">{t("ui-admin-word-none")}</p>
      )}
      {can && (
        <>
          <div className="row">
            <textarea
              className="word"
              value={tally}
              maxLength={api.CITY_ABOUT_LIMIT}
              placeholder={t("ui-admin-word-hint")}
              onChange={(e) => setText(e.target.value)}
            />
          </div>
          <div className="row">
            <button
              onClick={() =>
                go(async () => {
                  await session.send("city.about", { text: tally });
                  setText(null);
                })
              }
              disabled={busy || tally === city.about}
            >
              {t("ui-admin-word-publish")}
            </button>
            <span className="note">
              {t("ui-admin-word-count", {
                used: String(tally.length),
                limit: String(api.CITY_ABOUT_LIMIT),
              })}
            </span>
          </div>
        </>
      )}
    </div>
  );
}
