// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The globe before the world (D-319, D-013): one half of the login and
 * registration screens is the planet itself, from the public map.
 *
 * The same planet as the map's, drawn by the map's own component
 * (`map/Planet`): the GPU's ground with its relief, its water, its snow and
 * its clouds, and the overlays the player last left on the map -- north up,
 * turning by itself until the hand takes it, zoomed by the wheel. On it,
 * what the public map shows to everybody: the cities and the ways between
 * them, as of the delayed snapshot (D-319 item 7). There is no session yet,
 * so the book and the names are read from the public catalogs, and there is
 * no clock, hence no night. At the last step of registration the doors --
 * the printers a newcomer may be printed at -- appear as marks; a mark
 * chosen names the door whose card the other half shows.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import * as api from "../api";
import type { Door, MapNode, RecipeBook, WorldMap } from "../api";
import { CatalogProvider } from "../actions";
import { currentLocale, t } from "../locale";
import { namesOf, type Names } from "../names";
import { CELL_DEG, FINEST_UNIT, farOf, nearFrameM, nearOf } from "./map/bands";
import { arc, placeAt, projectAll, type Geo } from "./map/globe";
import { W } from "./map/model";
import { markShare, paper, type Box } from "./map/paper";
import { Planet } from "./map/Planet";
import { useClimateView } from "./map/useClimateView";
import { useGlobe } from "./map/useGlobe";
import { useLayers } from "./map/useLayers";

/** How much of the frame the disk takes at the outermost zoom. */
const FRAME = 1.05;
/** How fast the globe turns by itself, degrees of longitude a second: a
 *  slow turn on the login screen, stopped by the hand and on the door step. */
const SPIN_DEG_PER_S = 2;
const RAD = Math.PI / 180;
/** How far in the zoom may go, and one notch of it. */
const ZOOM_MAX = 400;
const NOTCH = 1.25;
/** Sizes in shares of the square the planet stands in: a node's dot and a
 *  label. */
const DOT = 1 / 300;
/** A door's mark, in **pixels** of the screen -- of a door with nobody behind
 *  it; `markShare` grows it with the citizens. Not a share of the square like
 *  the rest: the mark is a target for a finger before it is a picture, and a
 *  share of the square made it three pixels across on a phone, where the
 *  globe is a third of the size it has on a desktop. */
const MARK_PX = 10;
const LABEL = 1 / 36;
/** Below this many drawn cells across the frame the ground is one flat colour. */
const CELLS_ACROSS = 1.5;
/** How often the turning globe is redrawn, a second: at two degrees a
 *  second the ground moves an eighth of a degree between redraws -- under
 *  a cell's width at any zoom -- and a redraw is the whole frame's ground. */
const SPIN_FPS = 15;
/** From this zoom on the plain nodes are drawn, and from this one the
 *  cities' names: farther out they are a smudge, not a map. */
const NODES_ZOOM = 2;
const LABELS_ZOOM = 4;

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

/**
 * The planet the globe draws: the one the first door with degrees on it
 * stands on. One globe is one planet, so a door on another is not drawn --
 * and the half beside the globe offers those by name instead (`Doors`), which
 * is why this rule is exported rather than read twice.
 */
