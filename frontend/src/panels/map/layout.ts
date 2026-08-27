// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Where a node is drawn when the server has not said (D-237).
 *
 * Almost never, and less every day: a node gets its place when it is created,
 * and the catching-up seed gives one to everything laid before the rule. What
 * is left is a world mid-deploy and a hull in the sky. So this is a fallback,
 * and it is built to behave like one:
 *
 * * **it settles before anything is shown.** The whole thing is one synchronous
 *   pass, not an animation: the map appears laid out. A simulation running in
 *   `requestAnimationFrame` meant the first second of every opening was nodes
 *   crawling into place, and a click during it landed on whatever had drifted
 *   under the pointer;
 * * **it is the same for everybody.** No clock, no random, no starting point
 *   left over from the last opening: the start comes off the node's key and the
 *   pass is a fixed number of steps. Two players looking at one city see one
 *   picture, and so does the same player tomorrow;
 * * **nothing pulls to the centre and nothing pushes off the walls.** Repulsion
 *   has a radius, so two nodes with no edge between them simply do not know
 *   about each other. Before this, a lone node was pushed to the frame's edge
 *   by soft walls -- as far from everybody as the frame allowed -- and the map
 *   lied about the shape of the world;
 * * **nothing is dragged by hand.** A map that can be rearranged is a map that
 *   is different for the player who rearranged it, which is the whole thing
 *   D-237 forbids.
 */

import type { Link, Point } from "./model";

//: Interface numbers, not world numbers: they do not belong to balance. Kept
//: in step with the server's `MAP_STEP` -- an edge at rest is about as long as
//: the step a placed node stands at, so a half-placed map has one scale.
const REST = 150;
const REST_PER_SECOND = 2;
const REST_EXTRA = 110;
const SPRING = 0.05;
const REPULSE = 7500;
const REPULSE_RADIUS = 250;
const DAMPING = 0.8;
const MAX_STEP = 12;
//: How many passes. Enough for a group of a few dozen to stop moving, and
//: cheap: the whole thing runs once, when the layer is opened.
const PASSES = 300;
//: Below this a pass moves nothing anybody can see: a fiftieth of a pixel.
const SETTLED = 0.02;

/** A seeded starting point: the same node always begins in the same spot. */
function seed(key: string): Point {
  let value = 0;
  for (const ch of key) value = (value * 31 + ch.charCodeAt(0)) % 65_521;
  const angle = (value / 65_521) * Math.PI * 2;
  //: A spiral rather than a ring: a hundred nodes seeded on one circle start
  //: on top of each other, and repulsion has to unpick that before it can do
  //: anything useful.
  const reach = REST * (1 + (value % 7) / 2);
  return { x: Math.cos(angle) * reach, y: Math.sin(angle) * reach };
}

/**
 * Lay out the nodes that have no place, around the ones that have.
 *
 * `fixed` are the places the server gave: they do not move, and everything else
 * settles against them. Returns every key's point -- fixed ones included, so
 * the caller has one place to read a position from.
 */
export function settle(
  keys: readonly string[],
  links: readonly Link[],
  fixed: ReadonlyMap<string, Point>,
): Map<string, Point> {
  const at = new Map<string, Point>();
  const free: string[] = [];
  //: Sorted, so the passes below run in one order for everybody: floating-point
  //: addition is not associative, and two orders drift apart by the last digits.
  for (const key of [...keys].sort()) {
    const given = fixed.get(key);
    if (given) {
      at.set(key, { x: given.x, y: given.y });
    } else {
      at.set(key, seed(key));
      free.push(key);
    }
  }
  if (free.length === 0) return at;

  const speed = new Map(free.map((key) => [key, { x: 0, y: 0 }]));
  for (let pass = 0; pass < PASSES; pass++) {
    let fastest = 0;
    for (const link of links) {
      const one = at.get(link.a);
      const other = at.get(link.b);
      if (!one || !other) continue;
      const rest = REST + Math.min(REST_EXTRA, Math.sqrt(link.seconds) * REST_PER_SECOND);
      const dx = other.x - one.x;
      const dy = other.y - one.y;
      const gap = Math.max(1, Math.hypot(dx, dy));
      const pull = ((gap - rest) * SPRING) / gap;
      const push = speed.get(link.a);
      if (push) {
        push.x += dx * pull;
        push.y += dy * pull;
      }
      const back = speed.get(link.b);
      if (back) {
        back.x -= dx * pull;
        back.y -= dy * pull;
      }
    }
    for (const key of free) {
      const here = at.get(key)!;
      const go = speed.get(key)!;
      for (const [other, there] of at) {
        if (other === key) continue;
        const dx = there.x - here.x;
        const dy = there.y - here.y;
        const gap = Math.max(MAX_STEP, Math.hypot(dx, dy));
        if (gap > REPULSE_RADIUS) continue;
        const force = REPULSE / (gap * gap * gap);
        go.x -= dx * force;
        go.y -= dy * force;
      }
      go.x = Math.max(-MAX_STEP, Math.min(MAX_STEP, go.x * DAMPING));
      go.y = Math.max(-MAX_STEP, Math.min(MAX_STEP, go.y * DAMPING));
      here.x += go.x;
      here.y += go.y;
      fastest = Math.max(fastest, Math.abs(go.x), Math.abs(go.y));
    }
    //: Settled -- the remaining passes would change nothing, and the count
    //: above is only the ceiling for a layout that will not settle at all.
    if (fastest < SETTLED) break;
  }
  return at;
}
