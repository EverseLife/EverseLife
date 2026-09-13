// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The globe before the world (D-319, D-013): one half of the login and
 * registration screens is the planet itself, from the public map.
 *
 * The same planet as the map's, drawn by the map's own component
 * (`map/Planet`): the GPU's ground with its relief, its water, its snow and
 * its clouds -- north up, turning by itself until the hand takes it, zoomed
 * by the wheel. There is no session yet, so the book is read from the public
 * catalog and the clock from the public map's origin.
 *
 * Two states, by the step (owner, 2026-09-13; D-337, and the second named
 * place of standing motion in 50-interface/09 П3):
 *
 * - **before the doors** the planet is a picture of time passing: the year
 *   is wound slowly from now, as the map's winder winds it -- the sun goes
 *   round, day and night pass over the ground, the clouds move and the
 *   seasons turn (D-334, D-335). No marks at all: no provinces, no cities,
 *   no ways -- the ground and the sky alone;
 * - **at the doors** -- the last step of registration -- nothing is wound
 *   and nothing hides the ground: the time is now, the clouds are put away,
 *   and the printers a newcomer may be printed at stand as marks where they
 *   really are. A mark chosen names the door whose card the other half
 *   shows.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import * as api from "../api";
import type { Door, MapNode, RecipeBook, WorldMap } from "../api";
import { CatalogProvider } from "../actions";
import { dayHoursOf, type Clock } from "../clock";
import { t } from "../locale";
import { CELL_DEG, FINEST_UNIT, farOf, nearFrameM, nearOf } from "./map/bands";
import { placeAt, projectAll, type Geo } from "./map/globe";
import { W } from "./map/model";
import { markShare, paper, type Box } from "./map/paper";
import { Planet } from "./map/Planet";
import { useClimateView } from "./map/useClimateView";
import { useGlobe } from "./map/useGlobe";
import type { Overlays } from "./map/Switcher";
import { WIND_STEP_MAX_MS, type Pace } from "./map/useYear";

/** How much of the frame the disk takes at the outermost zoom. */
const FRAME = 1.05;
/** How fast the globe turns by itself, degrees of longitude a second: a
 *  slow turn on the login screen, stopped by the hand and on the door step. */
const SPIN_DEG_PER_S = 2;
const RAD = Math.PI / 180;
/** How far in the zoom may go, and one notch of it. */
const ZOOM_MAX = 400;
const NOTCH = 1.25;
/** A door's mark, in **pixels** of the screen -- of a door with nobody behind
 *  it; `markShare` grows it with the citizens. Not a share of the square like
 *  the rest: the mark is a target for a finger before it is a picture, and a
 *  share of the square made it three pixels across on a phone, where the
 *  globe is a third of the size it has on a desktop. */
const MARK_PX = 10;
/** Below this many drawn cells across the frame the ground is one flat colour. */
const CELLS_ACROSS = 1.5;
/** How often the moving globe is redrawn, a second -- its turn and its time
 *  in one commit: at two degrees a second the ground moves an eighth of a
 *  degree between redraws -- under a cell's width at any zoom -- and a
 *  redraw is the whole frame's ground. */
const SPIN_FPS = 15;
/** How fast time runs on the globe before the doors, as one of the map
 *  winder's paces (`useYear.PACES`): the slowest, a sixteenth -- a day of
 *  Terra's in half a minute, so the sun is watched creeping round rather
 *  than sweeping past, and a year in eight minutes (owner, 2026-09-13: the
 *  quarter was too quick). */
const TIME_PACE: Pace = "sixteenth";
/** What lies over the ground: the clouds and nothing else -- no provinces,
 *  no city lands, no lines (owner, 2026-09-13: no marks on this globe). */
