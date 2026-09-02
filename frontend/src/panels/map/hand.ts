// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What the hand may do to the frame: pan, zoom, and nothing else (D-238).
 *
 * Zoom by three means: the wheel, the two loupe buttons of the switcher, and
 * a pinch of two fingers -- a phone has no wheel, and until the pinch the
 * map on it stood at one height for ever.
 *
 * The camera owns the frame; this owns the arithmetic between a pointer and
 * the world, and the one rule about who is allowed to move what. Tethered, the
 * hand may only change how much is seen -- panning does not begin at all,
 * because a pan the camera undoes on the next step is worse than no pan: the
 * map would be fighting the hand. Loose, the hand takes the frame from every
 * autopilot and keeps it.
 *
 * Nodes are never dragged either way: the map is the same map for everybody
 * (D-237), and a rearranged one would be the single exception.
 */

import { useEffect, useRef, type PointerEvent, type RefObject, type WheelEvent } from "react";

import type { Camera } from "./camera";
import { H, W, type Point } from "./model";

/** How far the pointer travels before a press becomes a pan, in pixels. */
const SLOP = 4;

/** What the map is drawn on, as much of it as the arithmetic needs. */
type Box = { left: number; top: number; width: number; height: number };

/**
 * The scale between an elastic svg's pixels and the field it draws, and the
 * margins around the picture.
 *
 * An svg's viewBox keeps proportions (`meet`), so unless the element's ratio
 * and the viewBox's happen to agree there is empty room at two of the edges --
 * letterboxing. Without counting it a click lands beside the thing it was
 * aimed at, and a zoom "about the pointer" slides out from under it.
 *
 * Shared: the world map has a frame given as a scale, the ship's floor plan one
 * given as a width and a height (`panels/ship/Plan`). One formula, and it is
 * this one -- two copies of it would drift the day one of them was fixed.
 */
export function lensFor(box: Box, worldW: number, worldH: number) {
  const k = Math.min(box.width / worldW, box.height / worldH);
  return {
    k,
    offX: (box.width - worldW * k) / 2,
    offY: (box.height - worldH * k) / 2,
  };
}

/** The same lens for the world map, whose frame is a scale over `W`×`H`. */
export function lensOn(box: Box, scale: number) {
  return lensFor(box, W / scale, H / scale);
}

/** Where a point on the screen is in the world. */
export function worldAt(
  box: Box,
  frame: { x: number; y: number; scale: number },
  at: { clientX: number; clientY: number },
) {
  const m = lensOn(box, frame.scale);
  return {
    x: frame.x + (at.clientX - box.left - m.offX) / m.k,
    y: frame.y + (at.clientY - box.top - m.offY) / m.k,
  };
}

/** The zoom the wheel gives per notch, and how far in and out it may go. */
const NOTCH = 1.15;
const NEAREST = 4;
const FURTHEST = 0.4;

/** A scale kept within what the map may show. */
export function clampScale(scale: number): number {
  return Math.min(NEAREST, Math.max(FURTHEST, scale));
}

/**
 * The scale a pinch asks for: the fingers' spread now against their spread
 * when the pinch began, applied to the scale it began at. Measured from the
 * start rather than from the last move, so that a hundred rounded steps do
 * not drift the picture away from the fingers.
 */
export function pinchScale(scale0: number, spread0: number, spread: number): number {
  if (spread0 <= 0) return scale0;
  return clampScale(scale0 * (spread / spread0));
}

const spreadOf = (a: Point, b: Point) => Math.hypot(b.x - a.x, b.y - a.y);
const midOf = (a: Point, b: Point): Point => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });

/**
 * One move of a loose pinch: the fingers hold the world, so what lay under
 * their middle stays under it. First the frame follows the middle from where
 * it was to where it is, at the scale the frame has now; then the zoom is
 * about the world point under the new middle. Apart from the hook so that
 * the invariant can be held by a test without a pointer or a DOM.
 */
