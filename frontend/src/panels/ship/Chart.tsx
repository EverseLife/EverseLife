// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The bridge's own chart: the sky as **this ship** sees it (D-240).
 *
 * The console used to open the world map on its space layer. That map is a map
 * of places: it draws everything anybody can see and answers "what is where".
 * A bridge asks a different question -- "where can I go, how long does it take
 * and what does it cost" -- and the answer is different for every hull, because
 * hours and fuel come from this ship's thrust against this ship's mass.
 *
 * So the chart draws these things and nothing else:
 *
 * * the star and the orbits, because a passage is planned against them: the
 *   planets close and part, and half a day of waiting is worth four times the
 *   fuel (D-037);
 * * every planet at the place the clock puts it now -- the same arithmetic the
 *   world map does, over the same epoch;
 * * a **corridor** from this hull to every destination it may aim at, labelled
 *   with the hours and the fuel of **this** ship. Unreachable ones stay drawn
 *   and stay grey: what one cannot do today is exactly what one plans for;
 * * the hull itself, where the sky has it (D-289): at its planet, along the
 *   line its order flies, or wherever inertia carried it;
 * * the line ahead: the order's arc under way, the coast inertia draws for a
 *   hull whose engines are silent, and -- while the slider is held -- the arc
 *   of the point under the thumb.
 *
 * The course is set on it and nowhere else: a click picks the destination, and
 * the passage is ordered from the panel beneath. The list of routes the console
 * used to carry is gone -- a list is what one reads when the map cannot say it.
 */

import { useEffect, useMemo, useState } from "react";
import type { MapNode } from "../../api";
import { t } from "../../locale";
import { planetName } from "../../planets";
import { along, term } from "../map/orbits";
import type { Route, Vessel } from "./model";

/** The chart's own frame. Not the map's: this one is a panel, not a scene. */
const W = 640;
const H = 380;
const STAR = { x: W / 2, y: H / 2 };
const MARGIN = 46;
const TURN = Math.PI * 2;
const MS_PER_DAY = 86_400_000;
/** How far off its planet the hull is drawn: clear of the dot, still at it. */
const BERTH = 16;
/** And how far when it is actually in orbit: outside the ring drawn round the
 *  planet, so "на земле" and "на орбите" are told apart at a glance (D-245). */
const ORBIT = 26;
/**
 * How often the sky is redrawn. An orbit moves half a degree an hour, so a
 * minute is already generous -- this is a clock hand over numbers the server
 * has already given, never a poll (D-226).
 */
const TICK_MS = 60_000;

type Sphere = { key: string; name: string; planet: string; x: number; y: number; r: number };
type Point = { x: number; y: number };

/** Where a planet stands `day` days after the epoch. The world map's arithmetic. */
function place(orbit: { radius: number; period_days: number; phase: number }, day: number, fit: number) {
  const angle = orbit.phase + (TURN * day) / orbit.period_days;
  return {
    x: STAR.x + orbit.radius * fit * Math.cos(angle),
    y: STAR.y + orbit.radius * fit * Math.sin(angle),
    r: orbit.radius * fit,
  };
}

/** A line the server drew, in map units, as the chart's points. */
function drawn(trace: [number, number][], fit: number): string {
  return trace.map(([x, y]) => `${STAR.x + x * fit},${STAR.y + y * fit}`).join(" ");
}

