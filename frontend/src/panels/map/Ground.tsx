// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The ground of the globe (D-319, wave 4): the sea, the land in the tone of
 * its climate, the mountains, the rivers, and the night.
 *
 * The relief is asked for once per planet and kept for the session: it is
 * a constant of the world, not a state of it, and the globe under a city
 * does not change while one walks the city. Drawing is `map/relief`'s
 * arithmetic; this file is the fetch and the SVG.
 */

import { useEffect, useMemo, useState } from "react";

import * as api from "../../api";
import type { Look, RecipeBook, Terrain } from "../../api";
import type { Eye } from "./globe";
import { cellPaths, kindAt, nightPath, riverRuns, subsolar, type Warmth } from "./relief";

/** One answer per planet for the life of the page. */
const RELIEF = new Map<string, Promise<Terrain>>();

function reliefOf(planet: string): Promise<Terrain> {
  let asked = RELIEF.get(planet);
  if (!asked) {
    asked = api.terrain(planet).catch((why) => {
      //: A failed fetch is not a fact about the planet: ask again next time.
      RELIEF.delete(planet);
      throw why;
    });
    RELIEF.set(planet, asked);
  }
  return asked;
}

export function useTerrain(planet: string | null): Terrain | null {
  const [terrain, setTerrain] = useState<Terrain | null>(null);
  useEffect(() => {
    let live = true;
    setTerrain(null);
    if (!planet) return;
    reliefOf(planet).then(
      (got) => live && setTerrain(got),
      //: A bare globe is what the player sees; the reason goes to the console.
      (why) => console.warn(`terrain of ${planet}:`, why),
    );
    return () => {
      live = false;
    };
  }, [planet]);
  return terrain;
}

export function Ground({
  planet,
  eye,
  radius,
  book,
  clock,
  detailed,
}: {
  planet: string;
  eye: Eye;
  radius: number;
  book: RecipeBook | null;
  /** The standing planet's clock, with `look`; another planet's day is the book's. */
  clock: Look["clock"];
  /** Whether the frame holds several cells of the relief. Nearer, the one
   *  cell under the eye fills it, and sixteen thousand cells are not laid
   *  for a single flat colour. */
  detailed: boolean;
}) {
  const terrain = useTerrain(planet);
  /** The land's tones: the climate's two bounds, from the vault (D-065).
   *  Null until the book carries them -- and then no ground is drawn at all,
   *  rather than land of a tone made up here. */
  const bands = useMemo<Warmth | null>(() => {
    const bounds = book?.constants?.["biome.bounds"] as Record<string, unknown> | undefined;
    const cold = Number(bounds?.cold_c);
    const cool = Number(bounds?.cool_c);
    return Number.isFinite(cold) && Number.isFinite(cool) ? { cold, cool } : null;
  }, [book]);
  //: The sun as of this render, from the clock: no timer (D-226).
  const dayHours =
    clock?.planet === planet ? clock.day_hours : Number(book?.constants?.[`time.day_${planet}`] ?? 0);
  const sun = subsolar(clock?.epoch ?? null, dayHours, Date.now());
  const paths = useMemo(
    () => (terrain && bands && detailed ? cellPaths(terrain, eye, radius, bands) : null),
    [terrain, eye, radius, bands, detailed],
  );
  const rivers = useMemo(
    () => (terrain && detailed ? riverRuns(terrain, eye, radius) : []),
    [terrain, eye, radius, detailed],
  );
  const under = terrain && bands && !detailed ? kindAt(terrain, eye, bands) : null;
  const night = useMemo(() => (sun ? nightPath(eye, radius, sun) : null), [eye, radius, sun]);
  if (!bands) return null;
  return (
    <g className="ground" style={{ "--pc": `var(--planet-${planet})` } as React.CSSProperties}>
      <circle className="sea" cx={0} cy={0} r={radius} />
      {under && under !== "sea" && (
        <circle
          className={under === "high" || under === "water" ? under : `land ${under}`}
          cx={0}
          cy={0}
          r={radius}
        />
      )}
      {paths && (
        <>
          <path className="land cold" d={paths.land.cold} />
          <path className="land cool" d={paths.land.cool} />
          <path className="land warm" d={paths.land.warm} />
          <path className="high" d={paths.high} />
          <path className="water" d={paths.water} />
          {rivers.map((run, i) => (
            <polyline
              key={i}
              className="river"
              points={run.map((p) => `${p.x},${p.y}`).join(" ")}
            />
          ))}
        </>
      )}
      {night && <path className="night" d={night} />}
    </g>
  );
}
