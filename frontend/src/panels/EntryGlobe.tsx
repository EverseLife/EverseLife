// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The globe before the world (D-319, D-013): one half of the login and
 * registration screens is the planet itself, from the public map.
 *
 * The same globe as the map's -- the true sphere, the ground of the relief,
 * north up, turning by itself until the hand takes it, zoomed by the
 * wheel -- with what the
 * public map shows to everybody: the cities and the ways between them, as
 * of the delayed snapshot (D-319 item 7). There is no session yet, so the
 * book is read from the public catalog and there is no clock, hence no
 * night. At the last step of registration the doors -- the printers a
 * newcomer may be printed at -- appear as marks; a mark chosen names the
 * door whose card the other half shows.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import * as api from "../api";
import type { Door, MapNode, RecipeBook, WorldMap } from "../api";
import { t } from "../locale";
import { Ground } from "./map/Ground";
import { arc, projectAll, type Geo } from "./map/globe";
import { paper, type Box } from "./map/paper";
import { useGlobe } from "./map/useGlobe";

/** How much of the frame the disk takes at the outermost zoom. */
const FRAME = 1.05;
/** The radius the globe is drawn at, in the picture's own units -- not the
 *  map's. A planet in map units is tens of millions, and the browser reads
 *  a circle's centre as a length and saturates it at 2^25 device pixels
 *  (see `diskPath`): a dot near the limb would slide inward on any display
 *  scaled past one. The hand's drags are turned back to map units. */
const DISK = 1000;
/** How fast the globe turns by itself, degrees of longitude a second: a
 *  slow turn on the login screen, stopped by the hand and on the door step. */
const SPIN_DEG_PER_S = 2;
const RAD = Math.PI / 180;
/** How far in the zoom may go, and one notch of it. */
const ZOOM_MAX = 400;
const NOTCH = 1.25;
/** Sizes in shares of the frame: a node's dot, a door's mark, a label. */
const DOT = 1 / 300;
const MARK = 1 / 60;
const LABEL = 1 / 36;
/** Below this many drawn cells across the frame the ground is one flat colour. */
const CELLS_ACROSS = 1.5;
/** The finest reading of the ground: a thirty-second of a grid cell, where
 *  the tiles of the local relief are read (D-323, `Ground`). */
const FINEST_UNIT = 1 / 32;
/** How often the turning globe is redrawn, a second: at two degrees a
 *  second the ground moves an eighth of a degree between redraws -- under
 *  a cell's width at any zoom -- and a redraw is the whole frame's ground. */
const SPIN_FPS = 15;
/** From this zoom on the plain nodes are drawn, and from this one the
 *  cities' names: farther out they are a smudge, not a map. */
const NODES_ZOOM = 2;
const LABELS_ZOOM = 4;

/** The relief's cell, degrees. */
const CELL_DEG = 2;
const EQUATOR: Geo = { lat: 0, lon: 0 };

/**
 * What the drawing is measured against (`map/paper`): the empty square that
 * says where the planet stands, the half of the screen the globe has, and the
 * canvas itself -- all three read the same way, so nothing is assumed about
 * where the canvas begins or how big the window says it is.
 */
type Field = { square: Box; half: Box; canvas: Box };

/** A box to the pixel: finer than that is a render nobody sees, and the
 *  layout effect below takes a measurement after every render. */
function pixels(box: DOMRect): Box {
  return {
    x: Math.round(box.x),
    y: Math.round(box.y),
    w: Math.round(box.width),
    h: Math.round(box.height),
  };
}

function sameBox(a: Box, b: Box): boolean {
  return a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h;
}

/** Whether two measurements say the same thing. */
function sameField(a: Field, b: Field): boolean {
  return sameBox(a.square, b.square) && sameBox(a.half, b.half) && sameBox(a.canvas, b.canvas);
}

/** The surface nodes the public map places on this planet, and the sky. */
function surfaceOf(world: WorldMap | null, planet: string | null): MapNode[] {
  if (!world || !planet) return [];
  return world.nodes.filter(
    (node) => node.layer !== "space" && node.planet === planet && node.place && "lat" in node.place,
  );
}

