// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

import { useEffect, useState } from "react";
import type { Look, RoadWork } from "../../api";
import { Hint } from "../../Hint";
import { useSession } from "../../actions";
import { t } from "../../locale";

/** Roads from this node: what is laid, what sagged and what it costs (D-158).
 *
 * The surface rises by a tier for `road.surface_per_edge` of surface and
 * `road.build_hours` of time: offroad -> road -> paved highway. Without
 * maintenance a road overgrows back, so the condition is always shown -- an
 * overgrown one cuts the convoy off from a node it drove to yesterday.
 */
export function Roads({
  look,
  busy,
  act,
  only,
}: {
  look: Look;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  /** Show the road to this neighbour alone: the column speaks about one node. */
  only?: string;
}) {
  const session = useSession();
  const [roads, setRoads] = useState<RoadWork[]>([]);

  useEffect(() => {
    void session
      .send("road.here")
      .then((answer) => setRoads((answer.roads as RoadWork[]) ?? []))
      .catch(() => setRoads([]));
    //: Пересчитывается при переходе и после каждого действия: уложенная
    //: ступень меняет и покрытие, и остаток полотна в руках.
  }, [session, look.node?.key, look.inventory]);

  const shown = only ? roads.filter((path) => path.to === only) : roads;
  if (shown.length === 0) return null;
  const work_ = (edge: string, mend: boolean) =>
    act(() => session.send("road.lay", { edge, mend }));

  return (
    <div className="row roads">
      {shown.map((path) => (
        <span key={path.edge} className="note">
          {path.to}: {t(SURFACE_LABEL[path.surface])}
          {path.surface !== "trail" && ` ${path.condition.toFixed(0)}%`}
          {path.working ? (
            ` · ${t("ui-map-road-working")}`
          ) : (
            <>
              {/* Цена работы стоит на кнопке, а не в подсказке при наведении:
                  выключенная кнопка без объяснения читается как поломка, а с
                  телефона подсказку не увидеть вовсе. */}
              {path.next && path.needs != null && (
                <button
                  className="quiet"
                  onClick={() => work_(path.edge, false)}
                  disabled={busy || path.at_hand < path.needs}
                  title={t("ui-map-road-need", {
                    needs: path.needs.toFixed(0),
                    hand: path.at_hand.toFixed(0),
                  })}
                >
                  {t(path.surface === "trail" ? "ui-map-road-lay" : "ui-map-road-pave", {
                    needs: path.needs.toFixed(0),
                  })}
                </button>
              )}
              {path.mend_needs != null && (
                <button
                  className="quiet"
                  onClick={() => work_(path.edge, true)}
                  disabled={busy || path.at_hand < path.mend_needs}
                  title={t("ui-map-road-mend-need", { needs: path.mend_needs.toFixed(0) })}
                >
                  {t("ui-map-road-mend", { needs: path.mend_needs.toFixed(0) })}
                </button>
              )}
              {path.at_hand < Math.min(path.needs ?? Infinity, path.mend_needs ?? Infinity) && (
                <> · {t("ui-map-road-at-hand", { hand: path.at_hand.toFixed(0) })}</>
              )}
            </>
          )}
        </span>
      ))}
      <Hint>{t("ui-map-road-rule")}</Hint>
    </div>
  );
}

/** Surface in words: the player reads a road, not an enum. */
const SURFACE_LABEL: Record<RoadWork["surface"], string> = {
  trail: "ui-map-surface-trail",
  road: "ui-map-surface-road",
  paved: "ui-map-surface-paved",
};
