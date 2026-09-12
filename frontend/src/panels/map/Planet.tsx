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
import { supportsShadedGround, type Layer } from "./shade";
import type { Overlays } from "./Switcher";
import type { ClimateView } from "./useClimateView";

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
  //: The GPU ground is doing its best or has given up: the SVG ground
  //: draws the land until the textures are up, and for good once the GPU
  //: has failed -- a player must not see an empty disk where the globe was.
  const shadeable = useMemo(supportsShadedGround, []);
  const [shading, setShading] = useState<GroundGLState>("loading");
  const shaded = shadeable && shading !== "failed";
  const ready = shaded && shading === "ready";
  const gl = useRef<GroundGLHandle | null>(null);
  //: The map's camera redraws the ground at every frame it paints, off
  //: React, through this handle.
  useImperativeHandle(ref, () => ({ draw: () => gl.current?.draw() }), []);
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
            mode={ready ? "under" : "svg"}
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
