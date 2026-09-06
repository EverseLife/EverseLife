// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The scout's offer (D-321): a point of the ground was tapped, and this is
 * how far it is and the one thing to do about it -- send the body to look.
 * The landscape and the road answer over the socket: too near, too far,
 * water, taken, a way in between -- and the refusal shows here in the
 * world's own words. What the scout finds comes back by the journal when
 * the run ends (D-226), and the map redraws by the touches.
 */

import { t } from "../../locale";

export function Survey({
  metres,
  busy,
  trouble,
  onGo,
  onClear,
}: {
  metres: number;
  busy: boolean;
  /** The world's refusal of the last run asked for, if any: the map's own. */
  trouble: string | null;
  onGo: () => void;
  onClear: () => void;
}) {
  return (
    <aside className="inspect survey">
      <p className="sign">{t("ui-map-survey-aim", { metres: Math.round(metres) })}</p>
      {trouble && <p className="trouble">{trouble}</p>}
      <div className="row">
        <button onClick={onGo} disabled={busy}>
          {t("ui-map-survey")}
        </button>
        <button className="quiet" onClick={onClear} disabled={busy}>
          {t("ui-map-survey-clear")}
        </button>
      </div>
    </aside>
  );
}
