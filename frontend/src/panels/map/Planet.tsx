// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The planet as the map draws it (D-319, D-331), one drawing for both
 * screens that show one: the map, and the globe before the world
 * (`EntryGlobe`).
 *
 * The GPU's ground under the svg (`GroundGL`) with the sun, the season and
 * the weather of the moment shown (`useClimateView`); the SVG ground
 * (`Ground`), which draws the land until the textures are up, and for good
 * where WebGL2 is missing; and what is drawn on the ground itself where the
 * GPU drew it -- the provinces and the lines of the relief. What stands on
 * the ground -- the cities, the ways, a screen's own marks -- is the
 * caller's, and comes in as children, over all of it.
 *
 * The entry screen used to lay its planet by the SVG ground alone: flat
 * tones of the climate, no relief, no water, no clouds -- another planet
 * than the one the player met past the door (owner, 2026-09-13). Two copies
 * of this stack would drift apart the first time either changed; one
 * component cannot.
 *
 * The canvas and the svg are siblings, not one inside the other, so the
 * host lays them out: the canvas is placed over the svg's box by
 * `GroundGL` itself, against its offset parent, and paints under the svg by
 * DOM order.
 */

import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
  type SVGProps,
} from "react";

import type { Look } from "../../api";
import { useBook } from "../../actions";
import type { Eye } from "./globe";
import { Ground } from "./Ground";
import { GroundGL, type GroundGLHandle, type GroundGLState } from "./GroundGL";
import { Lines } from "./Lines";
import { Provinces } from "./Provinces";
import { rastersOf } from "./rasters";
import { supportsShadedGround, type Layer } from "./shade";
import type { Overlays } from "./Switcher";
import type { ClimateView } from "./useClimateView";

/** How long the planet may stand as its bare disk waiting for the GPU's
 *  ground, milliseconds, before the SVG ground draws the land after all:
 *  the passport and the quick copy of the picture are a third of a megabyte,
 *  seconds on the slowest line a player plays on, and past this the GPU's
 *  ground is taken to be stuck rather than slow. It still takes the SVG's
 *  place the moment it comes. */
const GPU_WAIT_MS = 8000;

/** The ball a surface scene looks at. */
export type Ball = { planet: string; eye: Eye; radius: number };

/** How the frame reads the ground: the map's facts of the frame
 *  (`map/bands`), or the entry screen's paper (`map/paper`). */
export type PlanetFrame = {
  /** Whether the frame holds several cells of the relief (`Ground`). */
  detailed: boolean;
  /** The drawn cell, in cells of the grid. */
  unit: number;
  /** How far from the eye the ground is laid, map units; undefined for the
   *  whole disk. */
  within: number | undefined;
  /** How far out the frame stands, in half-octaves (`bands.farOf`): the
   *  size the provinces' names are drawn at. */
  far: number;
  /** The frame's width, metres (`bands.nearFrameM`): the lines of the
   *  relief are drawn on the near frames alone. */
  frameM: number;
  /** On the way down from the sky (`bands.descentOf`): the eye is tilted,
   *  so the ground is read coarse and nothing that reads the rasters is
   *  laid over it. */
  approach: boolean;
};

export const Planet = forwardRef<
  GroundGLHandle,
  {
    /** The svg the planet is drawn in: the caller measures it and turns
     *  the hand's movements on it into the frame. */
    svg: RefObject<SVGSVGElement | null>;
    /** The svg's own attributes -- its viewBox, its role, its handlers. */
    field: SVGProps<SVGSVGElement>;
    /** The ball, or null where the scene is no surface: the sky, a house. */
    ball: Ball | null;
    /** The standing planet's clock, with `look`: the SVG ground's night.
     *  The GPU's night is the sun of `view`. */
    clock: Look["clock"];
    view: ClimateView;
    /** What the ground is coloured by (D-331). */
    layer: Layer;
    /** The overlays as drawn (`useLayers`). */
    shown: Overlays;
    frame: PlanetFrame;
    /** Whether a camera redraws the ground at every frame it paints,
     *  through the handle (the map). Without one (the entry screen) the
     *  frame is React's, and a viewBox React writes redraws it here. */
    camera?: boolean;
    children?: ReactNode;
  }
