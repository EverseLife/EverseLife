// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The season (D-334): where the planet stands on its orbit, and what that
 * does to the sun's latitude and the ground's temperature -- and the snow
 * and the ice the picture lays by it. Cut out of `shade.ts` on 2026-09-12
 * when the file crossed the bar; the law is the engine's (`climate.season_c`)
 * on the book's numbers, and this is its arithmetic for the picture, the
 * probe and the window.
 */

/**
 * The orbit's angle is the sky's own (`useSky`): the phase the world was
 * born at plus the real days gone over the planet's year, in turns. The
 * sun stands over the latitude the tilt's sine by the season's gives, and
 * the mean temperature of a latitude swings by `season.swing_c` at the
 * pole times the sine of the latitude -- the same law the engine reads
 * (`climate.season_c`), off the same numbers of the book.
 */
export type Season = {
  /** Where the planet is on its orbit, in turns from the equinox. */
  turns: number;
  tiltDeg: number;
  swingC: number;
  snowC: number;
  bandC: number;
  iceC: number;
  /** Below this share of the rain scale a cold is a dry cold, and the ground
   *  keeps only `dryKeep` of its snow (season.snow_dry_rain, snow_dry_share). */
  dryRain: number;
  dryKeep: number;
};

/** What the picture draws by when the book has no season: no law at all --
 *  no swing, no tilt, and the snow and the ice lines below any temperature
 *  there is, so nothing is guessed white (the drying law does the same
 *  without a book, `dryLaw`). */
export const SEASON_FALLBACK: Season = {
  turns: 0,
  tiltDeg: 0,
  swingC: 0,
  snowC: -1e9,
  bandC: 1,
  iceC: -1e9,
  dryRain: 0,
  dryKeep: 1,
};

const MS_PER_REAL_DAY = 86_400_000;

/** The planet's place on its orbit at the moment, in turns: the world's
 *  epoch and the book's `orbit.phase`/`orbit.period_days`. Without an
 *  epoch the world stands at its birth, which is the phase (`useSky` puts
 *  the planet there too); without a year, at the equinox. */
export function orbitTurns(
  constants: Record<string, unknown> | null | undefined,
  planet: string,
  epoch: string | null,
  nowMs: number,
): number {
  const periods = (constants?.["orbit.period_days"] ?? {}) as Record<string, number>;
  const phases = (constants?.["orbit.phase"] ?? {}) as Record<string, number>;
  const period = Number(periods[planet] ?? 0);
  const birth = Number(phases[planet] ?? 0) / (2 * Math.PI);
  const days = epoch && period > 0 ? (nowMs - new Date(epoch).getTime()) / MS_PER_REAL_DAY : 0;
  const turns = birth + (period > 0 ? days / period : 0);
  return ((turns % 1) + 1) % 1;
}

/** The season of a planet as of the moment, off the book. */
export function seasonOf(
  constants: Record<string, unknown> | null | undefined,
  planet: string,
  epoch: string | null,
  nowMs: number,
): Season {
  const of = (key: string) => {
    const table = (constants?.[key] ?? {}) as Record<string, number>;
    return Number(table[planet] ?? 0);
  };
  const num = (key: string, fallback: number) => {
    const value = Number(constants?.[key]);
    return Number.isFinite(value) ? value : fallback;
  };
  //: The band no narrower than a thousandth of a degree: the fragment
  //: divides by it, and a nought would be a blend of nothing.
  return {
    turns: orbitTurns(constants, planet, epoch, nowMs),
    tiltDeg: of("season.tilt_deg"),
    swingC: of("season.swing_c"),
    snowC: num("season.snow_c", SEASON_FALLBACK.snowC),
    bandC: Math.max(num("season.snow_band_c", SEASON_FALLBACK.bandC), 1e-3),
    iceC: num("season.ice_c", SEASON_FALLBACK.iceC),
    dryRain: num("season.snow_dry_rain", SEASON_FALLBACK.dryRain * 100) / 100,
    dryKeep: num("season.snow_dry_share", SEASON_FALLBACK.dryKeep * 100) / 100,
  };
}

/** The season's offset of the mean temperature at a latitude, degrees. */
export function seasonC(season: Season, latDeg: number): number {
  return season.swingC * Math.sin((latDeg * Math.PI) / 180) * Math.sin(2 * Math.PI * season.turns);
}

/** The latitude the sun stands over, degrees: the tilt at midsummer. */
export function subsolarLat(season: Season): number {
  const tilt = (season.tiltDeg * Math.PI) / 180;
  return (Math.asin(Math.sin(tilt) * Math.sin(2 * Math.PI * season.turns)) * 180) / Math.PI;
}

/** The snow's and the sea ice's tones (the picture's): a white a little
 *  blue, and the ice a little bluer and darker, so a frozen lake still
 *  reads as a lake under the snow round it. */
export const SNOW_TONE: readonly [number, number, number] = [0.92, 0.94, 0.97];
export const ICE_TONE: readonly [number, number, number] = [0.78, 0.86, 0.94];
/** How much of the grain the snow covers: not all, so the ground's kind
 *  still shows through a thin cover. */
export const SNOW_OVER_GRAIN = 0.7;
