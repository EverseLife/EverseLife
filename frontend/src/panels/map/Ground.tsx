// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The ground of the globe (D-319, wave 4): the sea, the land in the tone of
 * its climate, the mountains, and the night. Not the rivers: on a disk they
 * are hairlines over the tone and read as scratches (owner, 2026-09-06);
 * the field keeps them for the game, the picture does not show them.
 *
 * The relief is asked for once per planet and kept for the session: it is
 * a constant of the world, not a state of it, and the globe under a city
 * does not change while one walks the city. Drawing is `map/relief`'s
 * arithmetic; this file is the fetch and the SVG.
 */

import { useEffect, useId, useMemo, useState } from "react";

import * as api from "../../api";
import type { Look, RecipeBook, Terrain, Tile } from "../../api";
import { diskPath, type Eye } from "./globe";
import { TILE_UNIT } from "./bands";
import {
  COARSE_STRIDE,
  FINE_UNIT,
  cellPaths,
  kindAt,
  nightPath,
  subsolar,
  tileKey,
  tilesAbout,
  type Tiles,
  type Warmth,
} from "./relief";

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

/** The tiles of the local relief held of late, by planet, the `TILE_KEEP`
 *  most recent each -- and the ones on their way, so a tile is asked for
 *  once. A planet's worth of tiles would be a hundred megabytes of parsed
 *  numbers; a frame's worth and a walk's is a few. */
const TILES = new Map<string, Map<string, Tile>>();
const ASKED = new Set<string>();

/** The tiles held for a planet, for a reading of the ground off the map --
 *  the scout's field asks whether a point is land. */
export function tilesHeld(planet: string | null): Tiles | undefined {
  return planet ? TILES.get(planet) : undefined;
}
const TILE_KEEP = 64;

/**
 * The tiles a close frame needs, fetched as the frame comes to need them
 * and kept: the ground redraws as each arrives. Nothing is asked for from
 * afar -- the grid does there -- nor for a frame that would need too many.
 */
export function useTiles(
  planet: string | null,
  terrain: Terrain | null,
  eye: Eye,
  radius: number,
  unit: number,
  within: number | undefined,
): Tiles | undefined {
  const [held, setHeld] = useState(0);
  useEffect(() => {
    if (!planet || !terrain?.tile || unit > TILE_UNIT || within === undefined) return;
    let live = true;
    let own = TILES.get(planet);
    if (!own) {
      own = new Map();
      TILES.set(planet, own);
    }
    const store = own;
    for (const [row, col] of tilesAbout(terrain, eye, radius, within)) {
      const key = `${planet}/${tileKey(row, col)}`;
      if (store.has(tileKey(row, col)) || ASKED.has(key)) continue;
      ASKED.add(key);
      api.terrainTile(planet, row, col).then(
        (tile) => {
          store.set(tileKey(row, col), tile);
          for (const old of store.keys()) {
            if (store.size <= TILE_KEEP) break;
            store.delete(old);
            ASKED.delete(`${planet}/${old}`);
          }
          if (live) setHeld((n) => n + 1);
        },
        (why) => {
          //: Not a fact about the planet: ask again next time.
          ASKED.delete(key);
          console.warn(`tile ${key}:`, why);
        },
      );
    }
    return () => {
      live = false;
    };
  }, [planet, terrain, eye, radius, unit, within]);
  //: A new map each time a tile lands, so that what is memoised on it redraws.
  return useMemo(() => {
    void held;
    const own = planet ? TILES.get(planet) : undefined;
    return own && unit <= TILE_UNIT ? new Map(own) : undefined;
  }, [planet, unit, held]);
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
  coarse,
  fine = false,
  within,
  unit: chosen,
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
  /** Whether the disk is smaller than the frame -- on the approach -- and
   *  the ground is read every third cell. */
  coarse: boolean;
  /** Whether the frame is close enough for the coast's line to show its
   *  corners: then the grid is read at half a cell (`FINE_UNIT`). */
  fine?: boolean;
  /** Half the frame's width in map units, when the frame is a square about
   *  the eye: cells beyond it are not laid. */
  within?: number;
  /** The drawn cell in cells of the grid, when the caller decides it by its
   *  zoom rather than by `coarse`/`fine`: finer the closer, so the cells in
   *  the frame stay about as many. */
  unit?: number;
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
  const unit = chosen ?? (coarse ? COARSE_STRIDE : fine ? FINE_UNIT : 1);
  const tiles = useTiles(planet, terrain, eye, radius, unit, within);
  const paths = useMemo(
    () =>
      terrain && bands && detailed
        ? cellPaths(terrain, eye, radius, bands, unit, within, tiles)
        : null,
    [terrain, eye, radius, bands, detailed, unit, within, tiles],
  );
  const under = terrain && bands && !detailed ? kindAt(terrain, eye, bands, tiles) : null;
  const night = useMemo(() => (sun ? nightPath(eye, radius, sun) : null), [eye, radius, sun]);
  //: The clip's id is this instance's own: a second ground on the page --
  //: the entry screen's beside the map's -- must not share it.
  const clip = `ground-${useId().replace(/[^A-Za-z0-9_-]/g, "")}`;
  if (!bands) return null;
  //: The disk clips the ground: a cell cut by the horizon is drawn out to
  //: the limb along its corners' rays, and what that pushes past the circle
  //: is not the planet.
  //: A path, not a circle: see `diskPath`.
  const disk = diskPath(radius);
  return (
    <g className="ground" style={{ "--pc": `var(--planet-${planet})` } as React.CSSProperties}>
      <clipPath id={clip}>
        <path d={disk} />
      </clipPath>
      <path className="sea" d={disk} />
      <g clipPath={`url(#${clip})`}>
      {under && under !== "sea" && (
        <path className={under === "high" || under === "water" ? under : `land ${under}`} d={disk} />
      )}
      {paths && (
        <>
          <path className="land cold" d={paths.land.cold} />
          <path className="land cool" d={paths.land.cool} />
          <path className="land warm" d={paths.land.warm} />
          <path className="water" d={paths.water} />
          <path className="high" d={paths.high} />
        </>
      )}
      </g>
      {night && <path className="night" d={night} />}
    </g>
  );
}
