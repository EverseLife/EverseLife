// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The globe's state for the map: the eye, the radius, and the hand's turn.
 *
 * The arithmetic is `globe.ts`; this is what React keeps of it. The eye is
 * state, because every node moves when it moves -- but a pointer moves far
 * more often than a frame is painted, so the hand's drags are gathered and
 * the eye is turned once per animation frame, the way the walker's dot and
 * the camera's frame stay out of React's way (`camera.ts`).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { RecipeBook } from "../../api";
import { radiusUnits, turn, type Eye, type Geo } from "./globe";

export function useGlobe({
  book,
  planet,
  active,
}: {
  book: RecipeBook | null;
  /** Whose surface the scene shows: the planet the radius is read for. */
  planet: string | null;
  /** Whether the scene is a surface at all -- the sky and a house are flat. */
  active: boolean;
}) {
  //: The planet's radius is the vault's (`planet.radius` on
  //: `planet.terra_radius_km`, D-320), read from the book: no number of the
  //: client's own. Without it the scene is flat, as it was before the globe.
  const radius = useMemo(() => {
    if (!book?.constants || !planet) return null;
    const shares = book.constants["planet.radius"] as Record<string, number> | undefined;
    const share = Number(shares?.[planet]);
    const terra = Number(book.constants["planet.terra_radius_km"]);
    if (!Number.isFinite(share) || !Number.isFinite(terra) || share <= 0 || terra <= 0) return null;
    return radiusUnits(share * terra);
  }, [book, planet]);
  const globeScene = active && radius !== null;

  //: Where the eye stands: put on a place at every new scene, turned by the
  //: hand from there. Nowhere until a scene has a place to look at.
  const [eye, setEye] = useState<Eye | null>(null);
  const eyeRef = useRef(eye);
  eyeRef.current = eye;
  //: The drags since the last frame, and the frame booked to spend them.
  const pending = useRef({ dx: 0, dy: 0, raf: 0 });
  const flush = useCallback(() => {
    const held = pending.current;
    held.raf = 0;
    const was = eyeRef.current;
    if (!was || radius === null) return;
    const { dx, dy } = held;
    held.dx = 0;
    held.dy = 0;
    setEye(turn(was, radius, dx, dy));
  }, [radius]);
  const rotate = useCallback(
    (dx: number, dy: number) => {
      const held = pending.current;
      held.dx += dx;
      held.dy += dy;
      if (!held.raf) held.raf = requestAnimationFrame(flush);
    },
    [flush],
  );
  useEffect(
    () => () => {
      if (pending.current.raf) cancelAnimationFrame(pending.current.raf);
    },
    [],
  );
  /** Put the eye over a place: the origin of the frame moves there. */
  const lookAt = useCallback((place: Geo) => setEye({ lat: place.lat, lon: place.lon }), []);

  return {
    globeScene,
    radius,
    eye,
    lookAt,
    /** The hand's turn, only once there is an eye to turn: before that a
     *  drag falls through to the frame, so a loose camera is never stuck. */
    rotate: globeScene && eye ? rotate : undefined,
  };
}
