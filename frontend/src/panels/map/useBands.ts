// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The band the map is in, and the hand-over between bands (D-319, wave 4).
 *
 * The arithmetic of the bands is `bands.ts`; this is what React keeps of it:
 * which band is shown, what the camera's last frame decided (the **facts**:
 * cities open, the floor or the ceiling reached, the planet under the
 * middle), and the effect that hands the map from one band to the next when
 * a fact flips. The camera is made once and paints outside React, so it
 * reads the surface's bounds through a ref and tells the facts through a
 * setter that changes nothing when nothing changed.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import type { RecipeBook } from "../../api";
import {
  CITY_SCALE,
  SKY_BOUNDS,
  STREET_SCALE,
  boundsOf,
  cityOpen,
  descentOf,
  farOf,
  globeScale,
  groundOf,
  leavesSurface,
  mapBounds,
  openScale,
  planetUnder,
  reachesSurface,
  surfaceBounds,
  type Band,
  type Bounds,
} from "./bands";
import type { Camera, Frame } from "./camera";
import { H, W, type Point } from "./model";
import { radiusOf } from "./useGlobe";

/** What a frame decided: React's business only when one of these flips. */
export type Facts = {
  cities: boolean;
  floor: boolean;
  ceiling: boolean;
  /** The frame holds several cells of the relief: the ground is worth drawing. */
  ground: boolean;
  /** The drawn cell of the ground, in cells of the grid (`groundOf`). */
  unit: number;
  /** How far out past the cities' closing, half-octaves (`farOf`). */
  far: number;
  /** The frame's own scale, not the band's. What is drawn at a size in
   *  pixels divides by **this**: `scaleAt(far)` steps by half-octaves, so a
   *  mark held to it swelled by half between one notch and the next and
   *  snapped back at the notch -- which is the breathing the owner saw
   *  (2026-09-11). `far` still decides what is drawn and how it is sorted,
   *  where a number that flips a few times is exactly what is wanted. */
  scale: number;
  /** The planet under the middle of the frame at the sky's ceiling, if any. */
  over: string | null;
  /** How far down the approach the frame is: 1 at the floor, 0 at the globe. */
  descent: number;
};

export const NO_FACTS: Facts = {
  cities: false,
  floor: false,
  ceiling: false,
  ground: false,
  unit: 1,
  far: 0,
  //: Before the first frame is measured: the scale the cities close at, so
  //: nothing is divided by nought.
  scale: CITY_SCALE,
  over: null,
  descent: 0,
};

/** The surface's bounds, the scale its globe fills the frame at, the
 *  planet's radius in map units the ground's cell is read from, and whether
 *  the sky lies beyond the floor -- on the ship's console it does, on the
 *  map tab the floor is the disk and there is nothing beyond. */
export type Surface = Bounds & { globe: number; radius: number | null; console: boolean };

export type Sphere = { key: string; planet: string; at: Point };

/** The facts of a frame, from the surface's floor and where the planets are. */
export function factsOf(frame: Frame, surface: Surface, spheres: readonly Sphere[]): Facts {
  const ceiling = reachesSurface(frame.scale);
  const middle = { x: frame.x + W / (2 * frame.scale), y: frame.y + H / (2 * frame.scale) };
  const ground = groundOf(frame.scale, surface.radius);
  return {
    cities: cityOpen(frame.scale),
    //: The floor is a fact only where the sky lies under it; the descent
    //: tilts the eye only on the way down from the sky.
    floor: surface.console && leavesSurface(frame.scale, surface.furthest),
    ceiling,
    ground: ground.shown,
    unit: ground.unit,
    far: farOf(frame.scale),
    scale: frame.scale,
    over: ceiling ? (planetUnder(middle, spheres)?.planet ?? null) : null,
    descent: surface.console ? descentOf(frame.scale, surface.furthest, surface.globe) : 0,
  };
}

export function sameFacts(a: Facts, b: Facts): boolean {
  return (
    a.cities === b.cities &&
    a.floor === b.floor &&
    a.ceiling === b.ceiling &&
    a.ground === b.ground &&
    a.unit === b.unit &&
    a.far === b.far &&
    a.over === b.over &&
    a.descent === b.descent
  );
}

