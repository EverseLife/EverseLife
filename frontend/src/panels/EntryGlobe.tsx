// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The globe before the world (D-319, D-013): one half of the login and
 * registration screens is the planet itself, from the public map.
 *
 * The same globe as the map's -- the true sphere, the ground of the relief,
 * north up, turned by the hand and zoomed by its buttons or Ctrl+wheel --
 * with what the
 * public map shows to everybody: the cities and the ways between them, as
 * of the delayed snapshot (D-319 item 7). There is no session yet, so the
 * book is read from the public catalog and there is no clock, hence no
 * night. At the last step of registration the doors -- the printers a
 * newcomer may be printed at -- appear as marks; a mark chosen names the
 * door whose card the other half shows.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import * as api from "../api";
import type { Door, MapNode, RecipeBook, WorldMap } from "../api";
import { t } from "../locale";
import { Ground } from "./map/Ground";
import { arc, projectAll, type Geo } from "./map/globe";
import { useGlobe } from "./map/useGlobe";

/** How much of the frame the disk takes at the outermost zoom. */
const FRAME = 1.15;
/** How far in the zoom may go, and one notch of it. */
const ZOOM_MAX = 400;
const NOTCH = 1.25;
/** Sizes in shares of the frame: a node's dot, a door's mark, a label. */
const DOT = 1 / 300;
const MARK = 1 / 60;
const LABEL = 1 / 36;
/** Below this many cells across the frame the ground is one flat colour. */
const CELLS_ACROSS = 1.5;
/** From this zoom on the plain nodes are drawn, and from this one the
 *  cities' names: farther out they are a smudge, not a map. */
const NODES_ZOOM = 2;
const LABELS_ZOOM = 4;
/** From this zoom on the ground is read between the grid's cells. */
const FINE_ZOOM = 2;
/** The relief's cell, degrees. */
const CELL_DEG = 2;
const EQUATOR: Geo = { lat: 0, lon: 0 };

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
  const [planet, setPlanet] = useState<string | null>(null);
  //: Shown: the chosen planet, else the one with doors, else the first of the sky.
  const shown =
    planet ?? doors?.find((door) => door.place)?.planet ?? planets[0]?.planet ?? null;
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
  const drag = useRef<{ x: number; y: number } | null>(null);
  //: The page scrolls over the globe as anywhere: the wheel zooms only
  //: with Ctrl or Cmd held, the way maps in pages do, and then -- and only
  //: then -- the page must not scroll under it. React attaches wheel
  //: passively, so the listener is native.
  useEffect(() => {
    const field = svg.current;
    if (!field) return;
    const wheel = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      setZoom((was) => Math.min(ZOOM_MAX, Math.max(1, was * (e.deltaY < 0 ? NOTCH : 1 / NOTCH))));
    };
    field.addEventListener("wheel", wheel, { passive: false });
    return () => field.removeEventListener("wheel", wheel);
  }, [shown, eye, radius]);
  const nearer = () => setZoom((was) => Math.min(ZOOM_MAX, was * NOTCH));
  const farther = () => setZoom((was) => Math.max(1, was / NOTCH));
  if (!shown || !eye || !radius) {
    return <div className="entry-globe" aria-hidden="true" />;
  }
  const span = (2 * radius * FRAME) / zoom;
  const unitsPerPixel = () => span / (svg.current?.clientWidth || 1);
  const cellUnits = radius * ((CELL_DEG * Math.PI) / 180);
  const detailed = span > CELLS_ACROSS * cellUnits;
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
      {planets.length > 1 && (
        <div className="row tabs">
          {planets.map((sphere) => (
            <button
              key={sphere.key}
              className={sphere.planet === shown ? "" : "quiet"}
              aria-pressed={sphere.planet === shown}
              onClick={() => setPlanet(sphere.planet)}
            >
              {sphere.name}
            </button>
          ))}
        </div>
      )}
      <svg
        ref={svg}
        viewBox={`${-span / 2} ${-span / 2} ${span} ${span}`}
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
          radius={radius}
          book={book}
          clock={undefined}
          detailed={detailed}
          coarse={false}
          fine={zoom >= FINE_ZOOM}
          within={span / 2}
        />
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
                onClick={() => onPick(door.node)}
              >
                <title>{door.city ? `${door.name} · ${door.city}` : door.name}</title>
                <circle cx={at.x} cy={at.y} r={span * MARK * (mine ? 1.4 : 1)} />
              </g>
            );
          })}
        </g>
      </svg>
      <div className="row zoom">
        <button className="quiet" aria-label={t("ui-zoom-in")} onClick={nearer} disabled={zoom >= ZOOM_MAX}>
          +
        </button>
        <button className="quiet" aria-label={t("ui-zoom-out")} onClick={farther} disabled={zoom <= 1}>
          −
        </button>
      </div>
      {doors && <p className="note center">{t("ui-entry-globe-doors-hint")}</p>}
    </div>
  );
}
