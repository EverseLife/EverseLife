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
 * So the chart draws four things and nothing else:
 *
 * * the star and the orbits, because a passage is planned against them: the
 *   planets close and part, and half a day of waiting is worth four times the
 *   fuel (D-037);
 * * every planet at the place the clock puts it now -- the same arithmetic the
 *   world map does, over the same epoch;
 * * a **corridor** from this hull to every destination it may aim at, labelled
 *   with the hours and the fuel of **this** ship. Unreachable ones stay drawn
 *   and stay grey: what one cannot do today is exactly what one plans for;
 * * the hull itself, at its planet or along the corridor it is flying.
 *
 * The course is set on it and nowhere else: a click picks the destination, and
 * the passage is ordered from the panel beneath. The list of routes the console
 * used to carry is gone -- a list is what one reads when the map cannot say it.
 */

import { useEffect, useMemo, useState } from "react";
import type { MapNode } from "../../api";
import { planetName } from "../../planets";
import { term } from "../map/orbits";
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
/**
 * How often the sky is redrawn. An orbit moves half a degree an hour, so a
 * minute is already generous -- this is a clock hand over numbers the server
 * has already given, never a poll (D-226).
 */
const TICK_MS = 60_000;

type Sphere = { key: string; name: string; planet: string; x: number; y: number; r: number };

/** Where a planet stands `day` days after the epoch. The world map's arithmetic. */
function place(orbit: { radius: number; period_days: number; phase: number }, day: number, fit: number) {
  const angle = orbit.phase + (TURN * day) / orbit.period_days;
  return {
    x: STAR.x + orbit.radius * fit * Math.cos(angle),
    y: STAR.y + orbit.radius * fit * Math.sin(angle),
    r: orbit.radius * fit,
  };
}

export function Chart({
  vessel,
  planets,
  epoch,
  chosen,
  onChoose,
}: {
  vessel: Vessel;
  /** The spheres, as the sky read gave them. Only those with an orbit are drawn. */
  planets: MapNode[];
  epoch: string | null;
  /** The planet the course is set for, if any. */
  chosen: string | null;
  onChoose: (planet: string | null) => void;
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

  //: Where the hull is. Docked or merely undocked -- at its own planet; under
  //: way -- along the corridor, at the share of it the clock has covered. The
  //: same share the world map draws a passage by, off the same two moments.
  const home = by.get(vessel.planet);
  const goal = vessel.flight?.planet ? by.get(vessel.flight.planet) : undefined;
  const hull = (() => {
    if (!home) return null;
    if (!vessel.flight || !goal) return { x: home.x + BERTH, y: home.y - BERTH };
    const t0 = new Date(vessel.flight.started_at).getTime();
    const t1 = new Date(vessel.flight.arrives_at).getTime();
    const share = Math.min(1, Math.max(0, (Date.now() - t0) / Math.max(1, t1 - t0)));
    return { x: home.x + (goal.x - home.x) * share, y: home.y + (goal.y - home.y) * share };
  })();

  /** One line per destination planet: the cheapest row of it carries the price. */
  const corridors = useMemo(() => {
    const best = new Map<string, Route>();
    for (const route of vessel.routes) {
      const known = best.get(route.planet);
      if (!known || (route.hours ?? Infinity) < (known.hours ?? Infinity)) {
        best.set(route.planet, route);
      }
    }
    return [...best.values()];
  }, [vessel.routes]);

  return (
    <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="карта рейса">
      {/* The rings: what the planets run along, and what makes a window a
          window. Drawn under everything, in the faintest ink there is. */}
      {spheres.map((one) => (
        <circle key={`ring:${one.key}`} className="chart-orbit" cx={STAR.x} cy={STAR.y} r={one.r} />
      ))}
      <circle className="chart-star" cx={STAR.x} cy={STAR.y} r={7} />

      {/* The corridors of this hull: where it may go, and what that costs it
          right now. The label is the whole point of the chart. */}
      {home &&
        corridors.map((route) => {
          const there = by.get(route.planet);
          if (!there) return null;
          const mid = { x: (home.x + there.x) / 2, y: (home.y + there.y) / 2 };
          const picked = chosen === route.planet;
          return (
            <g
              key={`way:${route.planet}`}
              className={`chart-way${route.reachable ? "" : " off"}${picked ? " picked" : ""}`}
              onClick={() => onChoose(picked ? null : route.planet)}
            >
              <line x1={home.x} y1={home.y} x2={there.x} y2={there.y} />
              <text x={mid.x} y={mid.y - 6} textAnchor="middle">
                {route.hours == null ? "—" : term(route.hours)}
              </text>
              <text x={mid.x} y={mid.y + 10} textAnchor="middle" className="chart-fuel">
                {route.fuel == null ? "" : `${route.fuel.toFixed(0)} топлива`}
              </text>
            </g>
          );
        })}

      {/* The planets. The one under foot is marked, the chosen one is lit. */}
      {spheres.map((one) => {
        const mine = one.planet === vessel.planet;
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

      {hull && (
        <g className="chart-hull">
          <circle cx={hull.x} cy={hull.y} r={4} />
          <text x={hull.x + 8} y={hull.y + 4}>
            {vessel.name}
          </text>
        </g>
      )}
    </svg>
  );
}