export function pinchTo(cam: Camera, box: Box, from: Point, to: Point, scale: number) {
  const k = lensOn(box, cam.frame().scale).k;
  cam.panTo(cam.frame().x - (to.x - from.x) / k, cam.frame().y - (to.y - from.y) / k);
  cam.zoomTo(worldAt(box, cam.frame(), { clientX: to.x, clientY: to.y }), scale);
}

export function useHand({
  cam,
  svg,
  tethered,
  ready,
}: {
  cam: Camera;
  svg: RefObject<SVGSVGElement | null>;
  /** Whether the camera is tied to the body: then the hand may only zoom. */
  tethered: boolean;
  /** Whether there is an svg to listen on -- it comes and goes with the map. */
  ready: boolean;
}) {
  //: A grab on the field is a pan; a grab on a node is only ever a click.
  const dragging = useRef<{
    moved: boolean;
    startX: number;
    startY: number;
    panX0: number;
    panY0: number;
  } | null>(null);
  //: Every finger pressed on the field, by pointer id, where it is now. A
  //: mouse is one of them. Two fingers are a pinch; a third is not written
  //: down at all -- were it, the pair would change under the pinch when one
  //: of the first two lifted, and the frame would jump to the new pair.
  const fingers = useRef(new Map<number, Point>());
  //: The pinch under way: what the scale and the fingers' spread were when
  //: the second finger came down, and where their middle was on the last move.
  const pinch = useRef<{ scale0: number; spread0: number; mid: Point } | null>(null);

  /** The pixels-to-world scale of the field as it stands right now. */
  const pixelsPer = () => {
    const field = svg.current;
    return field ? lensOn(field.getBoundingClientRect(), cam.frame().scale).k : null;
  };

  const toWorld = (e: { clientX: number; clientY: number }) => {
    const field = svg.current;
    if (!field) return { x: 0, y: 0 };
    return worldAt(field.getBoundingClientRect(), cam.frame(), e);
  };

  const letGo = () => {
    fingers.current.clear();
    pinch.current = null;
    dragging.current = null;
  };

  //: Pointer capture is a convenience (the pan does not break at the edge),
  //: not a condition: a pointer without capture (touch emulation, tests) must
  //: not break panning.
  const capture = (target: Element, pointerId: number) => {
    try {
      target.setPointerCapture?.(pointerId);
    } catch {
      /* no pointer with that id: panning works without the capture too */
    }
  };

  //: A pan or a pinch begun before the tether changed is over. Without this
  //: the press outlives the mode: the pointer is still down, `movePointer`
  //: starts refusing, and the moment the tether goes again the pan resumes
  //: from a start point measured seconds ago -- a jump, out of nowhere. A
  //: pinch that outlived the tie-off would pan a loose camera without ever
  //: having taken the frame from it.
  useEffect(letGo, [tethered]);

  //: The wheel over the map is zoom, and only zoom. React attaches wheel
  //: passively, and preventDefault from there does not work -- the page
  //: scrolled along with the zoom. Suppressed by a native listener with passive: false.
  useEffect(() => {
    const field = svg.current;
    if (!field) return;
    const block = (e: Event) => e.preventDefault();
    field.addEventListener("wheel", block, { passive: false });
    return () => field.removeEventListener("wheel", block);
  }, [svg, ready]);

  return {
    grabField(e: PointerEvent) {
      if (fingers.current.size >= 2) return;
      fingers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (fingers.current.size === 2) {
        //: The second finger turns a press or a pan into a pinch, and the
        //: pan is over for good: resumed after the pinch from a start point
        //: measured before it, it would jump.
        dragging.current = null;
        const [a, b] = [...fingers.current.values()];
        for (const id of fingers.current.keys()) capture(e.currentTarget as Element, id);
        //: Loose, a pinch is the hand: it takes the frame from every autopilot.
        if (!tethered) cam.takeFrame();
        pinch.current = { scale0: cam.frame().scale, spread0: spreadOf(a, b), mid: midOf(a, b) };
        return;
      }
      //: Tethered, the field is not draggable at all: zooming is the whole
      //: of what the hand may do.
      if (tethered) return;
      capture(e.currentTarget as Element, e.pointerId);
      //: A press is not yet a pan: the frame changes hands only once the hand
      //: actually moves it (below). A click on the field -- to shut a menu, to
      //: miss a node -- must not stop the walker being followed.
      dragging.current = {
        moved: false,
        startX: e.clientX,
        startY: e.clientY,
        panX0: cam.frame().x,
        panY0: cam.frame().y,
      };
    },

    movePointer(e: PointerEvent) {
      if (fingers.current.has(e.pointerId)) {
        fingers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
      }
      const held = pinch.current;
      if (held && fingers.current.size >= 2) {
        const [a, b] = [...fingers.current.values()];
        const scale = pinchScale(held.scale0, held.spread0, spreadOf(a, b));
        //: Tethered, a pinch is the wheel: the middle stays the middle.
        if (tethered) return cam.zoomOnMiddle(scale);
        //: Loose, the fingers hold the world (`pinchTo`).
        const field = svg.current;
        if (!field) return;
        const mid = midOf(a, b);
        pinchTo(cam, field.getBoundingClientRect(), held.mid, mid, scale);
        held.mid = mid;
        return;
      }
      //: Asked again, and not only at the press: a capture the pointer never
      //: released (a button let go outside the window) could otherwise carry a
      //: pan into a camera that has been tied back on in the meantime.
      if (tethered) return;
      const drag = dragging.current;
      if (!drag) return;
      const dx = e.clientX - drag.startX;
      const dy = e.clientY - drag.startY;
      if (!drag.moved && Math.hypot(dx, dy) > SLOP) {
        drag.moved = true;
        //: Here the hand takes the frame, and keeps it: no autopilot argues
        //: with a pan under way, and none resumes until the player's own step.
        cam.takeFrame();
        //: What the hand drags is where the frame is **now** -- a chase may
        //: have moved it since the press.
        drag.panX0 = cam.frame().x;
        drag.panY0 = cam.frame().y;
        drag.startX = e.clientX;
        drag.startY = e.clientY;
        return;
      }
      if (!drag.moved) return;
      const k = 1 / (pixelsPer() ?? 1);
      cam.panTo(drag.panX0 - dx * k, drag.panY0 - dy * k);
    },

    releasePointer(e: PointerEvent) {
      //: A finger that was never written down (the third) changes nothing.
      if (!fingers.current.delete(e.pointerId)) return;
      //: The pinch is over the moment either of its fingers lifts, and the
      //: one left does not go on as a pan (see `grabField`).
      pinch.current = null;
      dragging.current = null;
    },

    zoom(e: WheelEvent) {
      const scale = clampScale(cam.frame().scale * (e.deltaY < 0 ? NOTCH : 1 / NOTCH));
      //: Tethered, the wheel changes how much is seen and nothing else: the
      //: middle stays the middle, and the follow is not taken away by it.
      if (tethered) return cam.zoomOnMiddle(scale);
      //: Loose, a wheel is the hand: it takes the frame from every autopilot.
      const p = toWorld(e);
      cam.takeFrame();
      cam.zoomTo(p, scale);
    },

    /** The loupe buttons: a notch nearer or farther about the middle -- a
     *  button has no cursor to zoom towards. Loose, it is the hand like the
     *  wheel and takes the frame; tethered, the middle stays the middle. */
    zoomBy(direction: 1 | -1) {
      const scale = clampScale(cam.frame().scale * (direction > 0 ? NOTCH : 1 / NOTCH));
      if (!tethered) cam.takeFrame();
      cam.zoomOnMiddle(scale);
    },
  };
}
