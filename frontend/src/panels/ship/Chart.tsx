// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The bridge's own chart: the sky as **this ship** sees it (D-240).
 *
 * The console used to open the world map on its space layer. That map is a map
 * of places: it draws everything anybody can see and answers "what is where".
 * A bridge asks a different question -- "where can I go, how long does it take
 * and what does it cost" -- and the answer is different for every hull, because
 * hours and fuel come from this ship's thrust against this ship's mass.
 *
 * So the chart draws these things and nothing else:
 *
 * * the star and the orbits, because a passage is planned against them: the
 *   planets close and part, and half a day of waiting is worth four times the
 *   fuel (D-037);
 * * every planet at the place the clock puts it now -- the same arithmetic the
 *   world map does, over the same epoch;
 * * a **corridor** from this hull to every destination it may aim at, labelled
 *   with the hours and the fuel of **this** ship. Unreachable ones stay drawn
 *   and stay grey: what one cannot do today is exactly what one plans for;
 * * the hull itself, where the sky has it (D-289): at its planet, along the
 *   line its order flies, or wherever inertia carried it -- and, this drawing's
 *   own addition, which way its nose points, which is the one thing a drifting
 *   crew wants first;
 * * the two lines D-289 asks for, told apart on purpose: the **coast** inertia
 *   lays if the engines stay silent and the **course** the order under way
 *   still has to fly -- plus, once a destination is picked and an arc worked
 *   out, the one under the slider's thumb. Nothing is drawn behind the hull:
 *   where it has been is not a thing one steers by.
 *
 * It is drawn as an instrument rather than as a picture: the hull is the middle
 * of the frame and stays there, the hand may only choose how near to look, and
 * nothing on it grows with the zoom -- see `scope.ts` for why, and `Glass.tsx`
 * for how far the licence to look like a ship's screen was taken.
 *
 * The course is set on it and nowhere else: a click picks the destination, and
 * the passage is ordered from the panel beneath. The list of routes the console
 * used to carry is gone -- a list is what one reads when the map cannot say it.
 */

import { useEffect, useMemo, useRef, useState, type PointerEvent } from "react";
import type { MapNode } from "../../api";
import { useBook } from "../../actions";
import { Glyph } from "../../Glyph";
import { t } from "../../locale";
import { planetName } from "../../planets";
import { along, term } from "../map/orbits";
import { Bezel, Screen } from "./Glass";
import { sameTarget, whole, type Route, type Target, type Vessel } from "./model";
import {
  CENTER,
  H,
  W,
  drawn,
  edgeOf,
  facing,
  heading,
  labelAt,
  part,
  pinchZoom,
  project,
  gridStep,
  partOf,
  ringSeen,
  span,
  unitFor,
  zoomAt,
  zoomBy,
  type Point,
  type Scope,
} from "./scope";

const TURN = Math.PI * 2;
const MS_PER_DAY = 86_400_000;
/**
 * The circle a moored hull runs on, in map units: how far it really stands off
 * the planet it hangs over (D-289, `sky.park`).
 *
 * A catalog number, so it comes with the book rather than as a key of its own
 * (D-225). It used to be twenty-six pixels of the display, which is why a hull
 * never left its planet however near one looked: a fixed pixel gap is not a
 * distance, and the zoom has nothing to open up.
 */
const PARK = "orbit.park_radii";
/** The scale a world's share of the Earth's radius is drawn at, and the
 *  shares themselves: the parking circle is so many radii of the body it is
 *  round (D-324), and the bodies stopped being one size. */
const BODY = "orbit.body_radius";
const SHARES = "planet.radius";
/** Below this the parking circle is a smudge on the dot, and is not drawn. */
const PARK_SEEN = 3;
/** How far the heading vector reaches, and where it starts off the hull. */
const NOSE = { from: 9, to: 52 };
/** How far inside the frame the marks of what fell off it stand. */
const EDGE = 16;
/**
 * How far this hull sees another, in map units: the one ring left on the glass.
 *
 * A catalog number, so it comes from `/public/constants` with the rest of the
 * book and not as a key of its own in `ship.view` (D-225): it is the same for
 * every hull in the system and does not change while the session lasts.
 */
