// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The atlas laid out again with a wide border (D-331 addendum, 2026-09-12).
 *
 * The rasters come from the server as twelve face tiles with a border of
 * one cell taken from the face over the edge (`engine/rasters.py`), which
 * is what the finest level of a texture needs and no more: a coarser level
 * has a texel of many cells, its border is that one cell shrunk to
 * nothing, and a read blended near the edge of a face reached into the
 * tile beside it in the atlas -- another face entirely, laid there by the
 * atlas and not by the sphere. The lie of the land, read three levels
 * coarser, and the shadow's far stretches both drew a line along every
 * seam; at the pole four seams meet.
 *
 * So the client lays the atlas out again before it goes to the GPU: the
 * same faces, the same order, each tile grown to the next power of two
 * (512 for a face of 254 cells) and the room round the face filled by the
 * server's own rule, carried a long way -- the edge cell carried outwards
 * over its inward neighbour along the great circle, so many steps, and
 * the projection asked whose cell that is (`healpix.ang2pix`). Whatever
 * face lies across the edge and however its lattice runs, the first cells
 * out are exactly right, as the server's border is, and the far ones are
 * where a straight line on the sphere leads, which is where a coarse read
 * expects them within its own texel. A power of two, so that every level's
 * texels align with the tiles and no texel straddles two faces; and the
 * border being 129 cells, a texel up to level seven stays inside its own
 * tile with its blend.
 *
 * Only the picture is laid out this way. The vector layer, the probe and
 * the figures read the rasters as they came, by the passport as it came.
 */

import type { RasterPassport } from "../../api";
import type { Geo } from "./globe";
import { ang2pix, atlasIndex, cellCentre } from "./healpix";

const RAD = Math.PI / 180;

/** Within this many cells of the edge every border texel is carried on
 *  its own; farther out one carry serves a block of this many texels a
 *  side, aligned to the tile so a level up to the block's own reads it
 *  whole. Level three's texel is eight cells, and a level's blend reaches
 *  half a texel past the face: what levels nought to four read is exact. */
export const FINE_CELLS = 8;

export type Widened = {
  /** The passport of the picture's own layout: the border and the size. */
  passport: RasterPassport;
  /** For every texel of the new atlas, the texel of the old one whose
   *  value goes in it; null when the atlas is already as wide as it gets. */
  map: Int32Array | null;
};

/** The least border a tile is grown to hold, cells: half a texel of level
 *  seven, which is as coarse as any frame reads the ground (the globe a
 *  few hundred pixels across). A face of 254 cells then gets 129. */
export const BORDER_MIN = 64;

/** The tile side that holds the face and at least BORDER_MIN each side,
 *  the next power of two: mip levels halve it whole, so no texel of any
 *  level straddles two tiles. */
export function wideSide(nside: number): number {
  let side = 1;
  while (side < nside + 2 * BORDER_MIN) side *= 2;
  return side;
}

function xyzOf(geo: Geo): [number, number, number] {
  const lat = geo.lat * RAD;
  const lon = geo.lon * RAD;
  return [Math.cos(lat) * Math.cos(lon), Math.cos(lat) * Math.sin(lon), Math.sin(lat)];
}

function geoOf(p: [number, number, number]): Geo {
  const len = Math.hypot(p[0], p[1], p[2]) || 1;
  return {
    lat: Math.asin(Math.max(-1, Math.min(1, p[2] / len))) / RAD,
    lon: Math.atan2(p[1], p[0]) / RAD,
  };
}

/** The atlas laid out with the wide border: the new passport and the
 *  texel map, or the passport as it came when its border is that already. */