export function Chart({
  vessel,
  planets,
  epoch,
  chosen,
  onChoose,
  plan,
}: {
  vessel: Vessel;
  /** The spheres, as the sky read gave them. Only those with an orbit are drawn. */
  planets: MapNode[];
  epoch: string | null;
  /** The planet the course is set for, if any. */
  chosen: string | null;
  onChoose: (planet: string | null) => void;
  /** The arc of the point the slider stands on, while it stands there (D-289). */
  plan: [number, number][] | null;
}) {
  //: The sky turns while the console is open, and it turns slowly. Not a data
  //: timer (D-226) -- nothing is asked of the server here; this is a clock hand
  //: over numbers the server already gave, the same one `useSky` winds on the
  //: world map.
  const [, setFrame] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setFrame((n) => n + 1), TICK_MS);
    return () => clearInterval(timer);
  }, []);

  const orbiting = useMemo(() => planets.filter((node) => node.orbit), [planets]);
  const reach = Math.max(0, ...orbiting.map((node) => node.orbit?.radius ?? 0));
  const fit = reach > 0 ? (H / 2 - MARGIN) / reach : 1;
  const day = (Date.now() - (epoch ? new Date(epoch).getTime() : Date.now())) / MS_PER_DAY;

  const spheres: Sphere[] = orbiting.map((node) => ({
    key: node.key,
    name: node.name,
    planet: node.planet,
    ...place(node.orbit!, day, fit),
  }));
  const by = new Map(spheres.map((one) => [one.planet, one]));

  //: Where the hull is. On a pad -- beside its planet's dot; in orbit -- out
  //: on a ring of its own, which is the whole visual point of the orbital step
  //: (D-245); in the sky -- where the sky has it (D-289): the state the server
  //: read, or along the order's line at the share of the time gone when the
  //: state is not there to read.
  const home = by.get(vessel.planet);
  const goal = vessel.flight?.planet ? by.get(vessel.flight.planet) : undefined;
  const hull: Point | null = (() => {
    if (vessel.stage !== "orbit" && vessel.sky) {
      return { x: STAR.x + vessel.sky.x * fit, y: STAR.y + vessel.sky.y * fit };
    }
    if (!home) return null;
    if (!vessel.flight || !goal) {
      const off = vessel.stage === "orbit" ? ORBIT : BERTH;
      return { x: home.x + off, y: home.y - off };
    }
    const t0 = new Date(vessel.flight.started_at).getTime();
    const t1 = new Date(vessel.flight.arrives_at).getTime();
    const share = Math.min(1, Math.max(0, (Date.now() - t0) / Math.max(1, t1 - t0)));
    //: Along the arc the sky gave the passage (D-271), where there is one; a
    //: climb or a descent has none and is drawn straight beside the planet.
    const arc = vessel.flight.arc;
    if (arc && arc.length >= 2) {
      const point = along(arc, share);
      return { x: STAR.x + point[0] * fit, y: STAR.y + point[1] * fit };
    }
    return { x: home.x + (goal.x - home.x) * share, y: home.y + (goal.y - home.y) * share };
  })();
  //: What the corridors start from: the planet under the hull, or, adrift,
  //: the hull itself -- a course is laid from wherever inertia left it.
  const origin: Point | undefined = vessel.stage === "adrift" && hull ? hull : home;

  /** One line per destination planet: the row carries both ends of the slider. */
  const corridors = useMemo(() => {
    const best = new Map<string, Route>();
    for (const route of vessel.routes) {
      if (!best.has(route.planet)) best.set(route.planet, route);
    }
    return [...best.values()];
  }, [vessel.routes]);

  //: The lines ahead (D-289). The coast inertia draws is shown whenever the
  //: hull is in the sky and not on its circle: under way it is what happens
  //: if the engines fall silent now, adrift it is the whole of the future.
  const inertia =
    vessel.sky?.inertia && vessel.stage !== "orbit" && vessel.sky.inertia.trace.length >= 2
      ? vessel.sky.inertia.trace
      : null;
  const order =
    vessel.stage === "flight" && vessel.flight?.arc && vessel.flight.arc.length >= 2
      ? vessel.flight.arc
      : null;

  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={t("ui-ship-chart")}>
      {/* The rings: what the planets run along, and what makes a window a
          window. Drawn under everything, in the faintest ink there is. */}
      {spheres.map((one) => (
        <circle key={`ring:${one.key}`} className="chart-orbit" cx={STAR.x} cy={STAR.y} r={one.r} />
      ))}
      <circle className="chart-star" cx={STAR.x} cy={STAR.y} r={7} />

      {/* The corridors of this hull: where it may go, and what that costs it
          right now. The label is the whole point of the chart. */}
      {origin &&
        corridors.map((route) => {
          const there = by.get(route.planet);
          if (!there) return null;
          const mid = { x: (origin.x + there.x) / 2, y: (origin.y + there.y) / 2 };
          const picked = chosen === route.planet;
          return (
            <g
              key={`way:${route.planet}`}
              className={`chart-way${route.reachable ? "" : " off"}${picked ? " picked" : ""}`}
              onClick={() => onChoose(picked ? null : route.planet)}
            >
              <line x1={origin.x} y1={origin.y} x2={there.x} y2={there.y} />
              <text x={mid.x} y={mid.y - 6} textAnchor="middle">
                {route.cheap == null
                  ? "—"
                  : t("ui-ship-chart-cheap", {
                      term: term(route.cheap.hours),
                      fuel: route.cheap.fuel.toFixed(0),
                    })}
              </text>
              <text x={mid.x} y={mid.y + 10} textAnchor="middle" className="chart-fuel">
                {route.fast == null
                  ? ""
                  : t("ui-ship-chart-fast", {
                      term: term(route.fast.hours),
                      fuel: route.fast.fuel.toFixed(0),
                    })}
              </text>
            </g>
          );
        })}

      {/* The lines ahead: the coast, the order, the plan under the thumb --
          in that order, so the one being chosen lies on top. */}
      {inertia && <polyline className="chart-inertia" points={drawn(inertia, fit)} />}
      {order && <polyline className="chart-course" points={drawn(order, fit)} />}
      {plan && plan.length >= 2 && <polyline className="chart-plan" points={drawn(plan, fit)} />}

      {/* The planets. The one under foot is marked, the chosen one is lit. */}
      {spheres.map((one) => {
        const mine = one.planet === vessel.planet && vessel.stage !== "adrift";
        const picked = chosen === one.planet;
        const route = corridors.find((r) => r.planet === one.planet);
        return (
          <g
            key={one.key}
            className={`chart-planet${mine ? " mine" : ""}${picked ? " picked" : ""}`}
            onClick={() => route && onChoose(picked ? null : one.planet)}
          >
            <circle
              cx={one.x}
              cy={one.y}
              r={mine ? 9 : 7}
              style={{ fill: `var(--planet-${one.planet})` }}
            />
            <text x={one.x} y={one.y - 14} textAnchor="middle">
              {planetName(one.planet)}
            </text>
          </g>
        );
      })}

      {/* The hull's own little orbit, drawn round the planet it hangs over.
          Only for the ship being commanded: other hulls are not on this chart
          at all, and this is a hint about **this** one (D-245). */}
      {home && vessel.stage === "orbit" && (
        <circle className="chart-parking" cx={home.x} cy={home.y} r={ORBIT} />
      )}

      {hull && (
        <g className={`chart-hull${vessel.stage === "adrift" ? " adrift" : ""}`}>
          <circle cx={hull.x} cy={hull.y} r={4} />
          <text x={hull.x + 8} y={hull.y + 4}>
            {vessel.name}
          </text>
        </g>
      )}
    </svg>
  );
}
