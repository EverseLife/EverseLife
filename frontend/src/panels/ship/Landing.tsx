// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Coming down, onto the planet the hull is already over (D-245, D-319).
 *
 * The pier is chosen here and not before the passage: with the planet
 * already below, which is the moment a crew actually knows what it is
 * choosing between and the moment a dark beacon actually matters. And it is
 * chosen **on the globe** (D-319 item 10): the planet turns under the hand,
 * the pads the server offers stand on it where the public map places them
 * -- the lit spaceports, or on a planet one lands anywhere on (D-233) every
 * node of its surface -- and a mark's size is the ground the pad has free,
 * because the hull sets down on the node's open ground; a mark with less
 * than the hull needs is drawn hollow, and the order to it would be refused
 * on the choice, not after the descent. The list beside the globe names the
 * same pads for the keyboard (D-077); the two read one choice.
 *
 * The surface is the console's map: the public snapshot, which is what the
 * world knows of a planet a few days ago (D-319 item 7) -- from orbit the eye
 * sees no node of the ground (`map.sight_km`) -- with the crew's own map laid
 * over it (owner, 2026-09-08). A pad does not move, so the two cannot
 * disagree about where one is; what they disagree about is whether it is
 * there at all, and the crew's own answer is the newer. Without that a world
 * younger than the snapshot's delay -- every world for its first days -- had
 * no globe here, only the list beside it.
 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import type { Look, RecipeBook, WorldMap } from "../../api";
import { t } from "../../locale";
import { planetName } from "../../planets";
import { Ground } from "../map/Ground";
import { CELL_DEG, FINEST_UNIT } from "../map/bands";
import { arc, projectAll, type Geo } from "../map/globe";
import { useGlobe } from "../map/useGlobe";
import type { Pad, Vessel } from "./model";
import { aimOf, growthOf, padsOn, spreadOf, surfaceOf, type PadMark } from "./pads";

/** The radius the globe is drawn at, in the picture's own units -- not the
 *  map's (see `EntryGlobe`, `diskPath`): the hand's drags are turned back to
 *  map units. */
const DISK = 1000;
/** How much of the frame the disk takes at the outermost zoom. */
const FRAME = 1.05;
/** How far in the zoom may go, and one notch of it: the piers of one city
 *  stand metres apart on a planet hundreds of kilometres across, and the
 *  frame has to come down to a street to tell them apart. */
const ZOOM_MAX = 4000;
const NOTCH = 1.25;
/** The first frame: the marks with this margin round them, and never
 *  narrower than this arc -- a lone pier opens with some ground about it. */
const MARGIN = 1.6;
const LEAST_DEG = 0.05;
const RAD = Math.PI / 180;
/** A pad's mark in **pixels** of the screen, for the least room among them;
 *  the others grow by the square root of theirs (`growthOf`), up to this
 *  much. The chosen one a little larger again, so the hand sees what it
 *  picked. */
const MARK_PX = 7;
const MARK_GROWTH_MAX = 2.5;
const PICKED_GROWTH = 1.3;
/** A plain node's dot, in pixels: the surface about the pads, for bearings. */
const DOT_PX = 2;
/** Below this many drawn cells across the frame the ground is one flat colour. */
const CELLS_ACROSS = 1.5;

