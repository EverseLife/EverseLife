// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The camera (`camera.ts`) and the field it paints: made once, outside React,
 * painted straight onto the svg's `viewBox`; told when the field changes its
 * height, and stopped with the map. Out of `GraphMap.tsx`, which is past the
 * eight-hundred-line bar already.
 *
 * The frame's own shape is measured off the svg itself. The viewBox used to
 * keep a fixed 880 by 540 whatever box it was drawn in, and the browser
 * fitted one shape inside the other: empty bands at the edges of a wide pane,
 * and the vector layer standing shorter than the ground under it (owner,
 * 2026-09-11: the svg did not match the ground). Now the viewBox is cut to
 * the box, and there is nothing left to fit.
 *
 * Not a loop, though the svg is what the viewBox is written onto: the
 * element's size is settled by the layout around it -- `flex: 1` in the
 * column on a wide pane, a CSS `aspect-ratio` on a narrow one -- and never by
 * the viewBox's own proportions.
 */

import { useEffect, useRef, type RefObject } from "react";

import { PHONE } from "../../narrow";
import { boundsOf, type Band } from "./bands";
import { createCamera, viewBoxOf, type Camera } from "./camera";
import type { GroundGLHandle } from "./GroundGL";
import { frameHeight } from "./model";
import { notchOf } from "./Switcher";
import { factsOf, type Facts, type Sphere, type Surface } from "./useBands";

/**
 * How close the frame starts on a phone (brief section 9). Twice: the field
 * there is 375px against a desktop's ~1000, and at 2 a node's name comes out
 * at the size the desktop reads it. Well inside what the hand may zoom to
 * (`map/hand`), so nothing about panning or pinching is special-cased.
 */
const PHONE_SCALE = 2;

export function useCamera({
  tallRef,
  setTall,
  bandRef,
  surfaceRef,
  tell,
  anyVisible,
}: {
  /** The frame's height in map units at scale 1, as last measured. A ref:
   *  the camera lives outside React and asks on every frame it paints. */
  tallRef: RefObject<number>;
  setTall: (tall: number) => void;
  bandRef: RefObject<Band>;
  surfaceRef: RefObject<Surface>;
  /** What the frame decided (`useBands`). */
  tell: (facts: Facts) => void;
  /** Whether the scene draws anything: the svg is in the tree only then. */
  anyVisible: boolean;
}): {
  svgRef: RefObject<SVGSVGElement | null>;
  zoomRef: RefObject<HTMLInputElement | null>;
  shadedRef: RefObject<GroundGLHandle | null>;
  /** Where the planets are, read at frame time: the sky lays them out. */
  spheres: RefObject<() => Sphere[]>;
  cam: Camera;
} {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const zoomRef = useRef<HTMLInputElement | null>(null);
  //: The ground the GPU draws under the svg (landscape plan wave 5): asked
  //: to redraw with every frame the camera paints, off React like the
  //: viewBox. Without WebGL2 the svg ground stands whole (`map/Planet`).
  const shadedRef = useRef<GroundGLHandle | null>(null);
  /**
   * The camera: the render reads the same object, so a render that happens
   * for its own reasons never puts back a frame the animation has moved on
   * from.
   */
  const camera = useRef<Camera | null>(null);
  //: Where the planets are, read at frame time: the sky lays them out.
  const spheres = useRef<() => Sphere[]>(() => []);
  //: A phone's field is a third of a desktop's width, and the same frame
  //: over it drew a node's name at five pixels. The frame starts twice as
  //: close there: the body's neighbourhood, legible, and the rest a pan away.
  //: Asked once, at creation, not subscribed to: the map is remounted when
  //: a phone changes section, and a desktop that narrows keeps the frame it
  //: had -- a hook here would redraw forty nodes for a value read once.
  if (!camera.current) {
    camera.current = createCamera({
      onFrame: (f, inFrame) => {
        svgRef.current?.setAttribute("viewBox", viewBoxOf(f, tallRef.current));
        if (inFrame) shadedRef.current?.drawNow();
        else shadedRef.current?.draw();
        //: The slider rides with the frame, off React like the viewBox.
        if (zoomRef.current) {
          zoomRef.current.value = String(
            notchOf(f.scale, boundsOf(bandRef.current, surfaceRef.current)),
          );
        }
        //: What the frame decides is React's business only when it flips:
        //: cities opening, a band's edge reached, a planet under the middle.
        tell(factsOf(f, surfaceRef.current, spheres.current()));
      },
      scale: window.matchMedia(PHONE).matches ? PHONE_SCALE : 1,
      tall: () => tallRef.current,
      still: () =>
        window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    });
  }
  const cam = camera.current;

  //: Measured after the scene is settled (and below the camera, which the
  //: measure tells of the change): the svg is not in the tree at all
  //: while the map has nothing to draw, and an observer set on a mount that
  //: had no svg would never see the one that follows.
  useEffect(() => {
    const field = svgRef.current;
    if (!field) return;
    const measure = () => {
      const next = frameHeight(field.getBoundingClientRect());
      //: Compared before it is set: a resize that changes nothing -- and the
      //: observer fires on every layout -- must not redraw the map. Through
      //: the ref, not a setter's updater: the camera is told of the change
      //: right here, and an updater may be run twice.
      const was = tallRef.current;
      if (Math.abs(was - next) < 0.5) return;
      //: The frame keeps its middle through the resize, not its corner.
      cam.reshape(was, next);
      tallRef.current = next;
      setTall(next);
    };
    measure();
    const watch = new ResizeObserver(measure);
    watch.observe(field);
    return () => watch.disconnect();
    //: The ref and the setter are the map's own for its whole life: listed,
    //: they are no reason to measure again.
  }, [anyVisible, cam, tallRef, setTall]);
  //: Nothing of the camera outlives the map.
  useEffect(() => () => cam.stop(), [cam]);

  return { svgRef, zoomRef, shadedRef, spheres, cam };
}
