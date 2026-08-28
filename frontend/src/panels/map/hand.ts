// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What the hand may do to the frame: pan, zoom, and nothing else (D-238).
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
import { H, W } from "./model";

/** How far the pointer travels before a press becomes a pan, in pixels. */
const SLOP = 4;

/** What the map is drawn on, as much of it as the arithmetic needs. */
type Box = { left: number; top: number; width: number; height: number };

/**
 * The scale between the field's pixels and the world, and the margins around
 * the picture.
 *
 * The svg is elastic and its viewBox keeps proportions (`meet`), so unless the
 * two ratios happen to agree there is empty room at two of the edges. Without
 * counting it a click lands beside the node it was aimed at.
 */
export function lensOn(box: Box, scale: number) {
  const worldW = W / scale;
  const worldH = H / scale;
  const k = Math.min(box.width / worldW, box.height / worldH);
  return {
    k,
    offX: (box.width - worldW * k) / 2,
    offY: (box.height - worldH * k) / 2,
  };
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

  /** The pixels-to-world scale of the field as it stands right now. */
  const scale = () => {
    const field = svg.current;
    return field ? lensOn(field.getBoundingClientRect(), cam.frame().scale).k : null;
  };

  const toWorld = (e: { clientX: number; clientY: number }) => {
    const field = svg.current;
    if (!field) return { x: 0, y: 0 };
    return worldAt(field.getBoundingClientRect(), cam.frame(), e);
  };

  //: Pointer capture is a convenience (the pan does not break at the edge),
  //: not a condition: a pointer without capture (touch emulation, tests) must
  //: not break panning.
  const capture = (e: PointerEvent) => {
    try {
      (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    } catch {
      /* no pointer with that id: panning works without the capture too */
    }
  };

  //: A pan begun before the tether was tied back on is over. Without this the
  //: press outlives the mode: the pointer is still down, `movePointer` starts
  //: refusing, and the moment the tether goes again the pan resumes from a
  //: start point measured seconds ago -- a jump, out of nowhere.
  useEffect(() => {
    if (tethered) dragging.current = null;
  }, [tethered]);

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
      //: Tethered, the field is not draggable at all: the wheel is the whole
      //: of what the hand may do.
      if (tethered) return;
      capture(e);
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
      const k = 1 / (scale() ?? 1);
      cam.panTo(drag.panX0 - dx * k, drag.panY0 - dy * k);
    },

    releasePointer() {
      dragging.current = null;
    },

    zoom(e: WheelEvent) {
      const scale = Math.min(
        NEAREST,
        Math.max(FURTHEST, cam.frame().scale * (e.deltaY < 0 ? NOTCH : 1 / NOTCH)),
      );
      //: Tethered, the wheel changes how much is seen and nothing else: the
      //: middle stays the middle, and the follow is not taken away by it.
      if (tethered) return cam.zoomOnMiddle(scale);
      //: Loose, a wheel is the hand: it takes the frame from every autopilot.
      const p = toWorld(e);
      cam.takeFrame();
      cam.zoomTo(p, scale);
    },
  };
}