export function Landing({
  vessel,
  busy,
  book,
  clock,
  world,
  land,
}: {
  vessel: Vessel;
  busy: boolean;
  book: RecipeBook | null;
  /** The standing planet's clock, for the night on the globe; another
   *  planet's day is the book's. */
  clock: Look["clock"];
  /** The public map, read by the console (D-319 item 7). */
  world: WorldMap | null;
  land: (port: string) => void;
}) {
  const home: Pad[] = vessel.landings;
  const cost = vessel.descent;
  const [chosen, setChosen] = useState("");
  const marks = useMemo(() => padsOn(world, vessel.planet, home), [world, vessel.planet, home]);
  //: In orbit and no price to come down: the orbit is too wide to land from
  //: (D-354), and the way down starts with a course to this same planet.
  if (!cost) {
    return <p className="note">{t("ui-ship-orbit-high")}</p>;
  }
  if (home.length === 0) {
    return <p className="note">{t("ui-ship-nowhere-to-land")}</p>;
  }
  const port = home.some((pad) => pad.node === chosen) ? chosen : home[0].node;
  const need = vessel.footprint ?? null;
  return (
    <div className="landing">
      <p>
        <b>{t("ui-ship-land-title")}</b> · {planetName(vessel.planet)} ·{" "}
        {/* A hull with no thrust at all is priced at nothing, and the number
            is left out the way the interpolation left it out --
            `String(undefined)` would show the player the word "undefined". */}
        {t("ui-ship-leg-cost", {
          hours: cost.hours?.toFixed(1) ?? "",
          fuel: cost.fuel?.toFixed(0) ?? "",
        })}{" "}
        {home.length > 1 ? (
          <select
            value={port}
            onChange={(e) => setChosen(e.target.value)}
            aria-label={t("ui-ship-pad-choice")}
          >
            {home.map((pad) => (
              <option key={pad.node} value={pad.node}>
                {wordOf(pad.name, pad.room ?? null, need)}
              </option>
            ))}
          </select>
        ) : (
          <span className="note">{wordOf(home[0].name, home[0].room ?? null, need)}</span>
        )}{" "}
        <button
          onClick={() => land(port)}
          disabled={busy || !cost.reachable}
          title={t(cost.reachable ? "ui-ship-land-hint" : "ui-ship-land-short")}
        >
          {t("ui-ship-land")}
        </button>
      </p>
      {marks.length > 0 && (
        <Pads
          planet={vessel.planet}
          book={book}
          clock={clock?.planet === vessel.planet ? clock : undefined}
          world={world}
          marks={marks}
          need={need}
          picked={port}
          onPick={setChosen}
        />
      )}
    </div>
  );
}

/** A pad's word in the list and on hover: its name -- a found node is
 *  nameless (D-321) -- and its room against what the hull needs. */
function wordOf(name: string, room: number | null, need: number | null): string {
  const who = name || t("ui-ship-pad-wild");
  if (room === null) return who;
  const metres = String(Math.round(room));
  const how =
    need !== null && room < need
      ? t("ui-ship-pad-full", { room: metres, need: String(Math.round(need)) })
      : t("ui-ship-pad-room", { room: metres });
  return `${who} · ${how}`;
}

/**
 * The globe under the hull, with the pads on it. The same sphere as the
 * map's and the entry's -- the ground of the relief, north up, turned by
 * the hand, zoomed by the wheel -- drawn in a square of the console at
 * `DISK`, so the browser never saturates a length.
 */
