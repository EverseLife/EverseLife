// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Where a point of the sphere falls on the picture's rasters (D-328).
 *
 * The field is cut into `12 nside**2` cells of equal area -- twelve square
 * faces, `nside` cells a side -- and the rasters travel as those faces laid
 * out as one texture, four across and three down, each with a border of one
 * cell taken from the face over the edge. The shader finds its cell in GLSL
 * (`shade.ts`); this is the same arithmetic for the code that reads the
 * bytes rather than draws them: the vector layer, which walks a lattice of
 * latitude and longitude and asks what stands at each point of it.
 *
 * The lattice stays latitude and longitude on purpose. A line is drawn on
 * the globe, not on the grid, and marching squares wants a quadrilateral
 * mesh; what changed with the grid is only where a sample's *value* comes
 * from. The lattice is cut as fine as the cells are, so nothing is lost:
 * `3 nside` bands of latitude are a cell apart down a meridian, and
 * `6 nside` of longitude a cell apart at the equator.
 */

import type { RasterPassport } from "../../api";

const RAD = Math.PI / 180;
const TAU = 2 * Math.PI;
/** Where the equatorial belt gives way to the polar caps, by the sine of
 *  the latitude: above it Collignon's cut, below it Lambert's. */
const POLAR_Z = 2 / 3;
/** How many bands of the sampling lattice go to one `nside`: a cell of the
 *  grid is `R sqrt(pi/3) / nside` metres, and a band of latitude
 *  `pi R / rows`, so the two are equal at `rows = nside sqrt(3 pi)`, which
 *  is three within a fiftieth. Twice that round the equator. */
export const BANDS_PER_NSIDE = 3;

/** The cell a point falls in. */
export function ang2pix(nside: number, latDeg: number, lonDeg: number): number {
  const z = Math.max(-1, Math.min(1, Math.sin(latDeg * RAD)));
  let phi = (lonDeg * RAD) % TAU;
  if (phi < 0) phi += TAU;
  const za = Math.abs(z);
  const turns = phi / (Math.PI / 2);
  let face: number;
  let ix: number;
  let iy: number;
  if (za <= POLAR_Z) {
    const first = nside * (0.5 + turns);
    const second = nside * z * 0.75;
    const up = Math.floor(first - second);
    const down = Math.floor(first + second);
    const over = Math.floor(up / nside);
    const under = Math.floor(down / nside);
    face = over === under ? (over & 3) + 4 : over < under ? over & 3 : (under & 3) + 8;
    ix = down % nside;
    iy = nside - (up % nside) - 1;
  } else {
    const quarter = Math.min(3, Math.floor(turns));
    const along = turns - quarter;
    const reach = nside * Math.sqrt(Math.max(0, 3 * (1 - za)));
    const up = Math.min(Math.floor(along * reach), nside - 1);
    const down = Math.min(Math.floor((1 - along) * reach), nside - 1);
    if (z >= 0) {
      face = quarter;
      ix = nside - down - 1;
      iy = nside - up - 1;
    } else {
      face = quarter + 8;
      ix = up;
      iy = down;
    }
  }
  return (face * nside + iy) * nside + ix;
}

/** Where a cell sits among the bytes of a raster: the atlas is row by row,
 *  and a face's tile carries its border. */
export function atlasIndex(passport: RasterPassport, cell: number): number {
  const n = passport.nside;
  const side = n + 2 * passport.border;
  const face = Math.floor(cell / (n * n));
  const rest = cell - face * n * n;
  const iy = Math.floor(rest / n);
  const ix = rest - iy * n;
  const row = Math.floor(face / passport.across) * side + passport.border + iy;
  const col = (face % passport.across) * side + passport.border + ix;
  return row * passport.cols + col;
}

/** The lattice of latitude and longitude the vector layer walks, and how a
 *  point of it reaches the rasters. */
export type Lattice = {
  rows: number;
  cols: number;
  /** The place in the rasters of the point at these degrees. */
  at: (lat: number, lon: number) => number;
};

export function latticeOf(passport: RasterPassport): Lattice {
  const nside = passport.nside;
  return {
    rows: BANDS_PER_NSIDE * nside,
    cols: 2 * BANDS_PER_NSIDE * nside,
    at: (lat, lon) => atlasIndex(passport, ang2pix(nside, lat, lon)),
  };
}