export function drawnOn(doors: readonly Door[] | null): string | null {
  return doors?.find((door) => door.place && "lat" in door.place)?.planet ?? null;
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
  //: The provinces' names, in the language the screen speaks: public too.
  const [names, setNames] = useState<Names | null>(null);
  const [world, setWorld] = useState<WorldMap | null>(null);
  useEffect(() => {
    let live = true;
    api.constants().then(
      //: Only the constants are needed here; the rest of the book is the
      //: session's, and a door has none. Empty rather than missing: the book
      //: goes into the context the map's layers read (`CatalogProvider`),
      //: and a reader there that walks the recipes must find none, not crash.
      (got) =>
        live &&
        setBook({
          bulk: [],
          materials: [],
          units: {},
          operations: [],
          recipes: [],
          classes: {},
          tool_classes: {},
          synonyms: {},
          constants: got.values,
        }),
      //: A bare half of the screen is what the player sees; the reason goes
      //: to the console.
      (why) => console.warn("constants:", why),
    );
    api.renames().then(
      (got) => live && setNames(namesOf(got, currentLocale())),
      //: A province then wears its id, as it does on the map without them.
      (why) => console.warn("renames:", why),
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
  //: Shown: the planet with doors, else the first of the sky -- the home
  //: world. Doors on any other planet are not drawn, and `Doors` names them
  //: instead: the two halves read the same rule (`drawnOn`).
  const shown = drawnOn(doors) ?? planets[0]?.planet ?? null;
  const surface = useMemo(() => surfaceOf(world, shown), [world, shown]);
  //: The doors this globe draws: on the planet it shows, and with degrees on
  //: its sphere -- a flat place is not a place on a globe. What is left out
  //: is named beside it instead (`Doors`), so nothing is lost by being
  //: undrawable.
  const onPlanet = useMemo(
    () =>
      (doors ?? []).filter(
        (door): door is Door & { place: Geo } =>
          door.planet === shown && !!door.place && "lat" in door.place,
      ),
    [doors, shown],
  );
  const globe = useGlobe({ book, planet: shown, active: true });
  const { eye, radius, lookAt } = globe;
  //: The sky over the ground as the map shows it. No clock before a
  //: session: the season and the weather of the world's first day, and no
  //: sun -- the ground is lit from the map's own north-west.
  const view = useClimateView(book, shown, radius, undefined);
  //: The overlays the player left on the map, as the terrain wears them:
  //: the planet before the door is the one behind it. Not the layer -- a
  //: legend layer would colour this planet with no legend beside it.
  const { overlays } = useLayers();
  //: The eye stands over the chosen door, else the first door, else the
  //: first placed node of the planet -- a city, the capital first.
  const chosen = onPlanet.find((door) => door.node === picked) ?? onPlanet[0];
  const target: Geo =
    chosen?.place ??
    (surface[0]?.place as Geo | undefined) ??
    //: A planet the public map shows nothing on yet -- a world younger than
    //: its first snapshot -- is looked at from over its equator and meridian.
    EQUATOR;
  //: Whether the eye has ever been put somewhere on purpose.
  const aimed = useRef(false);
  useEffect(() => {
    //: Unchoosing a door must not move the sky: on a phone the way back from
    //: the card is the way to the globe, and a player who turned the planet
    //: to its far side would find it spun home under them. The first aim
    //: still happens -- the screen opens over a door, not over a meridian.
    if (!picked && aimed.current) return;
    aimed.current = true;
    lookAt(target);
  }, [target.lat, target.lon, picked, lookAt]); // eslint-disable-line react-hooks/exhaustive-deps

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
  //: After every render, because the square **moves** as well as resizes: a
  //: step opens or closes and the half it is in changes shape under the
  //: square. A `ResizeObserver` sees a size, not a place, and the planet
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
  //: In map units, as the map draws: the ground's layers measure the frame
  //: in metres of the planet, and a picture of its own size would tell them
  //: of another planet.
  const span = (2 * radius * FRAME) / zoom;
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
  //: The closer, the finer the grid is read, so that the cells in the frame
  //: stay about as many as at the outermost zoom -- and the frame, not the
  //: planet, is what a redraw costs.
  const unit = Math.max(FINEST_UNIT, Math.min(1, sheet.spread / zoom));
  const cellUnits = radius * CELL_DEG * RAD * unit;
  const detailed = sheet.across > CELLS_ACROSS * cellUnits;
  //: The map's scale counts a map unit as a pixel of a frame `W` wide
  //: (`map/model`), whatever the pane; the names of `bands` are sized in
  //: those pixels. The paper knows the true one, so the provinces' names
  //: come out at the pixels `bands` means rather than at a pane's guess.
  const scale = 1 / sheet.perPixel;
  //: The frame's width is the seen half's, not an 880-pixel pane's: the
  //: lines of the relief ask how many metres the frame really holds.
  const frameScale = W / sheet.across;
  //: The square's side, pixels: what the dots and the names are sized by.
  const side = span * scale;
  const placed = projectAll(eye, radius, surface);
  const marks = projectAll(
    eye,
    radius,
    onPlanet.map((door) => ({ key: door.node, place: door.place })),
  );
  const byKey = new Map(surface.map((node) => [node.key, node]));
  //: A city is the node others hang under: it wears its name.
  const cities = new Set(surface.map((node) => node.parent).filter(Boolean));

  return (
    <div className="entry-globe">
      <div className="globe-space" ref={space} />
      <CatalogProvider book={book} names={names}>
        {/* No camera here: the ground redraws by itself as the eye turns
            and as the viewBox moves (`Planet`), so nobody holds its handle. */}
        <Planet
          svg={svg}
          field={{
            viewBox: sheet.viewBox,
            //: A group, not a picture: `role="img"` takes the whole drawing
            //: for one image and hides what is inside it, and inside it are
            //: the doors -- controls a keyboard has to reach (D-077).
            role: "group",
            "aria-label": t("ui-entry-globe-label"),
            style: { "--pc": `var(--planet-${shown})` } as React.CSSProperties,
            onPointerDown: (e) => {
              //: A finger is the page's first: it scrolls, and only a
              //: sideways drag turns the globe (`touch-action: pan-y` in the
              //: stylesheet). The mouse turns it outright.
              drag.current = { x: e.clientX, y: e.clientY };
              e.currentTarget.setPointerCapture(e.pointerId);
            },
            onPointerMove: (e) => {
              const from = drag.current;
              if (!from || !globe.rotate) return;
              //: A pixel of the hand in map units: the eye turns by the
              //: planet's measure.
              const k = sheet.perPixel;
              globe.rotate((e.clientX - from.x) * k, (e.clientY - from.y) * k);
              drag.current = { x: e.clientX, y: e.clientY };
            },
            onPointerUp: () => {
              drag.current = null;
            },
            onPointerCancel: () => {
              drag.current = null;
            },
          }}
          ball={{ planet: shown, eye, radius }}
          clock={undefined}
          view={view}
          layer="terrain"
          shown={overlays}
          frame={{
            detailed,
            unit,
            //: The whole disk while a drawn cell is a whole one: the frame
            //: holds the planet, and the provinces read the walk made once
            //: for it rather than cutting their own at every turn of the eye.
            within: unit >= 1 ? undefined : sheet.reach,
            far: farOf(scale),
            frameM: nearFrameM(nearOf(frameScale)),
            approach: false,
          }}
        >
          <g className="ways">
            {(world?.edges ?? []).map((edge) => {
              const a = byKey.get(edge.a)?.place;
              const b = byKey.get(edge.b)?.place;
              if (!a || !b || !("lat" in a) || !("lat" in b)) return null;
              const run = arc(eye, radius, a, b);
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
          {/* What stands on the planet is drawn in pixels about its own
              origin and stood on the sphere by a matrix that scales it
              (`placeAt`): a centre in map units saturates short of the limb
              (`diskPath`), and a `font-size` in map units is one the browser
              draws no glyphs for. */}
          <g className="places">
            {surface.map((node) => {
              const at = placed.get(node.key);
              const city = cities.has(node.key);
              //: Far out a city is a dot and a node nothing: a hundred dots
              //: on a disk the size of a coin is a smudge, and a name on it
              //: cannot be read at all.
              if (!at || (!city && zoom < NODES_ZOOM)) return null;
              return (
                <g
                  key={node.key}
                  className={`place ${city ? "city" : ""}`}
                  transform={placeAt(at, sheet.perPixel)}
                >
                  <circle r={side * DOT * (city ? 2 : 1)} />
                  {city && zoom >= LABELS_ZOOM && (
                    <text y={-side * DOT * 4} fontSize={side * LABEL}>
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
                  transform={placeAt(at, sheet.perPixel)}
                  //: A control, and reachable without a hand: the row of names
                  //: beside the globe is gone, so this mark is the whole of how
                  //: a door is chosen, and "full keyboard navigation" is not a
                  //: wish of the vault's but a rule (D-077, 50-interface/00).
                  role="button"
                  tabIndex={0}
                  aria-label={door.city ? `${door.name} · ${door.city}` : door.name}
                  aria-pressed={mine}
                  onKeyDown={(e) => {
                    if (e.key !== "Enter" && e.key !== " ") return;
                    //: Space scrolls a page and Enter submits a form: neither is
                    //: what a pressed button means here.
                    e.preventDefault();
                    onPick(door.node);
                  }}
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
                  {/* The mark grows with the people printed there, by the
                      logarithm of them (`markShare`): the chosen one a little
                      larger again, so the hand sees what it picked. */}
                  <circle r={MARK_PX * markShare(door.citizens) * (mine ? 1.3 : 1)} />
                </g>
              );
            })}
          </g>
        </Planet>
      </CatalogProvider>
      {/* Only while it is worth saying: with a door chosen the card is what
          the screen is about, and on a phone that card stands over this line
          anyway. An empty world has no dots to point at either. */}
      {doors && !picked && onPlanet.length > 0 && (
        <p className="note center">{t("ui-entry-globe-doors-hint")}</p>
      )}
    </div>
  );
}
