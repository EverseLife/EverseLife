// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The course: the slider between the fastest passage and the cheapest (D-271,
 * D-341).
 *
 * One planet is one course, because a crossing goes orbit to orbit (D-245):
 * which pad the hull ends on is chosen over the planet, once it is there. What
 * is chosen **here** is the flight time -- and with it the arc the sky offers
 * for it, its delta-v and its fuel. The engine samples the curve (`ship.course`);
 * this window only lets the owner walk along it, and the chart draws the arc
 * of the point it stands on (D-289). The order sends the hours back; the
 * helm flies that point under the whole sky from there.
 */

import { useEffect, useRef, useState } from "react";
import { useEdition, useSession } from "../../actions";
import { refusalText, t } from "../../locale";
import { planetName } from "../../planets";
import { term } from "../map/orbits";
import { whole, type CourseAnswer, type PlanLine, type Sample, type Target, type Vessel } from "./model";

export function Course({
  vessel,
  target,
  busy,
  fly,
  onPlan,
}: {
  vessel: Vessel;
  /** A planet's orbit, or a hull in sight (D-289, wave 3). */
  target: Target | null;
  busy: boolean;
  /** The order: the target, the hours, and the planet a flyby bends round.
   *  Settles once the order has been answered, taken or refused. */
  fly: (to: Target, hours: number, via: string | null) => Promise<void>;
  /** The arc of the point the slider stands on, for the chart to draw. */
  onPlan: (plan: PlanLine | null) => void;
}) {
  const session = useSession();
  const edition = useEdition("ship.", "transport.");
  const [samples, setSamples] = useState<Sample[] | null>(null);
  const [reserve, setReserve] = useState(0);
  const [trouble, setTrouble] = useState<string | null>(null);
  const [why, setWhy] = useState<CourseAnswer["why"]>(null);
  const [pick, setPick] = useState<number | null>(null);
  //: Reread after this window's own order is answered (D-341): a flyby gone
  //: by the order's moment is refused, and the slider must show the sky that
  //: refused it rather than offer the same pass again. Counted on the answer,
  //: not on a clock (D-226); a taken order closes the window anyway.
  const [settled, setSettled] = useState(0);
  const held = useRef<number | null>(null);

  const planet = target !== null && "planet" in target ? target.planet : null;
  const other = target !== null && "ship" in target ? target.ship : null;
  //: Reread when the target changes and when the world says so (D-226): the
  //: sky moves too slowly for a clock, and the hull's mass changes by orders.
  useEffect(() => {
    if (planet === null && other === null) return;
    let live = true;
    setSamples(null);
    setTrouble(null);
    setWhy(null);
    setPick(null);
    void session
      .send<CourseAnswer>(
        "ship.course",
        planet !== null ? { ship: vessel.ship, planet } : { ship: vessel.ship, ship_target: other },
      )
      .then((answer) => {
        if (!live) return;
        const got: Sample[] = answer.samples ?? [];
        setSamples(got);
        setReserve(answer.reserve ?? 0);
        setWhy(answer.why ?? null);
        //: Start at the cheap end, the last point: the default the engine
        //: flies unnamed -- or, rereading after a refusal, at the hours that
        //: were chosen, if the sky still offers them.
        const again = got.findIndex((one) => one.hours === held.current);
        setPick(again >= 0 ? again : got.length > 0 ? got.length - 1 : null);
      })
      .catch((error: unknown) => {
        //: The refusal in the engine's own words, not a guess about engines:
        //: a lost socket and a bad planet are not "no arc fits".
        if (live) setTrouble(error instanceof Error ? error.message : String(error));
      });
    return () => {
      live = false;
    };
  }, [session, vessel.ship, planet, other, edition, settled]);

  //: The chart follows the thumb (D-289): the arc of the point under it, and
  //: nothing once the target is dropped.
  useEffect(() => {
    const standing = target !== null && samples !== null && pick !== null ? samples[pick] : null;
    if (standing) held.current = standing.hours;
    onPlan(standing?.trace ? { trace: standing.trace, around: standing.around ?? null } : null);
    //: And nothing once the slider is gone: a line left behind after the
    //: order would lie on top of the order's own.
    return () => onPlan(null);
  }, [onPlan, target, samples, pick]);

  if (target === null) {
    return <p className="note">{t("ui-ship-pick-planet")}</p>;
  }
  const route = planet === null ? null : vessel.routes.find((one) => one.planet === planet);
  const sighted = other === null ? null : vessel.sightings.find((one) => one.ship === other);
  if (planet !== null && !route) {
    return <p className="note">{t("ui-ship-no-route")}</p>;
  }
  if (other !== null && !sighted) {
    return <p className="note">{t("ui-ship-target-gone")}</p>;
  }
  //: Thrust closes no planet and no hull: the reachable flag is the same
  //: question for both -- can the hull tear off at all.
  const reachable = route ? route.reachable : vessel.ratio >= vessel.min_ratio;
  if (trouble !== null) {
    return <p className="reason">{t("ui-ship-course-failed", { why: trouble })}</p>;
  }
  if (samples === null) {
    return <p className="note">{t("ui-ship-course-loading")}</p>;
  }
  if (samples.length === 0 || pick === null) {
    //: Nothing to a hull comes with the engine's reason (D-289, wave 3): a
    //: hull that will be gone by the hour is not the engines' fault.
    const said = why ? refusalText("", why.code, why.args) : "";
    return <p className={said ? "reason" : "note"}>{said || t("ui-ship-no-arc-fits")}</p>;
  }
  //: The slider is every sample the server sent (D-341): fastest first, and
  //: each one cheaper than the one before.
  const cheap = samples.length - 1;
  const chosen = samples[pick];
  const needs = chosen.fuel + reserve;
  //: Warnings, not locks (D-289): the engine refuses only the departure
  //: burn it cannot pay for. Two thresholds, two different ends -- short of
  //: the crossing the hull goes adrift under way; short only of the landing
  //: it reaches orbit and stays there -- said here, before the button, in
  //: the tank's own numbers.
  const shortCross = vessel.fuel < chosen.fuel;
  const shortLand = !shortCross && vessel.fuel < needs;
  return (
    <div className="course">
      <p>
        {planet !== null && route ? (
          <>
            <span
              className="planet-dot"
              style={{ background: `var(--planet-${planet})` }}
              aria-hidden="true"
            />
            <b>{planetName(planet)}</b>
          </>
        ) : (
          <b>{t("ui-ship-course-to-ship", { name: sighted?.name ?? "" })}</b>
        )}
        {!reachable && ` · ${t("ui-ship-thrust-cut")}`}
      </p>
      {/* One price to a hull in the deep (D-289, wave 3): the approach
          profile's own, and no slider between two ends that do not exist. To
          a hull in orbit round the same planet the arcs round it are a
          slider like a planet's (D-354). */}
      {samples.length > 1 && (
      <p className="row">
        <span className="note">{t("ui-ship-end-fast", { term: term(whole(samples[0])) })}</span>
        <input
          type="range"
          min={0}
          max={cheap}
          step={1}
          value={pick}
          aria-label={t("ui-ship-slider")}
          onChange={(e) => setPick(Number(e.target.value))}
        />
        <span className="note">{t("ui-ship-end-cheap", { term: term(whole(samples[cheap])) })}</span>
      </p>
      )}
      <p>
        {t("ui-ship-arc-cost", {
          term: term(whole(chosen)),
          fuel: chosen.fuel.toFixed(0),
          dv: chosen.dv.toFixed(0),
        })}
        {/* A flyby (D-341): the price is the pass's, and the chart's line
            bends at it -- the reader is told whose pull it borrows. */}
        {chosen.via && ` · ${t("ui-ship-via", { planet: planetName(chosen.via) })}`}
        {" · "}
        {t("ui-ship-dv-line", { have: vessel.dv.toFixed(0) })}{" "}
        <button
          onClick={() =>
            void fly(target, chosen.hours, chosen.via ?? null).then(() =>
              setSettled((count) => count + 1),
            )
          }
          disabled={busy || !reachable}
          title={t(reachable ? "ui-ship-fly-hint" : "ui-ship-thrust-short")}
        >
          {t("ui-ship-fly")}
        </button>
      </p>
      {shortCross && (
        <p className="reason">
          {t("ui-ship-short-cross", { fuel: vessel.fuel.toFixed(0), need: chosen.fuel.toFixed(0) })}
        </p>
      )}
      {shortLand && (
        <p className="reason">
          {t("ui-ship-short-land", { fuel: vessel.fuel.toFixed(0), need: needs.toFixed(0) })}
        </p>
      )}
    </div>
  );
}