export function useBands({
  book,
  initialLayer,
  hasSubnodes,
  radius,
  tall,
}: {
  book: RecipeBook | null;
  /** Where the map opens: the ship's console opens on space, and only the
   *  console has the sky at all -- the map tab's surface has no floor to
   *  fall through (owner, 2026-09-06). */
  initialLayer: string;
  /** Whether where one stands has an inside to show. */
  hasSubnodes: boolean;
  /** The shown planet's radius in map units, or null off the globe. */
  radius: number | null;
  /** The frame's height in map units at scale 1: the field's own shape
   *  (`model.frameHeight`). Everything vertical -- where the globe fits,
   *  where the floor of the surface is -- is measured against it. */
  tall: number;
}) {
  //: The band of scale the map is in: the sky, the surface, or the inside --
  //: a window, not a height. The console opens on the sky.
  const [asked, setBand] = useState<Band>(initialLayer === "space" ? "sky" : "surface");
  //: The inside is shown only where there is one: a walk from a house to a
  //: field with the door open would otherwise leave the map empty, with no
  //: door to close and no hand -- so the band is derived, not trusted.
  const band: Band = asked === "inside" && !hasSubnodes ? "surface" : asked;
  //: The band as of the last hand-over, ahead of the render: the hand reads
  //: its bounds here, so a notch of the wheel between a hand-over and the
  //: render that follows it clamps to the band entered, not the one left.
  const bandRef = useRef(band);
  bandRef.current = band;
  const enter = (next: Band) => {
    bandRef.current = next;
    setBand(next);
  };
  //: The surface's bounds come from the vault's height (`map.approach_km`)
  //: through the book; read through a ref by the camera, which is made once.
  const onConsole = initialLayer === "space";
  const surface = useMemo<Surface>(() => {
    const globe = globeScale(radius, tall);
    return {
      ...(onConsole
        ? surfaceBounds(Number(book?.constants?.["map.approach_km"]), tall)
        : mapBounds(globe, radius)),
      globe,
      radius,
      console: onConsole,
    };
  }, [book, radius, onConsole, tall]);
  const surfaceRef = useRef(surface);
  surfaceRef.current = surface;
  const [zoomed, setZoomed] = useState<Facts>(NO_FACTS);
  //: Compared before it is set, so a frame that changes nothing costs no render.
  const tell = (facts: Facts) => setZoomed((was) => (sameFacts(was, facts) ? was : facts));
  return { band, bandRef, enter, surface, surfaceRef, zoomed, tell };
}

/**
 * The hand-over between bands, on the ship's console -- the map tab has no
 * sky, and its floor is never a fact: zoomed out to the floor of the surface
 * the map is the sky; zoomed all the way in on a planet's marker -- or panned
 * onto one at the ceiling -- the surface opens where the true disk is the
 * marker's size, and the descent from there tilts it from the pole to where
 * one stands (plan §2 "Камера"). The two coordinate systems still do not
 * meet (plan §2, item 2): the stitch is a cut between two disks of one
 * size. Another planet's surface is not in the answer (D-240), so only
 * one's own opens.
 */
export function useHandOver({
  band,
  zoomed,
  enter,
  cam,
  book,
  mySphere,
  setPlanetFocus,
  surface,
}: {
  band: Band;
  zoomed: Facts;
  enter: (next: Band) => void;
  cam: Camera;
  book: RecipeBook | null;
  mySphere: string | null;
  setPlanetFocus: (planet: string | null) => void;
  /** The surface's bounds, whose `furthest` is the floor the disk opens just
   *  above and whose pair the door clamps a frame into. */
  surface: Surface;
}) {
  const floor = surface.furthest;
  /**
   * Through the door, and into the heights the band is drawn at.
   *
   * The bands share no scale: a frame that holds a continent holds a cabin as
   * a speck. The wheel's hand-over below sets the scale on the way between
   * the sky and the surface; the door had nobody to set it, so «Внутрь» from
   * a frame a thousand kilometres wide opened the inside empty -- one node
   * drawn under a pixel, with a door to close and nothing else in the field.
   * A scale already within the band is left alone: passing a door must not
   * jerk a frame that was fine.
   */
  const enterBand = (next: Band) => {
    enter(next);
    const bounds = boundsOf(next, surface);
    const held = cam.frame().scale;
    const within = Math.min(bounds.nearest, Math.max(bounds.furthest, held));
    if (within !== held) cam.zoomOnMiddle(within);
  };
  /** Open a planet's surface from the sky, the true disk the marker's size. */
  const open = (planet: string) => {
    setPlanetFocus(planet);
    enter("surface");
    cam.zoomOnMiddle(openScale(radiusOf(book, planet), floor));
  };
  useEffect(() => {
    if (band === "surface" && zoomed.floor) {
      enter("sky");
      cam.zoomOnMiddle(SKY_BOUNDS.furthest);
      return;
    }
    if (band === "sky" && zoomed.ceiling && zoomed.over) {
      if (mySphere && zoomed.over !== mySphere) return;
      open(zoomed.over);
    }
    //: The facts and the band are the reasons; the rest is read as it is.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoomed.floor, zoomed.ceiling, zoomed.over, band]);
  /** Open a planet and fly down to its streets: the marker's click. */
  const descend = (planet: string) => {
    open(planet);
    cam.zoomToward(STREET_SCALE);
  };
  return { descend, enterBand };
}
