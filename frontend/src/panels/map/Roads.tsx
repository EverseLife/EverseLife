// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

import { useEffect, useState } from "react";
import { SURFACE, type Look, type RoadWork } from "../../api";
import { Hint } from "../../Hint";
import { useSession } from "../../actions";
import { busyWith } from "../../busy";
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
    //: Recomputed on a move and after every action: a step laid changes both
    //: the surface and what is left of the roadbed in hand.
  }, [session, look.node?.key, look.inventory]);

  //: Laying a surface is an occupation (D-310), and a busy body has no hands
  //: for it. The button goes grey with the reason on it: a refusal collected
  //: after the click says the same thing one step too late.
  const occupied = busyWith(look);

  const shown = only ? roads.filter((path) => path.to === only) : roads;
  if (shown.length === 0) return null;
  const work_ = (edge: string, mend: boolean) =>
    act(() => session.send("road.lay", { edge, mend }));

  return (
    <div className="row roads">
      {shown.map((path) => (
        <span key={path.edge} className="note">
          {path.to}: {t(SURFACE[path.surface])}
          {paved(path.surface) && ` ${path.condition.toFixed(0)}%`}
          {path.working ? (
            ` · ${t("ui-map-road-working")}`
          ) : (
            <>
              {/* The price of the work stands on the button, not in a hover
                  hint: a disabled button with no explanation reads as a
                  breakage, and from a phone a hint is not seen at all. */}
              {path.next && path.needs != null && (
                <button
                  className="quiet"
                  onClick={() => work_(path.edge, false)}
                  disabled={busy || path.at_hand < path.needs || occupied !== null}
                  title={
                    occupied ??
                    t("ui-map-road-need", {
                      needs: path.needs.toFixed(0),
                      hand: path.at_hand.toFixed(0),
                    })
                  }
                >
                  {t(paved(path.surface) ? "ui-map-road-pave" : "ui-map-road-lay", {
                    needs: path.needs.toFixed(0),
                  })}
                </button>
              )}
              {path.mend_needs != null && (
                <button
                  className="quiet"
                  onClick={() => work_(path.edge, true)}
                  disabled={busy || path.at_hand < path.mend_needs || occupied !== null}
                  title={
                    occupied ?? t("ui-map-road-mend-need", { needs: path.mend_needs.toFixed(0) })
                  }
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

/** Whether a crew laid this: only a road or a highway has a condition to
 *  read and to mend. The wild and a trail are feet's work (D-319). */
function paved(surface: RoadWork["surface"]): boolean {
  return surface === "road" || surface === "paved";
}