export function widen(passport: RasterPassport): Widened {
  const n = passport.nside;
  const side = wideSide(n);
  const border = (side - n) / 2;
  if (border === passport.border) return { passport, map: null };
  const across = passport.across;
  const down = Math.ceil(12 / across);
  const cols = across * side;
  const rows = down * side;
  const map = new Int32Array(cols * rows).fill(-1);
  const cellOf = (face: number, ix: number, iy: number) => (face * n + iy) * n + ix;
  //: The centres of the cells the carries start from, kept once each: the
  //: edge cells and the ones inward of them, a few thousand a face.
  const centres = new Map<number, [number, number, number]>();
  const centreOf = (cell: number) => {
    let at = centres.get(cell);
    if (!at) {
      at = xyzOf(cellCentre(n, cell));
      centres.set(cell, at);
    }
    return at;
  };
  const clampCell = (at: number) => Math.max(0, Math.min(n - 1, at));
  //: The server's carry, so many steps: the edge cell E and the cell
  //: inward of it along each axis give the lattice's own steps on the
  //: sphere; out by kx of one and ky of the other along the great circle
  //: of their sum.
  const carry = (face: number, px: number, py: number): number => {
    const cx = clampCell(px);
    const cy = clampCell(py);
    const dx = Math.sign(px - cx);
    const dy = Math.sign(py - cy);
    const kx = Math.abs(px - cx);
    const ky = Math.abs(py - cy);
    const edge = centreOf(cellOf(face, cx, cy));
    const d: [number, number, number] = [0, 0, 0];
    if (dx !== 0) {
      const inward = centreOf(cellOf(face, clampCell(cx - dx), cy));
      for (let i = 0; i < 3; i++) d[i] += kx * (edge[i] - inward[i]);
    }
    if (dy !== 0) {
      const inward = centreOf(cellOf(face, cx, clampCell(cy - dy)));
      for (let i = 0; i < 3; i++) d[i] += ky * (edge[i] - inward[i]);
    }
    //: Tangent to the ball at the edge cell, then along the great circle
    //: by the length of the step -- the tangent's own length is the arc,
    //: a cell being a small angle.
    const along = d[0] * edge[0] + d[1] * edge[1] + d[2] * edge[2];
    for (let i = 0; i < 3; i++) d[i] -= along * edge[i];
    const arc = Math.hypot(d[0], d[1], d[2]);
    if (arc === 0) return atlasIndex(passport, cellOf(face, cx, cy));
    const p: [number, number, number] = [0, 0, 0];
    for (let i = 0; i < 3; i++) p[i] = edge[i] * Math.cos(arc) + (d[i] / arc) * Math.sin(arc);
    const geo = geoOf(p);
    return atlasIndex(passport, ang2pix(n, geo.lat, geo.lon));
  };
  const blocks = new Map<number, number>();
  for (let face = 0; face < 12; face++) {
    const c0 = (face % across) * side;
    const r0 = Math.floor(face / across) * side;
    blocks.clear();
    for (let ty = 0; ty < side; ty++) {
      const py = ty - border;
      for (let tx = 0; tx < side; tx++) {
        const px = tx - border;
        const at = (r0 + ty) * cols + c0 + tx;
        if (px >= 0 && px < n && py >= 0 && py < n) {
          map[at] = atlasIndex(passport, cellOf(face, px, py));
          continue;
        }
        const out = Math.max(
          px < 0 ? -px : px >= n ? px - n + 1 : 0,
          py < 0 ? -py : py >= n ? py - n + 1 : 0,
        );
        if (out <= FINE_CELLS) {
          map[at] = carry(face, px, py);
          continue;
        }
        //: One carry for the block, from its middle texel: aligned to the
        //: tile, so the block is whole texels of every level up to its own.
        const bx = Math.floor(tx / FINE_CELLS) * FINE_CELLS + FINE_CELLS / 2;
        const by = Math.floor(ty / FINE_CELLS) * FINE_CELLS + FINE_CELLS / 2;
        const key = by * side + bx;
        let source = blocks.get(key);
        if (source === undefined) {
          source = carry(face, bx - border, by - border);
          blocks.set(key, source);
        }
        map[at] = source;
      }
    }
  }
  return { passport: { ...passport, border, cols, rows }, map };
}

/** A raster laid out by the map: the same kind of array, the new size. */
export function retile<T extends Float32Array | Uint8Array>(map: Int32Array, source: T): T {
  const out = new (source.constructor as new (length: number) => T)(map.length);
  for (let i = 0; i < map.length; i++) {
    const from = map[i];
    out[i] = from >= 0 ? source[from] : 0;
  }
  return out;
}