const OVERLAYS: Overlays = {
  provinces: false,
  cities: false,
  contours: false,
  figures: false,
  clouds: true,
};

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
  //: The planet's clock with no body on it: the origin the public map
  //: names, the day the book gives -- what `look.clock` would say.
  const epoch = world?.epoch ?? null;
  const clock = useMemo<Clock | undefined>(
    () =>
      epoch && shown
        ? { planet: shown, epoch, day_hours: dayHoursOf(book?.constants, shown) }
        : undefined,
    [epoch, shown, book],
  );
  //: The door step: the planet as it is, to choose a place on.
  const choosing = doors !== null;
  //: Asked for motion to be spared, the globe neither turns nor winds: the
  //: rule every standing motion of the interface keeps (50-interface/09, П3).
  //: Read once, as the map's camera reads it: a setting changed mid-visit
  //: takes the next visit.
  const still = useMemo(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches, []);
  const moving = !choosing && !still;
  //: The sky over the ground as the map shows it, with the year wound by
  //: itself before the doors and held at now at them: the sun, the season
  //: and the weather of the moment shown. An animation of the picture, as
  //: the turn of the globe is, not a timer on data (D-226): nothing is
  //: asked of the server for it.
  //: Held at now, too, until there is a clock to wind: without one the sun,
  //: the season and the weather have no moment, and a wind would redraw
  //: the same picture for nothing.
  const view = useClimateView(book, shown, radius, clock, moving && clock ? TIME_PACE : null);
  //: The eye stands over the chosen door, else the first door, else the
  //: first placed node of the planet -- a city, the capital first.
  const chosen = onPlanet.find((door) => door.node === picked) ?? onPlanet[0];
  const target: Geo =
    chosen?.place ??
    (surface[0]?.place as Geo | undefined) ??
    //: A planet the public map shows nothing on yet -- a world younger than
    //: its first snapshot -- is looked at from over its equator and meridian.
    EQUATOR;
  //: Whether the eye has been put somewhere on purpose since the doors came.
  const aimed = useRef(false);
  //: The door step opening is a reason to aim again. The globe is the same
  //: one the login screen mounted, and it has turned by itself -- and under
  //: the hand -- through every step before: the first aim was spent on the
  //: login screen, and the doors opened on whatever side the turn had left,
  //: a still planet with no dot on it under a hint to choose one. Declared
  //: before the aim, so both run on the render the doors arrive in.
  useEffect(() => {
    if (choosing) aimed.current = false;
  }, [choosing]);
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
  //: stay where it is to be picked. Time runs on the same frames (`tick`):
  //: one loop, so the turn and the moment are one commit and one draw of
  //: the ground, not two loops beating against each other.
  const rotate = globe.rotate;
  const tick = view.year.tick;
  //: The eye's latitude through a ref: the hand moves it every frame of a
  //: drag, and a loop that restarted on it began its count again each time
  //: and never reached a step -- the sky stood still under the hand.
  const lat = useRef(0);
  lat.current = eye?.lat ?? 0;
  const hasEye = eye !== null;
  useEffect(() => {
    if (!moving || !rotate || !hasEye || !radius) return;
    let raf = 0;
    let last = performance.now();
    const step = (t: number) => {
      //: Not every frame: the turn is gathered until a redraw is worth it.
      if (t - last >= 1000 / SPIN_FPS) {
        const dt = Math.min(WIND_STEP_MAX_MS, t - last);
        last = t;
        if (!drag.current) {
          //: `rotate` takes the ground's movement in map units: to turn the
          //: eye east the ground goes west, by the arc of the turn at the
          //: eye's latitude.
          const stretch = Math.max(Math.cos(lat.current * RAD), 1e-3);
          rotate(-SPIN_DEG_PER_S * (dt / 1000) * RAD * radius * stretch, 0, true);
        }
        //: Under the hand too: the hand holds the globe, not the sky.
        tick(dt);
      }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [moving, rotate, tick, hasEye, radius]);
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
  //: The frame's facts the planet's layers read (`PlanetFrame`). Nothing
  //: here draws by them today -- the provinces and the lines are off
  //: (`OVERLAYS`) -- but they are told truly, so an overlay switched on is
  //: sized right. The map's scale counts a map unit as a pixel of a frame
  //: `W` wide (`map/model`); the paper knows the true pixel. The frame's
  //: width is the seen half's.
  const scale = 1 / sheet.perPixel;
  const frameScale = W / sheet.across;
  const marks = projectAll(
    eye,
    radius,
    onPlanet.map((door) => ({ key: door.node, place: door.place })),
  );

  return (
    <div className="entry-globe">
      <div className="globe-space" ref={space} />
      {/* No names: this globe writes none (`OVERLAYS`). */}
      <CatalogProvider book={book} names={null}>
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
          clock={clock}
          view={view}
          layer="terrain"
          //: At the doors the clouds are put away: they would hide the
          //: very ground the newcomer is choosing a place on.
          shown={choosing ? { ...OVERLAYS, clouds: false } : OVERLAYS}
          frame={{
            detailed,
            unit,
            //: The whole disk while a drawn cell is a whole one: the frame
            //: holds the planet, and nothing is cut for a window of it.
            within: unit >= 1 ? undefined : sheet.reach,
            far: farOf(scale),
            frameM: nearFrameM(nearOf(frameScale)),
            approach: false,
          }}
        >
          {/* A door's mark is drawn in pixels about its own origin and stood
              on the sphere by a matrix that scales it (`placeAt`): a centre in
              map units saturates short of the limb (`diskPath`). */}
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
