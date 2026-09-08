// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** How the camera follows a body on a globe (`panels/map/follow.ts`, D-238). */

import { describe, expect, it } from "vitest";

import { firstOnGlobe, needsTurn, HALF_PIXEL } from "../panels/map/follow";
import type { Geo } from "../panels/map/globe";

const here: Geo = { lat: 41, lon: 24 };
const there: Geo = { lat: -3, lon: 100 };

describe("where the tether turns to", () => {
  //: The tether asked one question -- "where is my node among the drawn?" --
  //: and a body whose node the scene does not draw got no answer at all: a
  //: closed city too small to draw, a scene showing the city and not the
  //: flat inside it. The frame was then slid instead, and a frame whose
  //: origin is the eye does not move (owner, 2026-09-08).
  it("takes the first candidate that is a point of a sphere", () => {
    expect(firstOnGlobe([here, there])).toBe(here);
    expect(firstOnGlobe([undefined, null, there])).toBe(there);
  });

  it("passes over the flat and the missing rather than turning to them", () => {
    //: A room and a hull's own layout are laid out in x and y: there is no
    //: latitude to turn a planet to.
    expect(firstOnGlobe([{ x: 3, y: 4 }, here])).toBe(here);
    expect(firstOnGlobe([undefined, { x: 0, y: 0 }, null])).toBeNull();
    expect(firstOnGlobe([])).toBeNull();
  });
});

describe("when the walking dot is worth a turn", () => {
  const eye: Geo = here;

  it("turns once the dot has strayed half a pixel", () => {
    expect(needsTurn({ dot: { x: 0, y: 0 }, scale: 1, eye, corrected: null })).toBe(false);
    expect(needsTurn({ dot: { x: 0.4, y: 0 }, scale: 1, eye, corrected: null })).toBe(false);
    expect(needsTurn({ dot: { x: HALF_PIXEL, y: 0 }, scale: 1, eye, corrected: null })).toBe(true);
    //: The stray is judged in pixels, so the same units far out are nothing.
    expect(needsTurn({ dot: { x: 100, y: 0 }, scale: 0.001, eye, corrected: null })).toBe(false);
  });

  it("turns once per eye, however many frames ask", () => {
    //: The dot is projected with the eye of the last render, and a turn is a
    //: state change that lands a frame or more later. Asking again against
    //: the same eye adds a second correction to the first -- the overshoot
    //: that shook the frame while walking.
    const dot = { x: 40, y: 0 };
    expect(needsTurn({ dot, scale: 1, eye, corrected: null })).toBe(true);
    expect(needsTurn({ dot, scale: 1, eye, corrected: eye })).toBe(false);
    //: And the moment the render lands a new eye, the question is open again.
    expect(needsTurn({ dot, scale: 1, eye: there, corrected: eye })).toBe(true);
  });
});
