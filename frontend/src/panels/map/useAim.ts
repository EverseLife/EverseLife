// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Where the frame aims itself: at the body, on a new scene, and along a walk
 * (D-237, D-238).
 *
 * The camera follows the body -- your node is the middle of the frame -- and
 * is re-aimed when you move, when the layer changes and when another city or
 * planet is opened, and at no other moment, so a hand that panned or zoomed
 * keeps what it did until the next step. Standing on no node of this layer at
 * all -- somebody else's city -- the frame opens on its first node, because a
 * camera aimed at nothing shows nothing. The rules of following on a globe
 * are `follow.ts`; this is when they are asked. Out of `GraphMap.tsx`, which
 * is past the eight-hundred-line bar already.
 */

import { useEffect, useMemo, useRef, type RefObject } from "react";

import type { MapNode, Transit } from "../../api";
import type { Band } from "./bands";
import type { Camera } from "./camera";
import { firstOnGlobe, needsTurn } from "./follow";
import { journeyOf, sceneKey, type Point } from "./model";
import { STAR } from "./orbits";
import type { useGlobe } from "./useGlobe";

/**
 * Re-aims the frame and follows the journey; returns the turn the walker
 * asks for on a globe, where the dot is brought back to the middle by
 * turning the eye (`useWalker`), and nothing off it.
 */
