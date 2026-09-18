// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The height raster's planar coding undone (2026-09-18): the server sends
 * each cell's remainder from the plane through its west, north and
 * north-west neighbours -- the grid's second difference, zigzagged, the low
 * bytes of the whole raster first and the high bytes after -- because that
 * squeezes to about half of the plain heights (`backend/src/api/planar.py`,
 * which says why). Undone here by two running sums, along the rows and down
 * the columns, in sixteen-bit arithmetic that wraps as the server's does:
 * exact for any heights at all.
 *
 * Pure, and pinned to the server's own fixture (`planar.test.ts`,
 * `test_planar.py`).
 */

/** The name the server is asked for the coding by (`raster/height.planar`). */
export const PLANAR = "planar";

/**
 * The heights in metres, `cols` a row, back from their coding: two passes --
 * the zigzag undone with the running sum along each row, then the sum down
 * the columns written out as metres, `unit` a step (the passport's
 * `height_unit_m`). A size the rows do not divide is refused: a count read
 * across the wrong rows is every height wrong and nothing failing.
 */
export function unplanar(coded: ArrayBuffer, cols: number, unit: number): Float32Array {
  const bytes = new Uint8Array(coded);
  const n = bytes.length >> 1;
  if (!(cols > 0) || bytes.length % 2 || n % cols) {
    throw new Error(`planar heights: ${bytes.length} bytes are not rows of ${cols}`);
  }
  //: A sixteen-bit array folds every sum into sixteen bits on its own: the
  //: modulus the server's remainders were taken in.
  const steps = new Int16Array(n);
  for (let row = 0; row < n; row += cols) {
    let west = 0;
    for (let i = row; i < row + cols; i++) {
      const zigzag = bytes[i] | (bytes[n + i] << 8);
      steps[i] = west + ((zigzag >>> 1) ^ -(zigzag & 1));
      west = steps[i];
    }
  }
  const metres = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    if (i >= cols) steps[i] += steps[i - cols];
    metres[i] = steps[i] * unit;
  }
  return metres;
}