function Pads({
  planet,
  book,
  clock,
  world,
  marks,
  need,
  picked,
  onPick,
}: {
  planet: string;
  book: RecipeBook | null;
  clock: Look["clock"];
  world: WorldMap | null;
  marks: PadMark[];
  /** What the hull needs of a pad's room, square metres; null unknown. */
  need: number | null;
  picked: string;
  onPick: (node: string) => void;
}) {
  const globe = useGlobe({ book, planet, active: true });
  const { eye, radius, lookAt } = globe;
  //: The first frame: over the chosen mark, wide enough for all of them.
  //: Aimed once -- a pick from the list must not spin the planet the hand
  //: has just turned; the mark picked is shown by its colour.
  const aimed = useRef(false);
  //: The frame is measured from the same point it looks at, or the mark it
  //: opens on lands on the edge of a frame sized for somebody else's.
  const [zoom, setZoom] = useState(() =>
    Math.min(
      ZOOM_MAX,
      Math.max(1, (2 * FRAME) / (spreadOf(marks, aimOf(marks, picked), LEAST_DEG, MARGIN) * RAD)),
    ),
  );
  useEffect(() => {
    if (aimed.current) return;
    const at = aimOf(marks, picked);
    if (!at) return;
    aimed.current = true;
    lookAt(at);
  }, [marks, picked, lookAt]);
  //: The canvas as state, not a ref: it exists only once there is an eye
  //: (below), and what hangs on it -- the wheel, the measurement -- must
  //: run when it appears, not when the eye turns.
  const [svg, setSvg] = useState<SVGSVGElement | null>(null);
  //: The canvas's width in pixels, measured drawn and watched: a mark is a
  //: size on the screen, and a size read off an unmounted canvas is a
  //: mark the width of the frame.
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    if (!svg) return;
    const measure = () => setWidth(svg.clientWidth);
    measure();
    const watch = new ResizeObserver(measure);
    watch.observe(svg);
    return () => watch.disconnect();
  }, [svg]);
  const drag = useRef<{ x: number; y: number } | null>(null);
  //: The wheel over the globe is zoom, and only zoom: the page must not
  //: scroll under it. React attaches wheel passively, so the listener is
  //: native.
  useEffect(() => {
    if (!svg) return;
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      setZoom((was) => Math.min(ZOOM_MAX, Math.max(1, was * (e.deltaY < 0 ? NOTCH : 1 / NOTCH))));
    };
    svg.addEventListener("wheel", wheel, { passive: false });
    return () => svg.removeEventListener("wheel", wheel);
  }, [svg]);
  //: The surface about the pads and the ways between, once per map: the
  //: frame redraws on every turn of the hand, and the map is every planet.
  const surface = useMemo(() => surfaceOf(world, planet), [world, planet]);
  const byKey = useMemo(() => new Map(surface.map((node) => [node.key, node])), [surface]);
  const padKeys = useMemo(() => new Set(marks.map((mark) => mark.node)), [marks]);
  const growth = useMemo(() => growthOf(marks, MARK_GROWTH_MAX), [marks]);
  if (!eye || !radius) return null;

  const span = (2 * DISK * FRAME) / zoom;
  //: A pixel in the picture's units, once the canvas is measured; before
  //: that, no mark -- one frame at most, the measurement is taken before
  //: the paint.
  const px = width > 0 ? span / width : 0;
  //: A pixel of the hand in map units: the eye turns by the planet's
  //: measure, not the picture's.
  const unitsPerPixel = px * (radius / DISK);
  const unit = Math.max(FINEST_UNIT, Math.min(1, 1 / zoom));
  const cellUnits = DISK * CELL_DEG * RAD * unit;
  const placed = projectAll(eye, DISK, surface);
  const stood = projectAll(eye, DISK, marks.map((mark) => ({ key: mark.node, place: mark.place })));

  return (
    <div className="pads">
      <svg
        ref={setSvg}
        viewBox={`${-span / 2} ${-span / 2} ${span} ${span}`}
        role="group"
        aria-label={t("ui-ship-pads-label")}
        style={{ "--pc": `var(--planet-${planet})` } as React.CSSProperties}
        onPointerDown={(e) => {
          drag.current = { x: e.clientX, y: e.clientY };
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          const from = drag.current;
          if (!from || !globe.rotate || !unitsPerPixel) return;
          globe.rotate((e.clientX - from.x) * unitsPerPixel, (e.clientY - from.y) * unitsPerPixel);
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
          planet={planet}
          eye={eye}
          radius={DISK}
          book={book}
          clock={clock}
          detailed={span > CELLS_ACROSS * cellUnits}
          coarse={false}
          unit={unit}
          within={span / 2}
        />
        <g className="ways">
          {(world?.edges ?? []).map((edge) => {
            const a = byKey.get(edge.a)?.place;
            const b = byKey.get(edge.b)?.place;
            if (!a || !b || !("lat" in a) || !("lat" in b)) return null;
            const run = arc(eye, DISK, a as Geo, b as Geo);
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
        {px > 0 && (
          <g className="places">
            {surface.map((node) => {
              const at = placed.get(node.key);
              if (!at || padKeys.has(node.key)) return null;
              return <circle key={node.key} className="place" cx={at.x} cy={at.y} r={px * DOT_PX} />;
            })}
          </g>
        )}
        {px > 0 && (
          <g className="marks">
            {marks.map((mark) => {
              const at = stood.get(mark.node);
              if (!at) return null;
              const mine = mark.node === picked;
              const full = need !== null && mark.room !== null && mark.room < need;
              const word = wordOf(mark.name, mark.room, need);
              return (
                <g
                  key={mark.node}
                  className={`pad${mine ? " picked" : ""}${full ? " full" : ""}`}
                  //: A control, reachable without a hand (D-077): the list
                  //: beside the globe names the same pads, and the mark is
                  //: the same choice made on the picture.
                  role="button"
                  tabIndex={0}
                  aria-label={word}
                  aria-pressed={mine}
                  onKeyDown={(e) => {
                    if (e.key !== "Enter" && e.key !== " ") return;
                    e.preventDefault();
                    onPick(mark.node);
                  }}
                  //: A press, not a click: the field takes the pointer to turn
                  //: the globe, and a captured pointer's click is fired at the
                  //: field, not at what was under the finger (`EntryGlobe`).
                  onPointerDown={(e) => {
                    if (e.button !== 0) return;
                    e.stopPropagation();
                    onPick(mark.node);
                  }}
                >
                  <title>{word}</title>
                  <circle
                    cx={at.x}
                    cy={at.y}
                    r={px * MARK_PX * growth(mark.room) * (mine ? PICKED_GROWTH : 1)}
                  />
                </g>
              );
            })}
          </g>
        )}
      </svg>
      <p className="note">{t("ui-ship-pads-hint")}</p>
    </div>
  );
}
