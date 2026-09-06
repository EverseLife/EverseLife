// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The walker: the dot of the body on the road (D-107, D-238).
 *
 * It moves by frames, not renders. A timer once recomputed its position
 * every half second -- on a six-second transit that is a dozen jumps
 * instead of movement. The dot moves in `requestAnimationFrame`, bypassing
 * React: the map re-renders when the map changed, not sixty times a second
 * for one dot. The leg's endpoints are asked for on every frame through
 * `where`: on the space layer the planets under the dot move while it walks.
 */

import { useEffect, useRef, type RefObject } from "react";

import type { Transit } from "../../api";
import type { Camera } from "./camera";
import { placeAt } from "./globe";
import type { Point } from "./model";

/** Where the dot is now along the leg. */
export function dotOn(
  ongoing: Transit,
  from: Point | undefined,
  to: Point | undefined,
  nowMs: number,
): Point | null {
  if (!from || !to) return null;
  const t0 = new Date(ongoing.started_at).getTime();
  const t1 = new Date(ongoing.arrives_at).getTime();
  const share = Math.min(1, Math.max(0, (nowMs - t0) / Math.max(1, t1 - t0)));
  return { x: from.x + (to.x - from.x) * share, y: from.y + (to.y - from.y) * share };
}

export function useWalker({
  ongoing,
  where,
  reprScene,
  cam,
  myRepr,
}: {
  ongoing: Transit | null;
  /** Where a node is drawn in the scene, as of now. */
  where: RefObject<(key: string) => Point | undefined>;
  reprScene: (key: string) => string | null;
  cam: Camera;
  /** Where the body stands, as the scene draws it. */
  myRepr: string | null;
}) {
  //: A group, not the circle itself: the dot is stood by a matrix, because
  //: a `cx` half a globe from the eye saturates (`globe.placeAt`).
  const walkerRef = useRef<SVGGElement | null>(null);
  //: While walking the camera follows the dot, frame by frame (D-238): the
  //: player watches themselves go, and arrival lands with nothing left to
  //: jump. A grab or a zoom hands the frame back to the hand.
  useEffect(() => {
    if (!ongoing) return;
    let raf = 0;
    const step = () => {
      const mark = walkerRef.current;
      const dot = dotOn(
        ongoing,
        where.current(reprScene(ongoing.from_key) ?? ""),
        where.current(reprScene(ongoing.to_key) ?? ""),
        Date.now(),
      );
      if (mark && dot) {
        mark.setAttribute("transform", placeAt(dot));
        //: The dot names where it is; the camera decides whether to chase it
        //: -- it does, unless the hand has taken the frame for this journey.
        cam.toDot(dot);
      }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
    //: The legs of the transit rather than `ongoing` itself: the object
    //: arrives new with every poll, and the effect would be rebuilt twice a
    //: second -- the very stutter this is here to avoid. Every field the
    //: closure reads is listed, so it never goes stale, and the camera is one
    //: object for the life of the map; the linter cannot be shown either.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    ongoing?.from_key,
    ongoing?.to_key,
    ongoing?.started_at,
    ongoing?.arrives_at,
    reprScene,
    cam,
  ]);

  //: Where the dot is drawn at this render; the frames move it from there.
  const walker = ongoing
    ? dotOn(
        ongoing,
        where.current(reprScene(ongoing.from_key) ?? ""),
        where.current(reprScene(ongoing.to_key) ?? ""),
        Date.now(),
      )
    : null;

  /**
   * Which node wears the player, if any.
   *
   * On the road the body stands in no node at all (D-107), so the node one
   * walked out of must stop wearing them -- but only where the dot on the
   * road says where they are instead. On a layer that draws neither end of
   * the leg there is no dot, and a map that says nothing at all is worse than
   * one that says where the walk began. A scout in the field (D-152) keeps
   * the mark for the same reason: they went **from** the node, they come back
   * to it, and no dot is drawn for them.
   */
  const standingAt = walker ? null : myRepr;

  return { walkerRef, walker, standingAt };
}