export function useAim({
  cam,
  globe,
  ground,
  skyPlaces,
  visible,
  byKey,
  here,
  myRepr,
  band,
  orbiting,
  inside,
  locationBase,
  sphereShown,
  drawn,
  tethered,
  ongoing,
}: {
  cam: Camera;
  globe: ReturnType<typeof useGlobe>;
  /** Where the scene's nodes are laid on the layers one walks. */
  ground: Map<string, Point>;
  /** Where the sky's bodies are, as of now (`useSky`). */
  skyPlaces: RefObject<Map<string, Point>>;
  visible: MapNode[];
  byKey: Record<string, MapNode>;
  /** Where the body stands. */
  here: string;
  /** Where you stand, as the scene draws it. */
  myRepr: string | null;
  band: Band;
  orbiting: boolean;
  inside: boolean;
  /** The node whose inside is shown. */
  locationBase: string;
  /** Whose surface is shown. */
  sphereShown: string | null;
  /** Whether the map has landed at all. */
  drawn: boolean;
  /** Whether the camera is tied to the body (`GraphMap.CAMERA`). */
  tethered: boolean;
  ongoing: Transit | null;
}): ((dot: Point) => void) | undefined {
  const { globeScene } = globe;
  //: Which scene the frame was last aimed at. A different one shares no
  //: coordinates with this one -- another layer, another city, another
  //: planet -- so the frame is cut to it rather than flown across nothing.
  const shownScene = useRef<string | null>(null);

  //: Read through a ref, and deliberately not depended on: the layout is
  //: rebuilt on every push from the server, and depending on it re-aimed the
  //: frame every few seconds -- dragging back, unasked, the hand that had
  //: just panned somewhere to look. The **reasons** to re-aim are below.
  const groundRef = useRef(ground);
  groundRef.current = ground;
  const visibleRef = useRef(visible);
  visibleRef.current = visible;
  //: Every node the map knows, not only the drawn ones: a body inside a
  //: closed city is not itself in the scene, and the tether has to find a
  //: place to turn to anyway.
  const byKeyRef = useRef(byKey);
  byKeyRef.current = byKey;
  const hereRef = useRef(here);
  hereRef.current = here;
  useEffect(() => {
    const laid = groundRef.current;
    const middle = orbiting
      ? (skyPlaces.current.get(myRepr ?? "") ?? STAR)
      : (laid.get(myRepr ?? "") ?? [...laid.values()][0]);
    //: On a globe the middle is found by turning the eye (below), not in
    //: the layout: a scene with nothing laid -- the body on the far side of
    //: the ball, nothing drawn -- still has a place to turn to (owner,
    //: 2026-09-13: tied back on with the body round the far side, the
    //: camera froze; the empty layout sent the tether away right here).
    if (!middle && !globeScene) return;
    const scene = sceneKey(band, inside ? locationBase : null, sphereShown);
    const cut = shownScene.current !== scene;
    shownScene.current = scene;
    //: On a globe the eye goes to where the body stands whenever the scene is
    //: new: the origin of the frame is the eye, so the middle is the origin.
    if (cut && globeScene) {
      //: Where the body stands, if this scene shows it; somebody else's city
      //: opened from outside has no node of yours, and then the scene's first
      //: place -- a window with no centre would be an empty field.
      const shown = visibleRef.current;
      const stand = firstOnGlobe([
        shown.find((node) => node.key === myRepr)?.place,
        byKeyRef.current[myRepr ?? ""]?.place,
        byKeyRef.current[hereRef.current]?.place,
        shown.find((node) => node.place && "lat" in node.place)?.place,
      ]);
      if (stand) {
        globe.lookAt(stand);
        cam.cut({ x: 0, y: 0 });
        return;
      }
    }
    //: A new scene is moved to **whatever else is going on**, walking or not:
    //: its coordinates are not the old ones, and a frame left in them shows
    //: an empty field. The walker cannot bring it back either -- on somebody
    //: else's city the legs of the transit are not drawn at all.
    if (cut) {
      if (middle) cam.cut(middle);
      return;
    }
    //: A loose camera moves for nothing but a new scene -- not for a step, not
    //: for a walk. That is what loose means, and the tether coming back is
    //: itself a reason to re-aim: the frame glides home the moment it is tied.
    if (!tethered) return;
    //: Within one scene, while the walk is being followed, the frame already
    //: has its aim: the dot.
    if (cam.following()) return;
    //: On the globe the body comes to the middle by the globe turning under
    //: the eye, and the frame stays on the eye: a frame slid to the body's
    //: projection would leave the planet off centre, and the hand -- which
    //: turns, and does not slide -- could never bring it back.
    if (globeScene) {
      //: Where to turn to, in the order the question is really asked: the
      //: node the scene shows for the body, then that node wherever the map
      //: has it, then the body's own node. The first alone left the globe
      //: still whenever the body's representative was not among the drawn --
      //: a closed city too small to draw, a scene that shows the city and not
      //: the flat -- and the tether then slid a frame whose origin is the
      //: eye, which is no movement at all (owner, 2026-09-08: «планета не
      //: всегда прокручивается до игрока»).
      const stand = firstOnGlobe([
        visibleRef.current.find((node) => node.key === myRepr)?.place,
        byKeyRef.current[myRepr ?? ""]?.place,
        byKeyRef.current[hereRef.current]?.place,
      ]);
      if (stand) {
        globe.aimAt(stand);
        cam.aimAt({ x: 0, y: 0 });
        return;
      }
    }
    if (middle) cam.aimAt(middle);
    //: Every reason the frame may move by itself: you moved, the scene
    //: changed, the tether was tied back on, or the map has just landed and
    //: there is at last a place to aim at. A push from the server is not one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [myRepr, band, orbiting, locationBase, sphereShown, drawn, tethered]);

  /**
   * A journey: the frame follows the dot while it lasts (D-238).
   *
   * Told to the camera as one journey, not deduced from the legs: a walk of
   * five nodes remounts the walker's loop five times, and a hand that took
   * the frame on the first leg must keep it to the last.
   *
   * A loose camera follows nothing: the walk goes on without it, and the dot
   * leaves the frame if that is where the road goes.
   */
  const journey = journeyOf(ongoing);
  useEffect(() => {
    //: Letting the tether go stops the frame **where it is**: `follow(false)`
    //: alone leaves a chase already booked to play out, and the map would
    //: coast for another half second after the very click that said stop.
    if (!tethered) return cam.takeFrame();
    cam.follow(journey !== null);
  }, [journey, cam, tethered]);

  //: Followed on the globe, the dot is kept in the middle by turning the
  //: eye -- only once it has strayed half a pixel, so a walk seen from afar
  //: does not redraw the whole ground at every frame for nothing.
  //:
  //: **One correction per eye.** The walker's loop runs every frame and reads
  //: the dot through `where`, which projects with the eye of the last
  //: **render**; a turn asked for is a `setEye`, and React lands it a frame or
  //: two later. Until it does, every frame sees the same stray and asks for
  //: the same correction again -- and `rotate` sums what it is given, so the
  //: eye overshot by as many frames as the render took and came back the next
  //: time round. That beat was the shake the owner saw while walking
  //: (2026-09-08). Remembering which eye was corrected for makes the second
  //: ask a no-op without slowing the first.
  const rotate = globe.rotate;
  const eyeShown = useRef(globe.eye);
  eyeShown.current = globe.eye;
  const corrected = useRef<typeof globe.eye>(null);
  return useMemo(
    () =>
      globeScene && rotate
        ? (dot: Point) => {
            if (!cam.following()) return;
            const eye = eyeShown.current;
            if (
              !needsTurn({
                dot,
                scale: cam.frame().scale,
                eye,
                corrected: corrected.current,
              })
            ) {
              return;
            }
            corrected.current = eye;
            rotate(-dot.x, -dot.y);
          }
        : undefined,
    [globeScene, rotate, cam],
  );
}
