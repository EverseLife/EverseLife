// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The sky, kept turning: where every planet and every hull is right now.
 *
 * The one layer that is computed rather than laid out (D-037, D-237). A planet
 * stands where the clock puts it, so nothing here is stored and nothing is
 * settled: the places are recomputed once a second -- an orbit does not need
 * sixty frames to move half a degree an hour -- and by frames only while the
 * sky is being wound forward, when it really does move.
 *
 * Winding is the only honest way to show the system turning: waiting shows
 * nothing, and the map says at every moment which moment it is showing.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { MapNode } from "../../api";
import type { Point } from "./model";
import {
  BERTH,
  FORECAST_SPEED,
  MARGIN,
  MS_PER_DAY,
  STAR,
  TURN,
  along,
  mooring,
} from "./orbits";
import { H } from "./model";

export type Sky = {
  /** Days since the epoch the sky is showing, winding included. */
  day: number;
  /** Where each planet and hull is, by key. A ref: this is redrawing, not state. */
  places: React.RefObject<Map<string, Point>>;
  /** How the vault's radii are squeezed into the frame. */
  fit: number;
  /** How many days ahead the sky is wound. Zero is now. */
  ahead: number;
  /** How far it may be wound: the corridors' calendar. */
  horizon: number;
  winding: boolean;
  setWinding: (on: boolean | ((was: boolean) => boolean)) => void;
  /** Wind to a given day ahead: the slider and the "now" button. */
  wind: (day: number) => void;
};

export function useSky({
  visible,
  epoch,
  orbiting,
  spaceRepr,
  horizon,
}: {
  visible: MapNode[];
  epoch: string | null;
  orbiting: boolean;
  /** How far ahead the sky may be wound, days: the calendar's length. */
  horizon: number;
  /** The node's delegate on the space layer: a ship in flight is bound for one. */
  spaceRepr: (key: string) => string | null;
}): Sky {
  const places = useRef<Map<string, Point>>(new Map());
  const [, setFrame] = useState(0);
  const [ahead, setAhead] = useState(0);
  const aheadRef = useRef(0);
  const [winding, setWinding] = useState(false);

  //: Radii from the vault are proportions, not pixels: the outermost ring is
  //: fitted into the frame, and every other one shrinks with it. Otherwise the
  //: farthest planet -- the one a player most wants to look at -- goes over the
  //: edge, and a map that hides a planet is worse than one drawn small.
  const reach = useMemo(
    () => Math.max(0, ...visible.map((node) => node.orbit?.radius ?? 0)),
    [visible],
  );
  const fit = reach > 0 ? Math.min(1, (H / 2 - MARGIN) / reach) : 1;

  /** Put every planet where the clock says it is, and every ship where it is. */
  const place = useRef<() => void>(() => {});
  place.current = () => {
    const start = epoch ? new Date(epoch).getTime() : Date.now();
    const day = (Date.now() - start) / MS_PER_DAY + aheadRef.current;
    const put = (key: string, x: number, y: number) =>
      places.current.set(key, { x, y });
    for (const node of visible) {
      if (!node.orbit) continue;
      const angle = node.orbit.phase + (TURN * day) / node.orbit.period_days;
      put(
        node.key,
        STAR.x + node.orbit.radius * fit * Math.cos(angle),
        STAR.y + node.orbit.radius * fit * Math.sin(angle),
      );
    }
    //: Ships go after the planets, because both of a ship's ends are planets:
    //: it either stands at one or is somewhere between two.
    for (const node of visible) {
      if (!node.aboard) continue;
      const berth = node.parent ? places.current.get(node.parent) : undefined;
      if (node.flight) {
        const t0 = new Date(node.flight.started_at).getTime();
        const t1 = new Date(node.flight.arrives_at).getTime();
        //: The passage is wound by the same clock as the sky: winding a day
        //: forward and leaving the ship where it was would draw a moment that
        //: never happens.
        const at_ = Date.now() + aheadRef.current * MS_PER_DAY;
        const share = Math.min(
          1,
          Math.max(0, (at_ - t0) / Math.max(1, t1 - t0)),
        );
        //: Along the line the sky gave (D-271, D-289) where there is one --
        //: an order's arc, or a drifter's coast, which is bound nowhere; a
        //: climb or a descent has none and goes straight between its ends.
        const arc = node.flight.arc;
        if (arc && arc.length >= 2) {
          const point = along(arc, share);
          put(node.key, STAR.x + point[0] * fit, STAR.y + point[1] * fit);
          continue;
        }
        const goal = places.current.get(spaceRepr(node.flight.to) ?? "");
        if (!berth || !goal) continue;
        put(
          node.key,
          berth.x + (goal.x - berth.x) * share,
          berth.y + (goal.y - berth.y) * share,
        );
      } else if (berth) {
        //: Docked, a ship stands **beside** its planet rather than on it: on it
        //: the planet would swallow the hull. The bearing is spun off the key,
        //: so two ships at one port do not sit in the same spot.
        const bearing = mooring(node.key);
        put(
          node.key,
          berth.x + BERTH * Math.cos(bearing),
          berth.y + BERTH * Math.sin(bearing),
        );
      }
    }
  };

  useEffect(() => {
    if (!orbiting) return;
    const draw = () => {
      place.current();
      setFrame((f) => f + 1);
    };
    draw();
    if (!winding) {
      const beat = window.setInterval(draw, 1000);
      return () => window.clearInterval(beat);
    }
    let raf = 0;
    let last = performance.now();
    const step = (now: number) => {
      aheadRef.current =
        (aheadRef.current + ((now - last) / 1000) * FORECAST_SPEED) % horizon;
      last = now;
      setAhead(aheadRef.current);
      draw();
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [orbiting, winding, visible, epoch, horizon]);

  const wind = (day: number) => {
    setWinding(false);
    aheadRef.current = day;
    setAhead(day);
    place.current();
    setFrame((f) => f + 1);
  };

  //: Which day the sky shows, winding included: the corridor calendar is
  //: leafed to it (D-271).
  const start = epoch ? new Date(epoch).getTime() : Date.now();
  const day = (Date.now() - start) / MS_PER_DAY + ahead;
  return { places, fit, ahead, day, horizon, winding, setWinding, wind };
}
