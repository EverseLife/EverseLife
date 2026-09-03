// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The hull under way and the hull adrift (D-289): the two cards the console
 * shows when there is no pier under the hull.
 *
 * A passage is a term like any other and wears the bar every term wears; a
 * drift is a verdict and an hour -- and, once another hull has come to rest
 * beside this one (wave 3), the hold, the consents and the gangway.
 */

import { Deadline } from "../../Deadline";
import { t } from "../../locale";
import { planetName } from "../../planets";
import type { Vessel } from "./model";

/**
 * A passage under way: where it goes, how far it has got, and when it ends.
 *
 * The bar is the one every term in this world wears (D-238). It matters more
 * here than anywhere: a hull in flight takes no orders at all, so the only
 * thing the console can honestly offer is the answer to "how long".
 */
export function Passage({
  v,
  busy,
  deaf,
  recall,
}: {
  v: Vessel;
  busy: boolean;
  /** No console aboard: the hull hears nothing from the ground (D-242). */
  deaf: boolean;
  recall: () => void;
}) {
  if (!v.flight) return null;
  //: What is left of the plan against the tanks (D-289): after the departure
  //: burn the tanks hold less than the whole plan by definition, so the
  //: number that matters is the rest of it.
  const short = v.course !== null && v.dv < v.course.left;
  return (
    <div className="doing">
      <span className="doing-what">
        {t("ui-ship-flight", { back: String(Boolean(v.flight.back)), name: v.flight.name })}
        {v.flight.planet && ` · ${planetName(v.flight.planet)}`}
        {v.course &&
          ` · ${t("ui-ship-course-dv", { need: v.course.left.toFixed(0), have: v.dv.toFixed(0) })}`}
      </span>
      {short && <span className="reason">{t("ui-ship-course-short")}</span>}
      <Deadline
        until={v.flight.arrives_at}
        since={v.flight.started_at}
        label={t("ui-ship-flight-label")}
      />
      <span className="doing-aside note">
        {t("ui-ship-flight-autopilot")}
        {!v.flight.back && ` ${t("ui-ship-may-turn")}`}
      </span>
      {/* The helm may still go over (D-242): the way back is as long as the way
          out has been, and costs its own fuel. Named with the pier it aims at,
          because "cancel" alone would not say where the hull ends up. */}
      {/* Already going back: there is nothing left to turn (D-242). */}
      {!v.flight.back && (
        <button className="quiet" onClick={recall} disabled={busy || deaf || !v.left}>
          {t("ui-ship-recall", { known: String(Boolean(v.left)), port: v.left ?? "" })}
        </button>
      )}
      {!v.flight.back && !v.left && (
        <span className="note">{t("ui-ship-no-origin")}</span>
      )}
    </div>
  );
}

/**
 * A hull adrift (D-289): the engines are silent, inertia carries it, and the
 * console says where to and by when -- the verdict a rescue is timed by.
 */
export function Drift({
  v,
  busy,
  dock,
  undock,
}: {
  v: Vessel;
  busy: boolean;
  dock: (other: string) => void;
  undock: () => void;
}) {
  if (!v.sky) return null;
  const fate = v.sky.inertia;
  const body =
    !fate || fate.body === null
      ? ""
      : fate.body === "star"
        ? t("ui-ship-star")
        : planetName(fate.body);
  return (
    <div className="doing">
      <span className="doing-what">{t("ui-ship-fate-label")}</span>
      {/* The verdict is the tick's, and the first tick since the drift may
          still be to come: then the heading stands alone. */}
      {fate && (
        <span className="doing-aside note">
          {fate.kind === "crash"
            ? t("ui-ship-fate-crash", { body })
            : fate.kind === "escape"
              ? t("ui-ship-fate-escape")
              : t("ui-ship-fate-stable")}
        </span>
      )}
      {fate && fate.kind !== "stable" && (
        <Deadline until={fate.at} since={v.sky.at} label={t("ui-ship-fate-label")} />
      )}
      {/* Two hulls that met (D-289, wave 3): flying as one, and, with both
          commanders' consent, joined by a gangway the crew walk across with
          what they carry -- a canister of fuel first of all. */}
      {v.held && (
        <span className="doing-aside">
          {t("ui-ship-held", { name: v.held.name })}
          {v.docked_to_ship
            ? ` · ${t("ui-ship-docked-ship", { name: v.held.name })}`
            : v.dock.asked
              ? ` · ${t("ui-ship-dock-asked")}`
              : v.dock.wanted
                ? ` · ${t("ui-ship-dock-wanted")}`
                : ""}
        </span>
      )}
      {v.held && v.yours && !v.docked_to_ship && !v.dock.asked && (
        <button className="quiet" onClick={() => dock(v.held!.ship)} disabled={busy}>
          {t(v.dock.wanted ? "ui-ship-dock-agree" : "ui-ship-dock")}
        </button>
      )}
      {v.held && v.yours && !v.docked_to_ship && !v.dock.asked && (
        <span className="note">{t("ui-ship-dock-hint")}</span>
      )}
      {v.docked_to_ship && v.yours && (
        <button className="quiet" onClick={undock} disabled={busy}>
          {t("ui-ship-undock")}
        </button>
      )}
    </div>
  );
}
