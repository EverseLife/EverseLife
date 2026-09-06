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

import type { MapNode, MapStub, RecipeBook } from "../../api";
import { tilted } from "./bands";
import { STUB_M, ahead, arc, radiusUnits, turn, type Eye, type Geo } from "./globe";
import type { Link, Point } from "./model";

/** The planet's radius in map units: the vault's (`planet.radius` on
 *  `planet.terra_radius_km`, D-320), read from the book -- no number of the
 *  client's own. Null until the book has come, or for no planet. */
export function radiusOf(book: RecipeBook | null, planet: string | null): number | null {
  if (!book?.constants || !planet) return null;
  const shares = book.constants["planet.radius"] as Record<string, number> | undefined;
  const share = Number(shares?.[planet]);
  const terra = Number(book.constants["planet.terra_radius_km"]);
  if (!Number.isFinite(share) || !Number.isFinite(terra) || share <= 0 || terra <= 0) return null;
  return radiusUnits(share * terra);
}

export function useGlobe({
  book,
  planet,
  active,
  descent = 0,
}: {
  book: RecipeBook | null;
  /** Whose surface the scene shows: the planet the radius is read for. */
  planet: string | null;
  /** Whether the scene is a surface at all -- the sky and a house are flat. */
  active: boolean;
  /** How far down the approach the frame is (`bands.descentOf`): on the way
   *  down the eye is shown tilted towards the pole, and the hand turns what
   *  is shown. */
  descent?: number;
}) {
  //: Without a radius the scene is flat, as it was before the globe.
  const radius = useMemo(() => radiusOf(book, planet), [book, planet]);
  const globeScene = active && radius !== null;

  //: Where the eye stands: put on a place at every new scene, turned by the
  //: hand from there. Nowhere until a scene has a place to look at.
  const [eye, setEye] = useState<Eye | null>(null);
  const eyeRef = useRef(eye);
  eyeRef.current = eye;
  const descentRef = useRef(descent);
  descentRef.current = descent;
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
    const down = descentRef.current;
    if (down > 0) {
      //: Tilted, the sphere turns about its axis only: the hand's sideways
      //: drag is read at the latitude shown, where a degree is short, and
      //: the up-and-down is the descent's, not the hand's -- a latitude
      //: banked unseen would surface as a jump when the tilt came off.
      const shown = tilted(was, down);
      setEye({ lat: was.lat, lon: turn(shown, radius, dx, 0).lon });
      return;
    }
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

/**
 * The curves of a surface scene: an edge as the runs of its great-circle
 * arc that face the eye, and a stub into the fog as a short arc out of its
 * seen end -- where that end stands for itself in the scene, since a closed
 * city has no fog round it. Both undefined off the globe: there the edges
 * are lines.
 */
export function useArcs({
  globeScene,
  eye,
  radius,
  byKey,
  reprScene,
}: {
  globeScene: boolean;
  eye: Eye | null;
  radius: number | null;
  byKey: Record<string, MapNode>;
  reprScene: (key: string) => string | null;
}) {
  const curve = useMemo(() => {
    if (!globeScene || !eye || !radius) return undefined;
    return (edge: Link): Point[] | null => {
      const a = byKey[edge.a]?.place;
      const b = byKey[edge.b]?.place;
      if (!a || !b || !("lat" in a) || !("lat" in b)) return null;
      return arc(eye, radius, a, b);
    };
  }, [globeScene, eye, radius, byKey]);
  const stubCurve = useMemo(() => {
    if (!globeScene || !eye || !radius) return undefined;
    return (stub: MapStub): Point[] | null => {
      const from = byKey[stub.from]?.place;
      if (!from || !("lat" in from) || reprScene(stub.from) !== stub.from) return null;
      return arc(eye, radius, from, ahead(radius, from, stub.bearing, STUB_M));
    };
  }, [globeScene, eye, radius, byKey, reprScene]);
  return { curve, stubCurve };
}