export function EntryGlobe({
  doors,
  picked,
  onPick,
}: {
  /** The doors to show, or null while the screen is not choosing one. */
  doors: Door[] | null;
  picked: string | null;
  onPick: (node: string) => void;
}) {
  //: The planet's radius and the ground's tones come from the vault, and
  //: the vault's book is public: read it once, before any identification.
  const [book, setBook] = useState<RecipeBook | null>(null);
  const [world, setWorld] = useState<WorldMap | null>(null);
  useEffect(() => {
    let live = true;
    api.constants().then(
      //: Only the constants are needed here; the rest of the book is the
      //: session's, and a door has none.
      (got) => live && setBook({ constants: got.values } as unknown as RecipeBook),
      //: A bare half of the screen is what the player sees; the reason goes
      //: to the console.
      (why) => console.warn("constants:", why),
    );
    api.worldMap().then(
      (got) => live && setWorld(got),
      (why) => console.warn("public map:", why),
    );
    return () => {
      live = false;
    };
  }, []);

  const planets = useMemo(() => (world?.nodes ?? []).filter((node) => node.orbit), [world]);
  //: Shown: the planet with doors, else the first of the sky -- the home world.
  const shown = doors?.find((door) => door.place)?.planet ?? planets[0]?.planet ?? null;
  const surface = useMemo(() => surfaceOf(world, shown), [world, shown]);
  const onPlanet = useMemo(
    () => (doors ?? []).filter((door) => door.planet === shown && door.place),
    [doors, shown],
  );
  const globe = useGlobe({ book, planet: shown, active: true });
  const { eye, radius, lookAt } = globe;
  //: The eye stands over the chosen door, else the first door, else the
  //: first placed node of the planet -- a city, the capital first.
  const chosen = onPlanet.find((door) => door.node === picked) ?? onPlanet[0];
  const target: Geo =
    chosen?.place ??
    (surface[0]?.place as Geo | undefined) ??
    //: A planet the public map shows nothing on yet -- a world younger than
    //: its first snapshot -- is looked at from over its equator and meridian.
    EQUATOR;
  useEffect(() => {
    lookAt(target);
  }, [target.lat, target.lon, lookAt]); // eslint-disable-line react-hooks/exhaustive-deps

  const [zoom, setZoom] = useState(1);
  const svg = useRef<SVGSVGElement | null>(null);
  //: The canvas is the window and the planet is a square of it (D-319): the
  //: drawing used to be cut by the box it sat in, and zoomed in that box was
  //: a keyhole -- the ground stopped at a seam a third of the way across the
  //: screen. The square stays in the flow, empty, to say where the planet
  //: stands and how big it is; the measurement below turns it into the
  //: viewBox, so the planet is drawn exactly where it was.
  const space = useRef<HTMLDivElement | null>(null);
  const [field, setField] = useState<Field | null>(null);
  const measure = useCallback(() => {
    const mark = space.current;
    const sheet = svg.current;
    const cell = mark?.closest(".entry-globe");
    if (!mark || !sheet || !cell) return;
    const square = pixels(mark.getBoundingClientRect());
    const canvas = pixels(sheet.getBoundingClientRect());
    if (!square.w || !square.h || !canvas.w || !canvas.h) return;
    //: The canvas's own box, not the window's: `width: 100%` of a fixed
    //: element is the initial containing block, and that is not `innerWidth`
    //: -- a scrollbar or a phone's address bar parts the two, and a viewBox
    //: whose sides no longer match the box's is letterboxed, which moves the
    //: planet and changes its size. Not moving it is the whole promise here.
    const now: Field = { square, canvas, half: pixels(cell.getBoundingClientRect()) };
    //: The same field is the same object: the measurement runs after every
    //: render, and a new object every time would be a render every time.
    setField((was) => (was && sameField(was, now) ? was : now));
  }, []);
  //: After every render, because the square **moves** as well as resizes: the
  //: door step hangs a line under the globe and the square rides up by half
  //: its height. A `ResizeObserver` sees a size, not a place, and the planet
  //: would have stayed where the square was two steps ago.
  useLayoutEffect(measure);
  useEffect(() => {
    const mark = space.current;
    const cell = mark?.closest(".entry-globe");
    if (!mark || !cell) return;
    //: And when nothing renders: the window resizes, the half grows a line of
    //: its own. Both boxes are watched -- the planet stands on the square's
    //: middle, the ground is laid to the half's edges.
    const watch = new ResizeObserver(measure);
    watch.observe(mark);
    watch.observe(cell);
    window.addEventListener("resize", measure);
    return () => {
      watch.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [measure]);
  const drag = useRef<{ x: number; y: number } | null>(null);
  //: The wheel over the globe is zoom, and only zoom: the page must not
  //: scroll under it. React attaches wheel passively, so the listener is
  //: native. A finger scrolls the page (`touch-action: pan-y`).
  useEffect(() => {
    const canvas = svg.current;
    if (!canvas) return;
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      setZoom((was) => Math.min(ZOOM_MAX, Math.max(1, was * (e.deltaY < 0 ? NOTCH : 1 / NOTCH))));
    };
    canvas.addEventListener("wheel", wheel, { passive: false });
    return () => canvas.removeEventListener("wheel", wheel);
  }, [shown, eye, radius]);
  //: The globe turns by itself on the login screen, slowly, eastwards --
  //: and stops under the hand and on the door step, where a mark must
  //: stay where it is to be picked. Frames, not renders: the turn goes
  //: through the eye's own frame-batched `rotate`.
  const rotate = globe.rotate;
  const spinning = doors === null;
  useEffect(() => {
    if (!spinning || !rotate || !eye || !radius) return;
    let raf = 0;
    let last = performance.now();
    const step = (t: number) => {
      //: Not every frame: the turn is gathered until a redraw is worth it.
      if (t - last >= 1000 / SPIN_FPS) {
        const dt = Math.min(200, t - last);
        last = t;
        if (!drag.current) {
          //: `rotate` takes the ground's movement in map units: to turn the
          //: eye east the ground goes west, by the arc of the turn at the
          //: eye's latitude.
          const stretch = Math.max(Math.cos(eye.lat * RAD), 1e-3);
          rotate(-SPIN_DEG_PER_S * (dt / 1000) * RAD * radius * stretch, 0);
        }
      }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
    //: The eye's latitude changes only under the hand; the loop reads it
    //: afresh each time the hand lets go (a new `eye` remounts it).
  }, [spinning, rotate, eye?.lat, radius]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!shown || !eye || !radius) {
    //: The square is kept even with nothing to draw: it is what the
    //: measurement watches, and it is the layout's, not the drawing's.
    return (
      <div className="entry-globe" aria-hidden="true">
        <div className="globe-space" ref={space} />
      </div>
    );
  }
  const span = (2 * DISK * FRAME) / zoom;
  //: The paper (`map/paper`): where the planet lands on a canvas that is the
  //: window, how far the ground is laid, how fine the grid is read. Until the
  //: first measurement the drawing is the square it always was -- one frame
  //: at most, since the measurement is taken before the paint.
  const square = svg.current
    ? Math.min(svg.current.clientWidth, svg.current.clientHeight)
    : 0;
  const sheet = field
    ? paper(field.square, field.half, field.canvas, span)
    : {
        perPixel: span / (square || 1),
        reach: span / 2,
        spread: 1,
        across: span,
        viewBox: `${-span / 2} ${-span / 2} ${span} ${span}`,
      };
  //: A pixel of the hand in map units: the eye turns by the planet's
  //: measure, not the picture's.
  const unitsPerPixel = () => sheet.perPixel * (radius / DISK);
  //: The closer, the finer the grid is read, so that the cells in the frame
  //: stay about as many as at the outermost zoom -- and the frame, not the
  //: planet, is what a redraw costs.
  const unit = Math.max(FINEST_UNIT, Math.min(1, sheet.spread / zoom));
  const cellUnits = DISK * CELL_DEG * RAD * unit;
  const detailed = sheet.across > CELLS_ACROSS * cellUnits;
  const placed = projectAll(eye, DISK, surface);
  const marks = projectAll(
    eye,
    DISK,
    onPlanet.map((door) => ({ key: door.node, place: door.place })),
  );
  const byKey = new Map(surface.map((node) => [node.key, node]));
  //: A city is the node others hang under: it wears its name.
  const cities = new Set(surface.map((node) => node.parent).filter(Boolean));

  return (
    <div className="entry-globe">
      <div className="globe-space" ref={space} />
      <svg
        ref={svg}
        viewBox={sheet.viewBox}
        role="img"
        aria-label={t("ui-entry-globe-label")}
        style={{ "--pc": `var(--planet-${shown})` } as React.CSSProperties}
        onPointerDown={(e) => {
          //: A finger is the page's first: it scrolls, and only a sideways
          //: drag turns the globe (`touch-action: pan-y` in the stylesheet).
          //: The mouse turns it outright.
          drag.current = { x: e.clientX, y: e.clientY };
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          const from = drag.current;
          if (!from || !globe.rotate) return;
          const k = unitsPerPixel();
          globe.rotate((e.clientX - from.x) * k, (e.clientY - from.y) * k);
          drag.current = { x: e.clientX, y: e.clientY };
        }}
        onPointerUp={() => {
          drag.current = null;
        }}
        onPointerCancel={() => {
          drag.current = null;
        }}
      >
        <Ground
          planet={shown}
          eye={eye}
          radius={DISK}
          book={book}
          clock={undefined}
          detailed={detailed}
          coarse={false}
          unit={unit}
          within={sheet.reach}
        />
        <g className="ways">
          {(world?.edges ?? []).map((edge) => {
            const a = byKey.get(edge.a)?.place;
            const b = byKey.get(edge.b)?.place;
            if (!a || !b || !("lat" in a) || !("lat" in b)) return null;
            const run = arc(eye, DISK, a, b);
            if (!run) return null;
            return (
              <polyline
                key={`${edge.a}|${edge.b}`}
                className={`way ${edge.surface}`}
                points={run.map((p) => `${p.x},${p.y}`).join(" ")}
              />
            );
          })}
        </g>
        <g className="places">
          {surface.map((node) => {
            const at = placed.get(node.key);
            const city = cities.has(node.key);
            //: Far out a city is a dot and a node nothing: a hundred dots
            //: on a disk the size of a coin is a smudge, and a name on it
            //: cannot be read at all.
            if (!at || (!city && zoom < NODES_ZOOM)) return null;
            return (
              <g key={node.key} className={`place ${city ? "city" : ""}`}>
                <circle cx={at.x} cy={at.y} r={span * DOT * (city ? 2 : 1)} />
                {city && zoom >= LABELS_ZOOM && (
                  <text x={at.x} y={at.y - span * DOT * 4} fontSize={span * LABEL}>
                    {node.name}
                  </text>
                )}
              </g>
            );
          })}
        </g>
        <g className="doors">
          {onPlanet.map((door) => {
            const at = marks.get(door.node);
            if (!at) return null;
            const mine = door.node === picked;
            return (
              <g
                key={door.node}
                className={`door ${mine ? "picked" : ""}`}
                //: A press, not a click, and it does not reach the globe
                //: beneath -- the map picks a node the same way (`Nodes`).
                //: A click would never come: the field takes the pointer to
                //: turn the globe, and a captured pointer's click is fired
                //: at the field, not at what was under the finger. So the
                //: mark was dead to the hand, and only the list of names
                //: beside it worked.
                onPointerDown={(e) => {
                  //: The primary button only: a right click opens a menu the
                  //: browser draws, and choosing a door under it would be a
                  //: choice nobody asked for.
                  if (e.button !== 0) return;
                  e.stopPropagation();
                  onPick(door.node);
                }}
              >
                <title>{door.city ? `${door.name} · ${door.city}` : door.name}</title>
                <circle cx={at.x} cy={at.y} r={span * MARK * (mine ? 1.4 : 1)} />
              </g>
            );
          })}
        </g>
      </svg>
      {doors && <p className="note center">{t("ui-entry-globe-doors-hint")}</p>}
    </div>
  );
}