const SIGHT = "orbit.sight_radius";
/** Below this the ring is a smudge round the hull rather than a circle. */
const SIGHT_SEEN = 4;
/** How many stops the slider has: fine enough that the thumb moves smoothly
 *  over a range this wide, and an integer, which is all a range input takes. */
const SLIDER = 1000;
/**
 * How often the sky is redrawn. An orbit moves half a degree an hour, so a
 * minute is already generous -- this is a clock hand over numbers the server
 * has already given, never a poll (D-226).
 */
const TICK_MS = 60_000;

type Sphere = { key: string; name: string; planet: string; x: number; y: number; r: number };

/** Where a planet stands `day` days after the epoch, in map units. The world
 *  map's arithmetic, over the same epoch, about the same star at the origin. */
function place(orbit: { radius: number; period_days: number; phase: number }, day: number) {
  const angle = orbit.phase + (TURN * day) / orbit.period_days;
  return {
    x: orbit.radius * Math.cos(angle),
    y: orbit.radius * Math.sin(angle),
    r: orbit.radius,
  };
}

/**
 * The hand on the display: the wheel, the two loupes and a pinch of two
 * fingers, and none of them may do anything but choose how near to look.
 *
 * There is no pan and no camera to take: the frame is the hull's, always
 * (`scope.ts`). What the world map needs a whole module for (`map/hand`) is
 * three handlers here, because half of that module is about who owns the frame.
 */
function useNear() {
  const [zoom, setZoom] = useState(1);
  //: The wheel is handled outside React (below) and has to read the zoom
  //: without re-subscribing on every notch: the state's own value, in a ref.
  const held = useRef(1);
  const svg = useRef<SVGSVGElement>(null);
  //: Every finger on the glass, by pointer id. Two are a pinch; a third is not
  //: written down, or the pair would change under the pinch when one lifted.
  const fingers = useRef(new Map<number, Point>());
  const pinch = useRef<{ zoom0: number; spread0: number } | null>(null);

  /** Look nearer or further. Says whether anything actually moved. */
  const look = (next: number) => {
    if (next === held.current) return false;
    held.current = next;
    setZoom(next);
    return true;
  };

  useEffect(() => {
    const field = svg.current;
    if (!field) return;
    //: React attaches `wheel` passively, and `preventDefault` from a passive
    //: listener does nothing -- the page scrolled along with the zoom. So the
    //: wheel is taken here, and the default is suppressed **only** when the
    //: notch actually moved the zoom: at either stop, and on a sideways swipe
    //: of a trackpad, the event is left to the page. The console stands inside
    //: a panel that scrolls, and a display that swallows the wheel for good is
    //: a display one cannot scroll past.
    const spin = (e: WheelEvent) => {
      if (e.deltaY === 0) return;
      if (look(zoomBy(held.current, e.deltaY < 0 ? 1 : -1))) e.preventDefault();
    };
    field.addEventListener("wheel", spin, { passive: false });
    return () => field.removeEventListener("wheel", spin);
  }, []);

  /** How far apart the two fingers are right now. */
  const gap = () => {
    const [a, b] = [...fingers.current.values()];
    return Math.hypot(b.x - a.x, b.y - a.y);
  };

  //: Pointer capture is a convenience, not a condition: without it a pen or a
  //: mouse released outside the glass never reports the lift, and the finger
  //: stays written down for ever -- after which the pinch never starts again.
  const capture = (target: Element, pointerId: number) => {
    try {
      target.setPointerCapture?.(pointerId);
    } catch {
      /* no pointer with that id: the pinch works without the capture too */
    }
  };

  return {
    zoom,
    svg,
    step: (steps: number) => look(zoomBy(held.current, steps)),
    slide: (part: number) => look(zoomAt(part)),
    onPointerDown: (e: PointerEvent) => {
      if (fingers.current.size >= 2) return;
      fingers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (fingers.current.size < 2) return;
      for (const id of fingers.current.keys()) capture(e.currentTarget as Element, id);
      pinch.current = { zoom0: held.current, spread0: gap() };
    },
    onPointerMove: (e: PointerEvent) => {
      if (!fingers.current.has(e.pointerId)) return;
      fingers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
      const grip = pinch.current;
      if (!grip || fingers.current.size < 2) return;
      look(pinchZoom(grip.zoom0, grip.spread0, gap()));
    },
    onPointerUp: (e: PointerEvent) => {
      //: The pinch is over the moment either finger lifts, and the one left
      //: does not go on as anything: there is nothing else a finger may do.
      if (fingers.current.delete(e.pointerId)) pinch.current = null;
    },
  };
}

