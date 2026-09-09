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
  /** The place in the rasters of the point at these degrees: the cell it
   *  stands in, for what the rasters keep as a class. */
  at: (lat: number, lon: number) => number;
  /** A quantity read between the cells around the point, for what they keep
   *  as a number -- the height above all, whose level line is the coast. */
  between: (raster: ArrayLike<number>, lat: number, lon: number) => number;
};

export function latticeOf(passport: RasterPassport): Lattice {
  const nside = passport.nside;
  const rings = ringsOf(passport);
  //: The rings hold cells of the grid, the rasters hold texels of the
  //: atlas, and this is one in terms of the other. Laid out once: it is the
  //: same arithmetic for every point and every frame.
  const seats = new Int32Array(12 * nside * nside);
  for (let cell = 0; cell < seats.length; cell++) seats[cell] = atlasIndex(passport, cell);
  return {
    rows: BANDS_PER_NSIDE * nside,
    cols: 2 * BANDS_PER_NSIDE * nside,
    at: (lat, lon) => seats[ang2pix(nside, lat, lon)],
    between: (raster, lat, lon) => rings.between((cell) => raster[seats[cell]], lat, lon),
  };
}

/** The rings of a planet, made once and kept: the table is four megabytes
 *  and every frame of the vector layer reads it. */
const RINGS = new Map<number, Rings>();
export function ringsOf(passport: RasterPassport): Rings {
  let held = RINGS.get(passport.nside);
  if (!held) RINGS.set(passport.nside, (held = new Rings(passport.nside)));
  return held;
}

/** The twelve faces by the ring they start on and where they stand round
 *  it -- the tables every HEALPix implementation carries. */
const RING_OF_FACE = [2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4];
const PLACE_OF_FACE = [1, 3, 5, 7, 0, 2, 4, 6, 1, 3, 5, 7];

/** How many cells a ring holds, as a quarter of them, and whether it is the
 *  half-step-shifted kind. */
function ringShape(nside: number, ring: number): [number, number] {
  if (ring < nside) return [ring, 0];
  if (ring > 3 * nside) return [4 * nside - ring, 0];
  return [nside, (ring - nside) & 1];
}

/** Where a latitude stands among the rings, as a fraction: whole numbers
 *  are the middles of rings, counted from the north. */
function ringOf(nside: number, latDeg: number): number {
  const z = Math.max(-1, Math.min(1, Math.sin(latDeg * RAD)));
  if (Math.abs(z) <= POLAR_Z) return 2 * nside - 1.5 * nside * z;
  if (z > 0) return nside * Math.sqrt(Math.max(0, 3 * (1 - z)));
  return 4 * nside - nside * Math.sqrt(Math.max(0, 3 * (1 + z)));
}

/**
 * The cells of a planet laid out by ring of equal latitude and by place
 * along it, and a quantity read **between** them.
 *
 * This is what the equal-area grid has instead of the four corners of a
 * square, and the vector layer needs it for the same reason the shader
 * does. A height read as the cell's own value is a field of steps, and the
 * level line of a field of steps runs along the edges of cells: the coast
 * came out as a chain of straight runs at forty-five degrees, which is the
 * shape of a cell and not the shape of a shore.
 */
export class Rings {
  private laid: Int32Array | null = null;

  constructor(readonly nside: number) {}

  get count(): number {
    return 4 * this.nside - 1;
  }

  get wide(): number {
    return 4 * this.nside;
  }

  /** `(rings, 4 nside)` cells. Built by walking the cells and putting each
   *  in its ring -- integer arithmetic, no projection: asking the
   *  projection for the middle of every place is a million calls and a
   *  quarter of a second, and this is ten milliseconds. */
  get table(): Int32Array {
    if (this.laid) return this.laid;
    const n = this.nside;
    const table = new Int32Array(this.count * this.wide);
    for (let pix = 0; pix < 12 * n * n; pix++) {
      const face = Math.floor(pix / (n * n));
      const rest = pix - face * n * n;
      const iy = Math.floor(rest / n);
      const ix = rest - iy * n;
      const ring = RING_OF_FACE[face] * n - ix - iy - 1;
      const [quarter, shifted] = ringShape(n, ring);
      const place = Math.floor((PLACE_OF_FACE[face] * quarter + ix - iy + 1 + shifted) / 2);
      const len = 4 * quarter;
      table[(ring - 1) * this.wide + ((((place - 1) % len) + len) % len)] = pix;
    }
    //: A short ring repeats along the width, so a place past its end is the
    //: ring come round rather than a hole.
    for (let ring = 1; ring <= this.count; ring++) {
      const len = 4 * ringShape(n, ring)[0];
      for (let seat = len; seat < this.wide; seat++) {
        table[(ring - 1) * this.wide + seat] = table[(ring - 1) * this.wide + (seat % len)];
      }
    }
    this.laid = table;
    return table;
  }

  /** A quantity read between the two rings around a point and the two
   *  places along each: the same reading the server makes (`healpix.Rings`)
   *  and the same surface the shader samples. */
  between(read: (cell: number) => number, latDeg: number, lonDeg: number): number {
    const n = this.nside;
    const table = this.table;
    const fraction = Math.max(1, Math.min(this.count, ringOf(n, latDeg)));
    const low = Math.min(Math.floor(fraction), this.count - 1);
    const down = fraction - low;
    let phi = (lonDeg * RAD) % TAU;
    if (phi < 0) phi += TAU;
    let out = 0;
    for (let k = 0; k < 2; k++) {
      const weight = k === 0 ? 1 - down : down;
      if (weight === 0) continue;
      const ring = low + k;
      const [quarter, shifted] = ringShape(n, ring);
      const along = phi * ((2 * quarter) / Math.PI) - 0.5 + shifted / 2;
      const first = Math.floor(along);
      const across = along - first;
      const len = 4 * quarter;
      const row = (ring - 1) * this.wide;
      const one = ((first % len) + len) % len;
      const two = (one + 1) % len;
      out += weight * ((1 - across) * read(table[row + one]) + across * read(table[row + two]));
    }
    return out;
  }
}