>(function Planet(
  { svg, field, ball, clock, view, layer, shown, frame, camera = false, children },
  ref,
) {
  const book = useBook();
  //: The GPU ground is doing its best or has given up. On its way, the
  //: planet is its disk alone (2026-09-18): the SVG ground that stood in
  //: there was another planet than the one that then appeared. For good
  //: once the GPU has failed, the SVG ground draws the land -- and after
  //: `GPU_WAIT_MS` without the GPU's, too: a request that never answers, a
  //: context lost and not given back, must not leave an empty disk where
  //: the globe was.
  const shadeable = useMemo(supportsShadedGround, []);
  const [shading, setShading] = useState<GroundGLState>("loading");
  const shaded = shadeable && shading !== "failed";
  const ready = shaded && shading === "ready";
  const waitingOn = shaded && !ready && ball ? ball.planet : null;
  const [late, setLate] = useState<string | null>(null);
  //: A timer of the window's, once, on how long it has waited -- not a
  //: clock on data (D-226): nothing is asked of the server by it.
  useEffect(() => {
    if (!waitingOn) return;
    const timer = setTimeout(() => setLate(waitingOn), GPU_WAIT_MS);
    return () => clearTimeout(timer);
  }, [waitingOn]);
  //: Come at last, it is waited for again the next time -- a lost context,
  //: a planet shown again.
  useEffect(() => {
    if (ready) setLate(null);
  }, [ready]);
  const patient = late === null || late !== ball?.planet;
  //: Without the GPU's ground the rasters are asked for here, at once: the
  //: probe and the legend read them over the SVG ground too, and nobody
  //: else asks for them now (`rasters.useRasters`). No copy first -- there
  //: is nothing to draw from it.
  const planetShown = ball?.planet ?? null;
  useEffect(() => {
    if (shaded || !planetShown) return;
    rastersOf(planetShown).catch((why) => console.warn(`rasters of ${planetShown}:`, why));
  }, [shaded, planetShown]);
  const gl = useRef<GroundGLHandle | null>(null);
  //: The map's camera redraws the ground at every frame it paints, off
  //: React, through this handle.
  useImperativeHandle(
    ref,
    () => ({ draw: () => gl.current?.draw(), drawNow: () => gl.current?.drawNow() }),
    [],
  );
  //: Without a camera a viewBox React writes is the frame: the entry
  //: screen's zoom and its paper are React's. With one, the camera has
  //: drawn that frame already, and a render that writes it again would be
  //: a second full pass of the shader for nothing.
  const viewBox = field.viewBox;
  useEffect(() => {
    if (!camera) gl.current?.draw();
  }, [camera, viewBox]);
  return (
    <>
      {shaded && ball && (
        <GroundGL
          ref={gl}
          planet={ball.planet}
          eye={ball.eye}
          radius={ball.radius}
          svg={svg}
          sun={view.sun}
          season={view.season}
          weather={view.weather}
          weatherDays={view.weatherDays}
          clouds={shown.clouds}
          layer={layer}
          onState={setShading}
        />
      )}
      <svg ref={svg} {...field}>
        {ball && (
          <Ground
            planet={ball.planet}
            eye={ball.eye}
            radius={ball.radius}
            book={book}
            clock={clock}
            at={view.year.atMs}
            //: The whole SVG ground only where the GPU's will not come, or
            //: is long in coming; on the way to it, the disk alone
            //: (`Ground`, `wait`).
            mode={ready ? "under" : shaded && patient ? "wait" : "svg"}
            detailed={frame.detailed}
            coarse={frame.approach}
            unit={frame.approach ? undefined : frame.unit}
            within={frame.approach ? undefined : frame.within}
          />
        )}
        {/* What is drawn on the ground itself, and only where the GPU drew
            it: the province's outline and name on the far frames, the
            relief's lines on the near ones. On the SVG path there are no
            rasters to read either from. */}
        {ball && ready && !frame.approach && (
          <>
            <Provinces
              planet={ball.planet}
              eye={ball.eye}
              radius={ball.radius}
              within={frame.within}
              far={frame.far}
              on={shown.provinces}
            />
            <Lines
              planet={ball.planet}
              eye={ball.eye}
              radius={ball.radius}
              within={frame.within}
              frameM={frame.frameM}
              show={{ contours: shown.contours, figures: shown.figures }}
            />
          </>
        )}
        {children}
      </svg>
    </>
  );
});
