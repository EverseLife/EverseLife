// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The height raster's planar coding undone (2026-09-18): the very bytes the
 * server's encoder makes of its fixture (`backend/tests/test_planar.py`)
 * come back as the heights, and any heights survive the round trip.
 */

import { describe, expect, it } from "vitest";

import { unplanar } from "../panels/map/planar";

//: Three rows of four, the extremes of sixteen bits side by side so the
//: remainders wrap: the same fixture the server's test encodes.
const FIXTURE_HEIGHTS = [0, 5, -3, 32767, -32768, 100, 101, 99, 7, 7, -7, 0];
const FIXTURE_CODED = [
  0, 10, 15, 251, 255, 65, 18, 248, 241, 56, 29, 18,
  0, 0, 0, 255, 255, 255, 0, 255, 255, 255, 0, 0,
];

/** The server's encoder, written again for the round trip: the second
 *  difference, zigzagged, low bytes then high. */
function planar(heights: Int16Array, cols: number): ArrayBuffer {
  const n = heights.length;
  const out = new Uint8Array(2 * n);
  const at = (i: number) => (i >= 0 ? heights[i] : 0);
  for (let i = 0; i < n; i++) {
    const west = i % cols === 0 ? 0 : at(i - 1);
    const north = at(i - cols);
    const northWest = i % cols === 0 ? 0 : at(i - cols - 1);
    const rest = ((heights[i] - west - north + northWest) << 16) >> 16;
    const zigzag = ((rest << 1) ^ (rest >> 15)) & 0xffff;
    out[i] = zigzag & 0xff;
    out[n + i] = zigzag >> 8;
  }
  return out.buffer;
}

describe("the planar coding of the heights", () => {
  it("reads the server's own bytes back as its heights", () => {
    const coded = new Uint8Array(FIXTURE_CODED).buffer;
    expect(Array.from(unplanar(coded, 4, 1))).toEqual(FIXTURE_HEIGHTS);
    //: And writing them again gives the server's bytes: the two sides agree
    //: on the order of the planes and the fold of the sign.
    expect(Array.from(new Uint8Array(planar(Int16Array.from(FIXTURE_HEIGHTS), 4)))).toEqual(FIXTURE_CODED);
  });

  it("is exact for any heights, wrap and all", () => {
    let seed = 7;
    const random = () => {
      seed = (seed * 1103515245 + 12345) & 0x7fffffff;
      return seed;
    };
    for (const [cols, rows] of [[37, 23], [1, 9], [9, 1], [1, 1]]) {
      const heights = Int16Array.from({ length: cols * rows }, (_, i) =>
        i % 5 === 0 ? (random() & 0xffff) - 0x8000 : Math.round(300 * Math.sin(i / 11)) + (random() % 7),
      );
      expect(Array.from(unplanar(planar(heights, cols), cols, 1))).toEqual(Array.from(heights));
    }
  });

  it("hands the heights on in metres, a step the passport's unit", () => {
    //: The raster counts decimetres (`height_unit_m`), and the metres come
    //: out of the unit -- as floats of the same precision the texture takes.
    const coded = new Uint8Array(FIXTURE_CODED).buffer;
    const metres = Float32Array.from(FIXTURE_HEIGHTS, (step) => step * 0.1);
    expect(Array.from(unplanar(coded, 4, 0.1))).toEqual(Array.from(metres));
  });

  it("refuses bytes that are not whole rows", () => {
    const coded = new Uint8Array(FIXTURE_CODED).buffer;
    //: No row width at all would loop for ever; a width the count does not
    //: divide would read every height across the wrong rows.
    expect(() => unplanar(coded, 0, 1)).toThrow();
    expect(() => unplanar(coded, 5, 1)).toThrow();
    expect(() => unplanar(new Uint8Array(7).buffer, 1, 1)).toThrow();
  });
});