export function Chart({
  vessel,
  planets,
  epoch,
  chosen,
  onChoose,
  plan,
}: {
  vessel: Vessel;
  /** The spheres, as the sky read gave them. Only those with an orbit are drawn. */
  planets: MapNode[];
  epoch: string | null;
  /** What the course is set for, if anything: a planet, or a hull in sight. */
  chosen: Target | null;
  onChoose: (target: Target | null) => void;
  /** The arc of the point the slider stands on, while it stands there (D-289). */
  plan: [number, number][] | null;
}) {
  //: The sky turns while the console is open, and it turns slowly. Not a data
  //: timer (D-226) -- nothing is asked of the server here; this is a clock hand
  //: over numbers the server already gave, the same one `useSky` winds on the
  //: world map.
  const [, setFrame] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setFrame((n) => n + 1), TICK_MS);
    return () => clearInterval(timer);
  }, []);
  const near = useNear();
  //: The one number the sky is drawn against that is not this hull's own: how
  //: far a hull sees another. Catalog, so it rides with the book (D-225).
  const book = useBook();
  const sight = Number(book?.constants?.[SIGHT] ?? 0);
  //: The circle this hull's own world holds it on, map units: so many radii
  //: (`orbit.park_radii`) of that world's drawn body (D-324). One flat number
  //: of units for every planet drew Pyroxis' circle inside Pyroxis.
  const shares = book?.constants?.[SHARES] as Record<string, number> | undefined;
  //: A world's drawn body in map units: the scale of the sky times that
  //: world's own share of the Earth's radius (D-320, D-324).
  const bodyOf = (planet: string) =>
    Number(book?.constants?.[BODY] ?? 0) * Number(shares?.[planet] ?? 1);
  const park = Number(book?.constants?.[PARK] ?? 0) * bodyOf(vessel.planet);
  //: The gradients and the pattern of the glass are named after the hull: two
  //: consoles on one screen would otherwise share one `id` and one of them
  //: would draw the other's.
  const mark = `chart-${vessel.ship.replace(/[^A-Za-z0-9_-]/g, "")}`;

  const orbiting = useMemo(() => planets.filter((node) => node.orbit), [planets]);
  const reach = Math.max(0, ...orbiting.map((node) => node.orbit?.radius ?? 0));
  const day = (Date.now() - (epoch ? new Date(epoch).getTime() : Date.now())) / MS_PER_DAY;

  const spheres: Sphere[] = orbiting.map((node) => ({
    key: node.key,
    name: node.name,
    planet: node.planet,
    ...place(node.orbit!, day),
  }));
  const by = new Map(spheres.map((one) => [one.planet, one]));

  //: Where the hull is, in map units, and where its mark hangs off that place.
  //: On a pad -- beside its planet's dot; in orbit -- out on the ring of its
  //: own, which is the whole visual point of the orbital step (D-245); in the
  //: sky -- where the sky has it (D-289): the state the server read, or along
  //: the order's line at the share of the time gone.
  const home = by.get(vessel.planet);
  const goal = vessel.flight?.planet ? by.get(vessel.flight.planet) : undefined;
  //: How much of the passage is behind: where along the arc the hull stands,
  //: where the line ahead begins, and which way the nose points.
  const share = (() => {
    if (!vessel.flight) return 0;
    const t0 = new Date(vessel.flight.started_at).getTime();
    const t1 = new Date(vessel.flight.arrives_at).getTime();
    return Math.min(1, Math.max(0, (Date.now() - t0) / Math.max(1, t1 - t0)));
  })();
  const at: Point | null = (() => {
    //: Adrift or in orbit, the state the server read is the place (D-354: in
    //: orbit a hull is a body in the sky like any other): nothing moves it
    //: but the next read. Under way the hull is walked along its line by the
    //: clock, as the world map walks it, so it does not stand still between
    //: two rereads of the console.
    if ((vessel.stage === "adrift" || vessel.stage === "orbit") && vessel.sky) {
      return { x: vessel.sky.x, y: vessel.sky.y };
    }
    if (!home) return null;
    if (!vessel.flight || !goal) {
      //: On the ground, at the planet, because that is where it is.
      return { x: home.x, y: home.y };
    }
    //: Along the arc the sky gave the passage (D-271), where there is one; a
    //: climb or a descent has none and is drawn straight beside the planet.
    const arc = vessel.flight.arc;
    const point = arc && arc.length >= 2 ? along(arc, share) : null;
    return point
      ? { x: point[0], y: point[1] }
      : { x: home.x + (goal.x - home.x) * share, y: home.y + (goal.y - home.y) * share };
  })();

  //: The display looks at the hull, and at the star only where there is no hull
  //: to look at -- a sky whose planets the read did not name.
  const scope: Scope = { at: at ?? { x: 0, y: 0 }, unit: unitFor(reach), zoom: near.zoom };
  const to = (p: { x: number; y: number }) => project(scope, p.x, p.y);
  const star = to({ x: 0, y: 0 });
  //: The graticule is ruled on the sky: the step is whatever the zoom asks
  //: for, and the offset pins the lines to the world's origin, so the ruling
  //: slides under the hull instead of standing still under a moving ship.
  const ruling = (() => {
    const step = span(scope, gridStep(scope));
    const over = (at: number) => ((at % step) + step) % step;
    return { step, x: over(star.x), y: over(star.y) };
  })();
  const hull: Point | null = at ? CENTER : null;
  const seesFar = sight > 0 ? span(scope, sight) : 0;
  //: What the corridors start from: the hull, wherever it is. A course is laid
  //: from where the ship stands, and now that standing "at a planet" is a real
  //: place rather than a pixel beside its dot, there is nothing else to use.
  const origin: Point | undefined = hull ?? (home ? to(home) : undefined);

  /** One line per destination planet: the row carries both ends of the slider. */
  const corridors = useMemo(() => {
    const best = new Map<string, Route>();
    for (const route of vessel.routes) {
      if (!best.has(route.planet)) best.set(route.planet, route);
    }
    return [...best.values()];
  }, [vessel.routes]);

  //: The lines ahead (D-289). The coast inertia draws is shown whenever the
  //: hull is in the sky: under way it is what happens if the engines fall
  //: silent now, adrift it is the whole of the future, and in orbit it is the
  //: lap round the planet -- sent round the planet's centre (D-354), and put
  //: where the planet stands on this display, since the planet moves on.
  const lap = vessel.sky?.inertia?.around ? by.get(vessel.sky.inertia.around) : undefined;
  const inertia =
    vessel.sky?.inertia && vessel.sky.inertia.trace.length >= 2
      ? vessel.sky.inertia.around
        ? lap
          ? vessel.sky.inertia.trace.map(([x, y]): [number, number] => [lap.x + x, lap.y + y])
          : null
        : vessel.sky.inertia.trace
      : null;
  const arc =
    vessel.stage === "flight" && vessel.flight?.arc && vessel.flight.arc.length >= 2
      ? vessel.flight.arc
      : null;
  //: Only the part still to be flown. The arc the server drew is the whole
  //: passage, and the half behind the hull is over: a display of where one is
  //: going does not also draw where one has been, and D-289 asks only for the
  //: line ahead.
  const ahead = arc ? part(arc, share, 1) : null;
  //: Which way the nose points. Under thrust it is the order's own arc; with
  //: the engines silent it is the coast -- and standing on a pad or on the
  //: circle, there is no heading to draw at all. Read off a line the server has
  //: already sent, so the wire gains nothing for it (D-225).
  const way = arc ? heading(arc, share) : inertia ? heading(inertia, 0) : null;

  //: Every world as the display has it: where its mark stands, whether the
  //: zoom pushed it out onto the edge, and where its readout goes. Worked out
  //: for all of them together and before anything is drawn, because two blocks
  //: that clash have to be told apart while both are still moveable.
  const marks = spheres.map((one) => {
    const seen = to(one);
    const edge = edgeOf(seen, EDGE);
    return {
      one,
      seen,
      edge,
      spot: edge ?? seen,
      label: labelAt(edge ?? seen, edge != null),
      mine: one.planet === vessel.planet && vessel.stage !== "adrift",
      route: corridors.find((r) => r.planet === one.planet),
    };
  });
  return (
    <div className="console">
      <svg
        className="chart"
        ref={near.svg}
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={t("ui-ship-chart")}
        onPointerDown={near.onPointerDown}
        onPointerMove={near.onPointerMove}
        onPointerUp={near.onPointerUp}
        onPointerCancel={near.onPointerUp}
      >
        <Screen mark={mark} grid={ruling} />

        {/* The rings: what the planets run along, and what makes a window a
            window. Drawn under everything, in the faintest ink there is. */}
        {spheres.map((one) => {
          //: At the near end of the zoom an orbit is a circle forty thousand
          //: pixels across whose middle is far off the frame. The ones that
          //: cannot cross the glass are not drawn at all.
          const r = span(scope, one.r);
          if (!ringSeen(star, r)) return null;
          return <circle key={`ring:${one.key}`} className="chart-orbit" cx={star.x} cy={star.y} r={r} />;
        })}
        {/* The star. Two rings round the disc, because with the hull in the
            middle of the frame the star is off it -- and a lone bright dot
            among the planets would read as one more planet. */}
        <g className="chart-star">
          <circle className="chart-corona" cx={star.x} cy={star.y} r="20" />
          <circle className="chart-corona" cx={star.x} cy={star.y} r="13" />
          <circle cx={star.x} cy={star.y} r="7" />
        </g>

        {/* The corridors of this hull: where it may go. Only the line --
            what the passage costs is written at the destination, below, where
            four readouts stand as far apart as the worlds they belong to. */}
        {origin &&
          corridors.map((route) => {
            const there = by.get(route.planet);
            if (!there) return null;
            const end = to(there);
            const picked = sameTarget(chosen, { planet: route.planet });
            return (
              <g
                key={`way:${route.planet}`}
                className={`chart-way${route.reachable ? "" : " off"}${picked ? " picked" : ""}`}
                onClick={() => onChoose(picked ? null : { planet: route.planet })}
              >
                {/* A hairline is not a click target: the corridor is picked on
                    a band as wide as a finger, and the band is not drawn. */}
                <line className="chart-grab" x1={origin.x} y1={origin.y} x2={end.x} y2={end.y} />
                <line x1={origin.x} y1={origin.y} x2={end.x} y2={end.y} />
              </g>
            );
          })}

        {/* The lines ahead: the coast, then the order, then the plan under the
            thumb -- in that order, so the one being chosen lies on top of the
            ones that are merely true. */}
        {inertia && <polyline className="chart-inertia" points={drawn(inertia, scope)} />}
        {ahead && ahead.length >= 2 && (
          <polyline className="chart-course" points={drawn(ahead, scope)} />
        )}
        {/* The arc being chosen. Only with a destination picked, and only
            once the panel below has actually worked one out: an instrument
            standing idle shows what **is**, not what might be. */}
        {chosen && plan && plan.length >= 2 && (
          <polyline className="chart-plan" points={drawn(plan, scope)} />
        )}

        {/* The worlds. A name apiece and nothing else: what a passage costs is
            written only at the world the course is actually set for, and only
            when it is. Four price blocks standing on the glass at all times are
            a table drawn over a picture -- the question "what does it cost to
            go there" is asked about **one** destination, by picking it.

            A world the zoom pushed off the frame is not lost: it moves to the
            edge at its bearing, with whatever it was carrying, and is picked
            from there. So the numbers are on the display at every zoom, and no
            mark grows with it -- a dot is the size the eye needs, at every
            distance. */}
        {marks.map(({ one, seen, edge, spot, label, mine, route }) => {
          const picked = sameTarget(chosen, { planet: one.planet });
          return (
            <g
              key={one.key}
              className={`chart-planet${mine ? " mine" : ""}${picked ? " picked" : ""}${
                edge ? " far" : ""
              }${route && !route.reachable ? " off" : ""}`}
              //: The tint is set as the group's `color` rather than as the
              //: dot's `fill`, so that the bloom round it is the planet's own
              //: light: `drop-shadow` reads `currentColor`, never the fill.
              style={{ color: `var(--planet-${one.planet})` }}
              onClick={() => route && onChoose(picked ? null : { planet: one.planet })}
            >
              {edge ? (
                <path
                  d="M0 0L-9 5L-9 -5Z"
                  fill="currentColor"
                  transform={`translate(${spot.x} ${spot.y}) rotate(${facing({
                    x: seen.x - CENTER.x,
                    y: seen.y - CENTER.y,
                  })})`}
                />
              ) : (
                //: A marker while the world is far, its own body once it is
                //: near. The two are the same picture at the moment the disc
                //: outgrows the dot, so nothing jumps -- and from D-324 the
                //: worlds are no longer one size, so a parking circle drawn to
                //: scale round an identical dot would read as a mistake rather
                //: than as a giant. The circle is that world's own near-planet
                //: orbit and depends on nothing but the world (owner,
                //: 2026-09-08); the drawing says so.
                <circle
                  cx={spot.x}
                  cy={spot.y}
                  r={Math.max(mine ? 9 : 7, span(scope, bodyOf(one.planet)))}
                  fill="currentColor"
                />
              )}
              <text x={label.x} y={label.name} textAnchor={label.anchor}>
                {planetName(one.planet)}
              </text>
              {route && picked && (
                <>
                  <text
                    className="chart-cheap"
                    x={label.x}
                    y={label.cheap}
                    textAnchor={label.anchor}
                  >
                    {route.cheap == null
                      ? "—"
                      : t("ui-ship-chart-cheap", {
                          term: term(whole(route.cheap)),
                          fuel: route.cheap.fuel.toFixed(0),
                        })}
                  </text>
                  <text
                    className="chart-fast"
                    x={label.x}
                    y={label.fast}
                    textAnchor={label.anchor}
                  >
                    {route.fast == null
                      ? ""
                      : t("ui-ship-chart-fast", {
                          term: term(whole(route.fast)),
                          fuel: route.fast.fuel.toFixed(0),
                        })}
                  </text>
                </>
              )}
            </g>
          );
        })}

        {/* The parking circle round this hull's planet, at the radius the
            sky gives it: where a climb puts a hull and an arrival aims one
            (D-354). The hull's own lap is the coast line above; this is the
            circle it is measured against. Only for the ship being commanded,
            and at the system's own scale it is less than a pixel and is not
            drawn at all: that is what a parking orbit is against a system
            eight hundred units wide. */}
        {home && vessel.stage === "orbit" && span(scope, park) >= PARK_SEEN && (
          <circle
            className="chart-parking"
            cx={to(home).x}
            cy={to(home).y}
            r={span(scope, park)}
          />
        )}

        {/* How far this hull sees another (D-289): the one circle on the
            glass, and a fact rather than a graduation. It is drawn in map
            units like everything else, so it opens up with the zoom -- and at
            rest it is two pixels across, which is itself the answer to "why
            can I not see anybody": the sight radius is five units against a
            system eight hundred wide. Below a few pixels it is a smudge round
            the hull and is not drawn. */}
        {hull && seesFar >= SIGHT_SEEN && (
          <circle className="chart-sight" cx={hull.x} cy={hull.y} r={seesFar} />
        )}

        {/* The others in the sky (D-289, wave 3): one's own hulls always, foreign
            ones while in sight. A drifter with a line to be met on is a target,
            and is chosen the way a planet is; the rest are there to be seen.

            Off the frame they keep a mark on the edge, as the worlds do, and
            for a harder reason: "свои корпуса видны всегда", and this drawing
            is the only place in the client where a hull is picked as a target
            -- dropped at the edge, a drifting hull on the far side of the
            system could not be gone to at all. */}
        {vessel.sightings.map((other) => {
          const seen = to(other);
          const edge = edgeOf(seen, EDGE);
          const spot = edge ?? seen;
          //: On the glass a hull's name stands beside its dot, out of the way
          //: of the worlds' readouts above and below them; only a mark driven
          //: onto the edge needs the readout's own placement, which knows how
          //: to read inwards from whichever edge it is.
          const label = edge
            ? labelAt(spot, true)
            : { x: spot.x + 7, anchor: "start" as const, name: spot.y - 5 };
          const picked = sameTarget(chosen, { ship: other.ship });
          return (
            <g
              key={`other:${other.ship}`}
              className={`chart-other${other.mine ? " mine" : ""}${other.target ? " target" : ""}${
                picked ? " picked" : ""
              }${edge ? " far" : ""} ${other.doing}`}
              onClick={() => other.target && onChoose(picked ? null : { ship: other.ship })}
            >
              {edge ? (
                <path
                  d="M0 0L-7 4L-7 -4Z"
                  transform={`translate(${spot.x} ${spot.y}) rotate(${facing({
                    x: seen.x - CENTER.x,
                    y: seen.y - CENTER.y,
                  })})`}
                />
              ) : (
                <circle cx={spot.x} cy={spot.y} r={3} />
              )}
              <text x={label.x} y={label.name} textAnchor={label.anchor}>
                {other.name}
              </text>
            </g>
          );
        })}

        {/* Which way the nose points, and how far the eye should read ahead of
            it. The one mark on the glass that says the hull is going somewhere
            rather than sitting somewhere. */}
        {hull && way && (
          <g className="chart-nose" transform={`translate(${hull.x} ${hull.y}) rotate(${facing(way)})`}>
            <line x1={NOSE.from} y1="0" x2={NOSE.to} y2="0" />
            <path d={`M${NOSE.to} 0L${NOSE.to - 7} 4L${NOSE.to - 7} -4Z`} />
          </g>
        )}

        {hull && (
          <g className={`chart-hull${vessel.stage === "adrift" ? " adrift" : ""}`}>
            {way ? (
              <path
                d="M8 0L-5 5L-2.5 0L-5 -5Z"
                transform={`translate(${hull.x} ${hull.y}) rotate(${facing(way)})`}
              />
            ) : (
              <circle cx={hull.x} cy={hull.y} r={4} />
            )}
            <text x={hull.x + 10} y={hull.y + 15}>
              {vessel.name}
            </text>
          </g>
        )}

        <Bezel
          mark={mark}
          zoom={near.zoom}
          sight={!!hull && seesFar >= SIGHT_SEEN}
          inertia={!!inertia}
          course={!!(ahead && ahead.length >= 2)}
          plan={!!(chosen && plan && plan.length >= 2)}
        />
      </svg>

      {/* The scan of the tube. A div rather than a layer of the drawing: the
          lines are the display's own pixels, and drawn inside the `viewBox`
          they would stretch with the panel instead of staying a scan. */}
      <div className="console-scan" aria-hidden="true" />

      {/* How near one is looking, and the three ways of changing it. A wheel
          is not enough -- a phone has none -- and the loupes are not enough
          either: the zoom runs from the whole system to a docking, seven
          hundred and fifty fold, and crossing that by notches is a minute of
          clicking. The slider is both the answer and the readout: where the
          thumb stands **is** how near one is looking, at a glance, without
          reading the figure in the corner. */}
      <div className="console-near">
        <button
          className="quiet"
          aria-label={t("ui-zoom-out")}
          title={t("ui-zoom-out")}
          onClick={() => near.step(-1)}
        >
          <Glyph name="farther" />
        </button>
        <input
          type="range"
          className="console-zoom"
          min={0}
          max={SLIDER}
          step={1}
          value={Math.round(partOf(near.zoom) * SLIDER)}
          aria-label={t("ui-ship-chart-zoom")}
          onChange={(e) => near.slide(Number(e.target.value) / SLIDER)}
        />
        <button
          className="quiet"
          aria-label={t("ui-zoom-in")}
          title={t("ui-zoom-in")}
          onClick={() => near.step(1)}
        >
          <Glyph name="nearer" />
        </button>
      </div>
    </div>
  );
}
