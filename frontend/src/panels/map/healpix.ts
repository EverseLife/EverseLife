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

/** One lattice per fineness: it carries a table of a million entries, and
 *  it holds the rings, which are laid out as they are read. */
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
    row: (raster, lat, lon0, step, many, out, at) =>
      rings.row(raster, seats, lat, lon0, step, many, out, at),
  };
}

/** The rings of a planet, made once and kept: every frame of the vector
 *  layer reads them, and a ring is laid out when it is first read. */
const RINGS = new Map<number, Rings>();
export function ringsOf(passport: RasterPassport): Rings {
  let held = RINGS.get(passport.nside);
  if (!held) RINGS.set(passport.nside, (held = new Rings(passport.nside)));
  return held;
}

/** How many cells a ring holds, as a quarter of them, and whether it is the
 *  half-step-shifted kind. */
function ringShape(nside: number, ring: number): [number, number] {
  if (ring < nside) return [ring, 0];
  if (ring > 3 * nside) return [4 * nside - ring, 0];
  return [nside, (ring - nside) & 1];
}

/** The latitude of a ring's cells, degrees. */
function ringLat(nside: number, ring: number): number {
  const three = 3 * nside * nside;
  const z =
    ring < nside
      ? 1 - (ring * ring) / three
      : ring > 3 * nside
        ? ((4 * nside - ring) * (4 * nside - ring)) / three - 1
        : ((2 * nside - ring) * 2) / (3 * nside);
  return (Math.asin(Math.max(-1, Math.min(1, z))) * 180) / Math.PI;
}

/** The longitude of a place in a ring, degrees. Places run east and wrap. */
function ringLon(quarter: number, shifted: number, place: number): number {
  const phi = ((place + 0.5 - shifted / 2) * (Math.PI / 2)) / quarter;
  return (((phi * 180) / Math.PI + 180) % 360) - 180;
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
  /** The rings already laid out, by ring number. A frame touches a few
   *  dozen of a planet's `4 nside - 1` -- a thousand on Terra -- and all of
   *  them together weigh what one solid table would. */
  private laid = new Map<number, Int32Array>();
  readonly nside: number;

  constructor(nside: number) {
    this.nside = nside;
  }

  get count(): number {
    return 4 * this.nside - 1;
  }

  get wide(): number {
    return 4 * this.nside;
  }

  /**
   * One ring: `4 nside` cells by place along it, and a short ring repeated
   * along the width so a place past its end is the ring come round.
   *
   * Laid out when the ring is first read and not before. Walking every cell
   * of the planet to put it in its ring is the shorter arithmetic per ring,
   * but it is four hundred milliseconds of it before a single line can be
   * drawn, and a frame touches a few dozen of a planet's thousand. Asking
   * the projection for the middle of every place of one ring is a fifth of
   * a millisecond, and it is asked for the rings that are looked at.
   */
  ring(jr: number): Int32Array {
    const held = this.laid.get(jr);
    if (held) return held;
    const n = this.nside;
    const [quarter, shifted] = ringShape(n, jr);
    const lat = ringLat(n, jr);
    const len = 4 * quarter;
    const row = new Int32Array(this.wide);
    for (let seat = 0; seat < this.wide; seat++) {
      const place = seat % len;
      row[seat] = seat < len ? ang2pix(n, lat, ringLon(quarter, shifted, place)) : row[place];
    }
    this.laid.set(jr, row);
    return row;
  }


  /** A quantity read between the two rings around a point and the two
   *  places along each: the same reading the server makes (`healpix.Rings`)
   *  and the same surface the shader samples. */
  between(read: (cell: number) => number, latDeg: number, lonDeg: number): number {
    const n = this.nside;
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
      const row = this.ring(ring);
      const one = ((first % len) + len) % len;
      const two = (one + 1) % len;
      out += weight * ((1 - across) * read(row[one]) + across * read(row[two]));
    }
    return out;
  }

  /**
   * A whole row of one latitude, read between the cells and written out.
   *
   * The two rings a point falls between, and how it stands between them,
   * are the latitude's business alone; only the place along each ring
   * moves with the longitude. Settled once for the row, the reading of a
   * sample is two lookups and two multiplies -- and the vector layer reads
   * a million of them for a planet and forty thousand for every turn of
   * the eye, so the difference is the map moving under the hand or not.
   */
  row(
    raster: ArrayLike<number>,
    seats: Int32Array,
    latDeg: number,
    lon0: number,
    step: number,
    many: number,
    out: Float32Array,
    at: number,
  ): void {
    const n = this.nside;
    const fraction = Math.max(1, Math.min(this.count, ringOf(n, latDeg)));
    const low = Math.min(Math.floor(fraction), this.count - 1);
    const down = fraction - low;
    //: The two rings, each with its own length, its own half-step shift and
    //: its own share of the answer.
    const rows = [this.ring(low), this.ring(low + 1)];
    const weights = [1 - down, down];
    const scales = [0, 0];
    const shifts = [0, 0];
    const lengths = [0, 0];
    for (let k = 0; k < 2; k++) {
      const ring = low + k;
      const quarter = ring < n ? ring : ring > 3 * n ? 4 * n - ring : n;
      const shifted = ring < n || ring > 3 * n ? 0 : (ring - n) & 1;
      scales[k] = (2 * quarter) / Math.PI;
      shifts[k] = shifted / 2 - 0.5;
      lengths[k] = 4 * quarter;
    }
    for (let j = 0; j < many; j++) {
      let phi = ((lon0 + j * step) * RAD) % TAU;
      if (phi < 0) phi += TAU;
      let sum = 0;
      for (let k = 0; k < 2; k++) {
        const weight = weights[k];
        if (weight === 0) continue;
        const along = phi * scales[k] + shifts[k];
        const first = Math.floor(along);
        const across = along - first;
        const len = lengths[k];
        const row = rows[k];
        const one = ((first % len) + len) % len;
        const two = one + 1 === len ? 0 : one + 1;
        const a = raster[seats[row[one]]];
        const b = raster[seats[row[two]]];
        sum += weight * (a + (b - a) * across);
      }
      out[at + j] = sum;
    }
  }
}
