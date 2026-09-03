// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What only the space layer draws, and the one control only it has.
 *
 * The star, the rings the planets run on, the corridors between them with what
 * a passage costs on the day shown, and the arc a ship in flight is on. None of
 * it is a graph: there are no edges in the void and nobody walks there (D-037,
 * D-201), so all of it is drawn from the clock and from the engine's calendar
 * of each corridor rather than from anything the map holds (D-271).
 */

import type { MapNode, WorldMap } from "../../api";
import { Hint } from "../../Hint";
import { t } from "../../locale";
import type { LayerId, Point } from "./model";
import { STAR, forecast, term, windowOpen } from "./orbits";
import type { Sky } from "./useSky";

/**
 * Winding the sky forward: the only way to see the system turn.
 *
 * A planet stands where the clock puts it, and the clock moves too slowly to
 * watch -- an orbit passes fractions of a degree in an hour. So the motion is
 * shown by winding time rather than by waiting, and the map keeps saying which
 * moment it is showing: a map showing anything but now must say so.
 */
export function SkyClock({ sky }: { sky: Sky }) {
  const { ahead, winding, setWinding, wind } = sky;
  return (
    <div className="row sky">
      <button className="quiet" onClick={() => setWinding((on) => !on)}>
        {t(winding ? "ui-map-sky-stop" : "ui-map-sky-wind")}
      </button>
      <input
        type="range"
        min={0}
        max={sky.horizon}
        step={0.25}
        value={ahead}
        aria-label={t("ui-map-sky-slider")}
        onChange={(e) => wind(Number(e.target.value))}
      />
      <span className="note">
        {ahead < 0.05
          ? t("ui-map-sky-now-note")
          : t("ui-map-sky-ahead", { days: ahead.toFixed(1) })}
      </span>
      {ahead >= 0.05 && (
        <button className="quiet" onClick={() => wind(0)}>
          {t("ui-map-sky-now")}
        </button>
      )}
      <Hint>{t("ui-map-sky-rule")}</Hint>
    </div>
  );
}

/** The star, the orbits, the corridors and the ships in flight. */
export function SkyBackdrop({
  map,
  visible,
  at,
  repr,
  fit,
  day,
}: {
  map: WorldMap;
  visible: MapNode[];
  at: (key: string) => Point | undefined;
  repr: (key: string, layer: LayerId) => string | null;
  fit: number;
  /** Which day the sky is showing, counted from the epoch: the calendar is
   *  leafed to it. */
  day: number;
}) {
  /** The planet's own node, by planet name: corridors are keyed by planet. */
  const sphereOf = (planet: string): MapNode | undefined =>
    (map.nodes ?? []).find((node) => node.orbit && node.planet === planet);
  return (
    <>
      {/* The system behind everything: the star and the rings the planets run
          on. Hairlines only -- the map is a surface with numbers lying on top
          of it, and there the background keeps quiet (D-072). */}
      <g className="system" aria-hidden="true">
        {visible.map(
          (node) =>
            node.orbit && (
              <circle
                key={`orbit-${node.key}`}
                className="orbit"
                cx={STAR.x}
                cy={STAR.y}
                r={node.orbit.radius * fit}
              />
            ),
        )}
        <circle className="star-glow" cx={STAR.x} cy={STAR.y} r={18} />
        <circle className="star-glow" cx={STAR.x} cy={STAR.y} r={27} />
        <circle className="star" cx={STAR.x} cy={STAR.y} r={7} />
      </g>

      {/* The corridors: what the cheapest passage between two planets costs on
          the day shown. There is no edge under them -- one does not walk the
          void -- so they are drawn from the engine's calendar (D-271): the
          cheapest arc for each of the coming days, and the window is where it
          dips. This is what makes a passage something one plans: the window
          is worth waiting for, and the map says how long. */}
      {(map.routes ?? []).map((route) => {
        const one = sphereOf(route.a);
        const other = sphereOf(route.b);
        const here = one && at(one.key);
        const there = other && at(other.key);
        if (!one || !other || !here || !there) return null;
        const today = forecast(route, day);
        const open = windowOpen(route, day);
        const midX = (here.x + there.x) / 2;
        const midY = (here.y + there.y) / 2;
        const away = Math.hypot(midX - STAR.x, midY - STAR.y) || 1;
        return (
          <g key={`corridor|${route.a}|${route.b}`} className="corridor">
            <line x1={here.x} y1={here.y} x2={there.x} y2={there.y} />
            <text
              x={midX + ((midX - STAR.x) / away) * 14}
              y={midY + ((midY - STAR.y) / away) * 14}
              className={`passage${open ? " open" : ""}`}
            >
              {today ? term(today.hours) : "—"}
            </text>
          </g>
        );
      })}

      {/* The line a ship is on. There is no edge under it -- undocking took the
          only one away (D-201) -- so it is drawn from the passage itself: the
          arc the sky gave it (D-271), or, on a climb and a descent, the
          straight step between the two ends. */}
      {visible.map((node) => {
        if (!node.flight) return null;
        const arc = node.flight.arc;
        if (arc && arc.length >= 2) {
          const points = arc.map(
            ([x, y]) => `${STAR.x + x * fit},${STAR.y + y * fit}`,
          );
          //: Bound nowhere, the line is a drifter's coast (D-289): drawn in
          //: the ink of a warning, because that is what it is.
          const drift = node.flight.to === null;
          return (
            <polyline
              key={`route|${node.key}`}
              className={`route${drift ? " drift" : ""}`}
              points={points.join(" ")}
            />
          );
        }
        const from = at(node.parent ?? "");
        //: A hull as the target (D-289, wave 3) names no node: no line to draw.
        const to = node.flight.to === null ? null : at(repr(node.flight.to, "space") ?? "");
        if (!from || !to) return null;
        return (
          <line
            key={`route|${node.key}`}
            className="route"
            x1={from.x}
            y1={from.y}
            x2={to.x}
            y2={to.y}
          />
        );
      })}
    </>
  );
}
