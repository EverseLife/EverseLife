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

/**
 * Where a point of the sphere sits **inside its face**, as a fraction: the
 * face, and (u, v) in [0, nside] with whole cells at halves. A port of the
 * shader's `atlasUV` (`shade.ts`), figure for figure, and it has to be: this
 * is the surface the ground is painted by, and a vector line cut from any
 * other surface runs beside the colour it is meant to edge.
 */
export function faceUV(nside: number, latDeg: number, lonDeg: number): { face: number; u: number; v: number } {
  const z = Math.max(-1, Math.min(1, Math.sin(latDeg * RAD)));
  let phi = (lonDeg * RAD) % TAU;
  if (phi < 0) phi += TAU;
  const za = Math.abs(z);
  const turns = phi / (Math.PI / 2);
  const n = nside;
  if (za <= POLAR_Z) {
    const first = n * (0.5 + turns);
    const second = n * z * 0.75;
    const up = first - second;
    const down = first + second;
    const over = Math.floor(up / n);
    const under = Math.floor(down / n);
    const face = over === under ? (over & 3) + 4 : over < under ? over & 3 : (under & 3) + 8;
    return { face, u: down - n * Math.floor(down / n), v: n - (up - n * Math.floor(up / n)) };
  }
  const quarter = Math.min(3, Math.floor(turns));
  const along = turns - quarter;
  const reach = n * Math.sqrt(Math.max(0, 3 * (1 - za)));
  const a = Math.max(0, Math.min(n, along * reach));
  const b = Math.max(0, Math.min(n, (1 - along) * reach));
  return z >= 0 ? { face: quarter, u: n - b, v: n - a } : { face: quarter + 8, u: a, v: b };
}

/**
 * A quantity read at a point **the way the shader reads it**: the four
 * texels of the atlas about the point, blended by where it stands between
 * their centres -- level nought of a LINEAR texture, in arithmetic. The
 * face's border ring is what the blend reaches into at the edge, exactly as
 * the hardware's does.
 *
 * The coast used to be cut from a reading between the grid's rings of
 * latitude instead. That is a fine surface, and the server reads its nodes
 * by it -- but it is not the shader's, and the two zero-lines ran apart:
 * the owner saw the shore's line beside the water's colour (2026-09-11).
 */
export function atlasBetween(
  passport: RasterPassport,
  raster: ArrayLike<number>,
  latDeg: number,
  lonDeg: number,
): number {
  const { face, u, v } = faceUV(passport.nside, latDeg, lonDeg);
  const side = passport.nside + 2 * passport.border;
  //: Texel centres sit at halves: the texel left of and below the point.
  const x = u - 0.5;
  const y = v - 0.5;
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  const fx = x - x0;
  const fy = y - y0;
  const col0 = (face % passport.across) * side + passport.border + x0;
  const row0 = Math.floor(face / passport.across) * side + passport.border + y0;
  const at = (r: number, c: number) => raster[r * passport.cols + c];
  return (
    (1 - fy) * ((1 - fx) * at(row0, col0) + fx * at(row0, col0 + 1)) +
    fy * ((1 - fx) * at(row0 + 1, col0) + fx * at(row0 + 1, col0 + 1))
  );
}

/**
 * The centre of a cell, in degrees: the inverse of `ang2pix` for the face's
 * own (ix, iy). What a thing that belongs to a **cell** stands on -- a tree
 * of the woods -- rather than on a point of the mesh the eye happens to
 * carry, which slides over the cells as the eye moves (owner, 2026-09-11:
 * the trees jittered as the camera moved).
 */
export function cellCentre(nside: number, cell: number): Geo {
  const n = nside;
  const face = Math.floor(cell / (n * n));
  const rest = cell - face * n * n;
  const iy = Math.floor(rest / n);
  const ix = rest - iy * n;
  //: The ring the cell sits on, counted from the north pole, and its place
  //: along it -- the classic HEALPix figures, from the face's base-pixel
  //: row and column (`JRLL`, `JPLL`).
  const jr = JRLL[face] * n - (ix + iy) - 1;
  let nr: number;
  let z: number;
  let shift: number;
  if (jr < n) {
    nr = jr;
    z = 1 - (nr * nr * 4) / (12 * n * n);
    shift = 0;
  } else if (jr > 3 * n) {
    nr = 4 * n - jr;
    z = -1 + (nr * nr * 4) / (12 * n * n);
    shift = 0;
  } else {
    nr = n;
    z = ((2 * n - jr) * 2) / (3 * n);
    shift = (jr - n) & 1;
  }
  let jp = (JPLL[face] * nr + (ix - iy) + 1 + shift) / 2;
  if (jp > 4 * nr) jp -= 4 * nr;
  if (jp < 1) jp += 4 * nr;
  const phi = ((jp - (shift + 1) / 2) * Math.PI) / (2 * nr);
  let lon = phi / RAD;
  if (lon > 180) lon -= 360;
  return { lat: Math.asin(Math.max(-1, Math.min(1, z))) / RAD, lon };
}
/** The base-pixel row and column of each of the twelve faces (HEALPix). */
const JRLL = [2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4] as const;
const JPLL = [1, 3, 5, 7, 0, 2, 4, 6, 1, 3, 5, 7] as const;

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
  /** The place in the rasters of the point at these degrees: the cell it
   *  stands in, for what the rasters keep as a class. */
  at: (lat: number, lon: number) => number;
  /** A quantity read between the cells around the point, for what they keep
   *  as a number -- the height above all, whose level line is the coast. */
  between: (raster: ArrayLike<number>, lat: number, lon: number) => number;
  /** The same reading for a whole row of one latitude at once, written into
   *  `out` from `at`. Which two rings a point falls between depends on the
   *  latitude alone, so a row settles them once instead of once a sample --
   *  and a row is a thousand samples. */
  row: (
    raster: ArrayLike<number>,
    lat: number,
    lon0: number,
    step: number,
    many: number,
    out: Float32Array,
    at: number,
  ) => void;
};

/** One lattice per fineness: it carries a table of a million entries. */
const LATTICES = new Map<number, Lattice>();
export function latticeOf(passport: RasterPassport): Lattice {
  const held = LATTICES.get(passport.nside);
  if (held) return held;
  const made = madeLattice(passport);
  LATTICES.set(passport.nside, made);
  return made;
}

function madeLattice(passport: RasterPassport): Lattice {
  const nside = passport.nside;
  //: The cells of the grid against the texels of the atlas, laid out once:
  //: it is the same arithmetic for every point and every frame.
  const seats = new Int32Array(12 * nside * nside);
  for (let cell = 0; cell < seats.length; cell++) seats[cell] = atlasIndex(passport, cell);
  //: `between` and `row` read the shader's own surface (`atlasBetween`):
  //: the lines are drawn over the colour, and the two must come off one
  //: surface or they part. A reading between the grid's rings of latitude
  //: -- the server's own surface for its nodes -- used to stand here, and
  //: the coast it cut ran beside the water's colour (owner, 2026-09-11).
  return {
    rows: BANDS_PER_NSIDE * nside,
    cols: 2 * BANDS_PER_NSIDE * nside,
    at: (lat, lon) => seats[ang2pix(nside, lat, lon)],
    between: (raster, lat, lon) => atlasBetween(passport, raster, lat, lon),
    row: (raster, lat, lon0, step, many, out, at) => {
      for (let j = 0; j < many; j++) out[at + j] = atlasBetween(passport, raster, lat, lon0 + j * step);
    },
  };
}
