// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The course: the slider between the fastest arc and the cheapest (D-271).
 *
 * One planet is one course, because a crossing goes orbit to orbit (D-245):
 * which pad the hull ends on is chosen over the planet, once it is there. What
 * is chosen **here** is the flight time -- and with it the arc the sky offers
 * for it, its delta-v and its fuel. The engine samples the curve (`ship.course`);
 * this window only lets the owner walk along it, and the chart draws the arc
 * of the point it stands on (D-289). The order sends the hours back; the
 * helm flies that point under the whole sky from there.
 */

import { useEffect, useState } from "react";
import { useEdition, useSession } from "../../actions";
import { refusalText, t } from "../../locale";
import { planetName } from "../../planets";
import { term } from "../map/orbits";
import { range, type CourseAnswer, type Sample, type Target, type Vessel } from "./model";

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
  fly: (to: Target, hours: number) => void;
  /** The arc of the point the slider stands on, for the chart to draw. */
  onPlan: (trace: [number, number][] | null) => void;
}) {
  const session = useSession();
  const edition = useEdition("ship.", "transport.");
  const [samples, setSamples] = useState<Sample[] | null>(null);
  const [reserve, setReserve] = useState(0);
  const [trouble, setTrouble] = useState<string | null>(null);
  const [why, setWhy] = useState<CourseAnswer["why"]>(null);
  const [pick, setPick] = useState<number | null>(null);

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
        //: Start at the cheap end: the default the engine flies unnamed.
        const span = range(got);
        setPick(span ? span[1] : null);
      })
      .catch((error: unknown) => {
        //: The refusal in the engine's own words, not a guess about engines:
        //: a lost socket and a bad planet are not "no arc fits".
        if (live) setTrouble(error instanceof Error ? error.message : String(error));
      });
    return () => {
      live = false;
    };
  }, [session, vessel.ship, planet, other, edition]);

  //: The chart follows the thumb (D-289): the arc of the point under it, and
  //: nothing once the target is dropped.
  useEffect(() => {
    const held = target !== null && samples !== null && pick !== null ? samples[pick] : null;
    onPlan(held?.trace ?? null);
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
  const span = range(samples);
  if (!span || pick === null) {
    //: Nothing to a hull comes with the engine's reason (D-289, wave 3): a
    //: hull that will be gone by the hour is not the engines' fault.
    const said = why ? refusalText("", why.code, why.args) : "";
    return <p className={said ? "reason" : "note"}>{said || t("ui-ship-no-arc-fits")}</p>;
  }
  const [fast, cheap] = span;
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
            <b>{planetName(planet)}</b> · <span className="note">{route.name}</span>
          </>
        ) : (
          <b>{t("ui-ship-course-to-ship", { name: sighted?.name ?? "" })}</b>
        )}
        {!reachable && ` · ${t("ui-ship-thrust-cut")}`}
      </p>
      {/* One price to a hull (D-289, wave 3): the approach profile's own,
          and no slider between two ends that do not exist. */}
      {samples.length > 1 && (
      <p className="row">
        <span className="note">{t("ui-ship-end-fast", { term: term(samples[fast].hours) })}</span>
        <input
          type="range"
          min={fast}
          max={cheap}
          step={1}
          value={pick}
          aria-label={t("ui-ship-slider")}
          onChange={(e) => setPick(Number(e.target.value))}
        />
        <span className="note">{t("ui-ship-end-cheap", { term: term(samples[cheap].hours) })}</span>
      </p>
      )}
      <p>
        {t("ui-ship-arc-cost", {
          term: term(chosen.hours),
          fuel: chosen.fuel.toFixed(0),
          dv: chosen.dv.toFixed(0),
        })}
        {" · "}
        {t("ui-ship-dv-line", { have: vessel.dv.toFixed(0) })}{" "}
        <button
          onClick={() => fly(target, chosen.hours)}
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
