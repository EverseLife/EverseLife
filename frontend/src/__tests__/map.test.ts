// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/** The map's pure parts: what is shown around you, and where it is drawn (D-237). */

import { describe, expect, it } from "vitest";

import type { MapNode, Transit } from "../api";
import {
  ARRIVED,
  CHASE_TAU,
  LONGEST_STEP,
  arrived,
  chase,
  createCamera,
  frameOn,
  type Frame,
} from "../panels/map/camera";
import { clampScale, lensOn, pinchScale, pinchTo, worldAt } from "../panels/map/hand";
import { settle } from "../panels/map/layout";
import {
  DEPTH,
  H,
  W,
  delegate,
  homeCity,
  journeyOf,
  nearby,
  offworld,
  sceneKey,
  type Link,
  type Point,
} from "../panels/map/model";
import { along, forecast, mooring, term, windowOpen } from "../panels/map/orbits";
import { long, price, spread } from "../panels/map/words";
import { DEFAULT_LOCALE, Words, learn } from "../locale";

//: The terms below are assembled from the client's own locale (D-251), which
//: ships with the build rather than over the wire -- so an empty bundle is
//: still a complete one here, and `spread(40, 120)` comes out in words.
learn(new Words({ locale: DEFAULT_LOCALE, locales: [DEFAULT_LOCALE], ftl: "" }, null));

const chain = (n: number): Link[] =>
  Array.from({ length: n - 1 }, (_, i) => ({
    a: `n${i}`,
    b: `n${i + 1}`,
    surface: "road",
    seconds: 60,
  }));

const keys = (n: number) => Array.from({ length: n }, (_, i) => `n${i}`);

const node = (over: Partial<MapNode>): MapNode =>
  ({
    key: "x",
    name: "Узел",
    layer: "city",
    parent: null,
    exit: false,
    port: false,
    planet: "terra",
    orbit: null,
    deferred: false,
    aboard: false,
    flight: null,
    ...over,
  }) as MapNode;

describe("nearby", () => {
  it("reaches exactly DEPTH steps and no further", () => {
    const near = nearby("n0", keys(6), chain(6));
    expect(DEPTH).toBe(2);
    expect([...near].sort()).toEqual(["n0", "n1", "n2"]);
  });

  it("measures from where you stand, not from the graph's beginning", () => {
    const near = nearby("n3", keys(6), chain(6));
    expect([...near].sort()).toEqual(["n1", "n2", "n3", "n4", "n5"]);
  });

  it("shows the whole group when there is no node of yours to measure from", () => {
    //: Somebody else's city opened from the outside: a window with no centre
    //: would be an empty screen.
    expect(nearby(null, keys(6), chain(6)).size).toBe(6);
    expect(nearby("elsewhere", keys(6), chain(6)).size).toBe(6);
  });

  it("does not reach what no edge leads to", () => {
    const near = nearby("n0", [...keys(3), "lone"], chain(3));
    expect(near.has("lone")).toBe(false);
  });
});

describe("camera", () => {
  it("puts the aimed place in the middle of the frame", () => {
    expect(frameOn({ x: 0, y: 0 }, 1)).toEqual({ x: -W / 2, y: -H / 2 });
    //: Zoomed in twice, the frame covers half the world and the same point
    //: still stands in its centre.
    expect(frameOn({ x: 100, y: 100 }, 2)).toEqual({ x: 100 - W / 4, y: 100 - H / 4 });
  });

  it("closes the gap by a share of itself, never overshooting", () => {
    const from = { x: 0, y: 0 };
    const to = { x: 100, y: 200 };
    const step = chase(from, to, CHASE_TAU);
    //: One tau: about a third of the gap is left, on both axes alike.
    expect(step.x).toBeCloseTo(100 * (1 - 1 / Math.E), 6);
    expect(step.y).toBeCloseTo(200 * (1 - 1 / Math.E), 6);
    //: Within one frame's worth of time the frame lands short of its aim.
    const frame = chase(from, to, 64);
    expect(frame.x).toBeLessThan(to.x);
    expect(frame.y).toBeLessThan(to.y);
    //: However long the step, it never lands past the aim -- a step that
    //: swallows the whole gap stops exactly on it.
    const far = chase(from, to, 10_000);
    expect(far.x).toBeLessThanOrEqual(to.x);
    expect(far.y).toBeLessThanOrEqual(to.y);
    expect(far.x).toBeCloseTo(100, 6);
  });

  it("does not move without time, and time never runs backwards", () => {
    const from = { x: 7, y: -3 };
    const to = { x: 100, y: 100 };
    expect(chase(from, to, 0)).toEqual(from);
    //: A clock that jumped back must not drag the frame the other way.
    expect(chase(from, to, -50)).toEqual(from);
  });

  it("moves the same distance whether the time comes in one step or four", () => {
    const from = { x: 0, y: 0 };
    const to = { x: 100, y: 0 };
    const once = chase(from, to, 64);
    let split = from;
    for (let i = 0; i < 4; i++) split = chase(split, to, 16);
    expect(split.x).toBeCloseTo(once.x, 6);
  });

  it("calls the chase over only within a fraction of a pixel", () => {
    expect(arrived({ x: 0, y: 0 }, { x: ARRIVED / 2, y: 0 })).toBe(true);
    expect(arrived({ x: 0, y: 0 }, { x: 1, y: 0 })).toBe(false);
  });
});

/**
 * The camera's orchestration -- who holds the frame and when the follow comes
 * back -- on a clock and an animation loop of our own: the browser's are
 * exactly what a test cannot wait for.
 */
describe("the camera", () => {
  const loop = () => {
    let booked: ((t: number) => void)[] = [];
    let id = 0;
    let clock = 0;
    return {
      now: () => clock,
      raf: (step: (t: number) => void) => {
        booked.push(step);
        return ++id;
      },
      cancel: () => {
        booked = [];
      },
      /** Run `n` frames, `ms` apart -- 16ms is what a browser gives. */
      pump(n = 1, ms = 16) {
        for (let i = 0; i < n; i++) {
          clock += ms;
          const due = booked;
          booked = [];
          for (const step of due) step(clock);
        }
      },
      booked: () => booked.length,
    };
  };

  const rig = (still = false) => {
    const beat = loop();
    const seen: Frame[] = [];
    const cam = createCamera({
      onFrame: (f) => seen.push({ ...f }),
      still: () => still,
      now: beat.now,
      raf: beat.raf,
      cancel: beat.cancel,
    });
    return { beat, seen, cam };
  };

  //: What the frame is looking at: its own middle, whatever the scale.
  const middleOf = (f: Frame) => ({
    x: f.x + W / (2 * f.scale),
    y: f.y + H / (2 * f.scale),
  });

  it("cuts to a place at once and books no frames", () => {
    const { beat, cam } = rig();
    cam.cut({ x: 300, y: 100 });
    expect(middleOf(cam.frame())).toEqual({ x: 300, y: 100 });
    expect(beat.booked()).toBe(0);
  });

  it("chases a place, shrinking every step, and stops on arrival", () => {
    const { beat, cam } = rig();
    cam.aimAt({ x: 600, y: 0 });
    const xs = [cam.frame().x];
    for (let i = 0; i < 3; i++) {
      beat.pump();
      xs.push(cam.frame().x);
    }
    const steps = xs.slice(1).map((x, i) => x - xs[i]);
    expect(steps[0]).toBeGreaterThan(0);
    //: Every next step is smaller: that is the softness of the landing.
    expect(steps[1]).toBeLessThan(steps[0]);
    expect(steps[2]).toBeLessThan(steps[1]);
    //: And the first step is nowhere near the whole way -- no teleport.
    expect(steps[0]).toBeLessThan(600 / 2);

    beat.pump(60);
    expect(middleOf(cam.frame())).toEqual({ x: 600, y: 0 });
    //: Arrived means arrived: nothing is booked for the next frame.
    expect(beat.booked()).toBe(0);
  });

  it("gives the frame to the hand and does not take it back mid-journey", () => {
    const { beat, cam } = rig();
    cam.follow(true);
    cam.toDot({ x: 500, y: 0 });
    beat.pump();
    const chased = cam.frame().x;
    expect(chased).toBeGreaterThan(-W / 2);

    //: The hand takes the frame: the chase stops where it stood.
    cam.takeFrame();
    const held = cam.frame().x;
    beat.pump(5);
    expect(cam.frame().x).toBe(held);
    expect(cam.following()).toBe(false);

    //: The next leg of the same walk must not steal it back.
    cam.toDot({ x: 900, y: 0 });
    beat.pump(5);
    expect(cam.frame().x).toBe(held);

    //: A new journey does bring the follow back.
    cam.follow(true);
    cam.toDot({ x: 900, y: 0 });
    beat.pump();
    expect(cam.frame().x).toBeGreaterThan(held);
  });

  it("keeps still where motion is unwanted", () => {
    const { beat, cam } = rig(true);
    cam.aimAt({ x: 400, y: 0 });
    //: Cut, not chased: nothing to animate for whoever asked for no motion.
    expect(middleOf(cam.frame())).toEqual({ x: 400, y: 0 });
    expect(beat.booked()).toBe(0);
    cam.follow(true);
    expect(cam.following()).toBe(false);
  });

  it("zooms a tethered camera without moving what is in the middle", () => {
    const { beat, cam } = rig();
    cam.cut({ x: 300, y: 200 });
    cam.zoomOnMiddle(2);
    //: The whole of what a hand may do to a tethered camera: less world in the
    //: frame, the same thing in the centre of it.
    expect(cam.frame().scale).toBe(2);
    expect(middleOf(cam.frame())).toEqual({ x: 300, y: 200 });
    expect(beat.booked()).toBe(0);
  });

  it("keeps the aim on a place, not on a frame, when the scale changes", () => {
    const { beat, cam } = rig();
    cam.follow(true);
    cam.toDot({ x: 400, y: 0 });
    beat.pump();
    //: The wheel turns mid-walk. An aim remembered as a ready-made frame would
    //: have been worked out for scale 1, and the walker would land off centre.
    cam.zoomOnMiddle(2);
    beat.pump(120);
    expect(cam.frame().scale).toBe(2);
    expect(middleOf(cam.frame())).toEqual({ x: 400, y: 0 });
  });

  it("never lets one long step swallow the whole distance", () => {
    const { beat, cam } = rig();
    const before = cam.frame();
    cam.aimAt({ x: 1000, y: 0 });
    //: A tab that was hidden for ten seconds comes back with one huge gap:
    //: the step must be no longer than the longest one allowed, or the
    //: smoothing turns back into the jump it was put there to remove.
    beat.pump(1, 10_000);
    const moved = cam.frame().x - before.x;
    const longest = chase(before, frameOn({ x: 1000, y: 0 }, 1), LONGEST_STEP).x - before.x;
    expect(moved).toBeCloseTo(longest, 6);
    //: And that is a fraction of the way, not the whole of it.
    expect(moved).toBeLessThan((frameOn({ x: 1000, y: 0 }, 1).x - before.x) / 2);
  });
});

describe("the hand's arithmetic", () => {
  //: A field of exactly the world's proportions: no margins to account for.
  const snug = { left: 0, top: 0, width: W, height: H };

  it("measures the field against the world at any zoom", () => {
    expect(lensOn(snug, 1)).toEqual({ k: 1, offX: 0, offY: 0 });
    //: Zoomed in twice, half the world fills the same pixels.
    expect(lensOn(snug, 2).k).toBe(2);
  });

  it("counts the empty room the kept proportions leave at the edges", () => {
    //: A field twice as wide as the world's shape: the picture is as tall as
    //: it can be and the room left over is split between left and right.
    const wide = { left: 0, top: 0, width: 2 * W, height: H };
    const m = lensOn(wide, 1);
    expect(m.k).toBe(1);
    expect(m.offX).toBe(W / 2);
    expect(m.offY).toBe(0);
  });

  it("finds the world under a point, margins and all", () => {
    const frame = { x: 0, y: 0, scale: 1 };
    expect(worldAt(snug, frame, { clientX: 0, clientY: 0 })).toEqual({ x: 0, y: 0 });
    expect(worldAt(snug, frame, { clientX: 10, clientY: 4 })).toEqual({ x: 10, y: 4 });
    //: The frame's own offset moves the answer with it.
    expect(worldAt(snug, { x: 100, y: 50, scale: 1 }, { clientX: 10, clientY: 4 })).toEqual({
      x: 110,
      y: 54,
    });
    //: On a field wider than the world's shape, a click in the left margin
    //: lands left of the picture -- and must not be read as a click on it.
    const wide = { left: 0, top: 0, width: 2 * W, height: H };
    expect(worldAt(wide, frame, { clientX: 0, clientY: 0 }).x).toBe(-W / 2);
    expect(worldAt(wide, frame, { clientX: W / 2, clientY: 0 }).x).toBe(0);
  });

  it("reads the same point back after a zoom to it", () => {
    //: What the wheel promises: the place under the cursor stays under it.
    const frame = { x: 0, y: 0, scale: 1 };
    const at = { clientX: 300, clientY: 200 };
    const under = worldAt(snug, frame, at);
    const zoomed = {
      scale: 2,
      x: under.x - (under.x - frame.x) * (frame.scale / 2),
      y: under.y - (under.y - frame.y) * (frame.scale / 2),
    };
    const again = worldAt(snug, zoomed, at);
    expect(again.x).toBeCloseTo(under.x, 6);
    expect(again.y).toBeCloseTo(under.y, 6);
  });
});

describe("journeyOf", () => {
  const leg = (over: Partial<Transit>): Transit =>
    ({
      to: "Рынок",
      to_key: "market",
      from_key: "gate",
      started_at: "2026-08-28T00:00:00Z",
      arrives_at: "2026-08-28T00:01:00Z",
      ...over,
    }) as Transit;

  it("names a journey by where it ends, not by the leg under way", () => {
    //: An autopath of several legs: every leg but the last carries the plan's
    //: last node, so the journey keeps one identity from the first step.
    const first = journeyOf(leg({ to_key: "gate2", final_key: "far" }));
    const second = journeyOf(leg({ to_key: "cross", final_key: "far" }));
    //: The last leg's own end **is** the plan's end, and the plan is gone.
    const last = journeyOf(leg({ to_key: "far" }));
    expect(first).toBe("far");
    expect(second).toBe("far");
    expect(last).toBe("far");
  });

  it("is nothing at all while one stands still", () => {
    expect(journeyOf(null)).toBeNull();
    expect(journeyOf(undefined)).toBeNull();
  });
});

describe("sceneKey", () => {
  it("tells apart a layer, a city and a planet", () => {
    expect(sceneKey("city", "capital", "terra")).toBe("city|capital|terra");
    expect(sceneKey("city", "capital", "terra")).not.toBe(
      sceneKey("city", "harbour", "terra"),
    );
    expect(sceneKey("planet", null, "terra")).not.toBe(
      sceneKey("planet", null, "pyroxis"),
    );
    //: Nothing is nothing, however it arrives.
    expect(sceneKey("space", null, null)).toBe(sceneKey("space", null, null));
  });
});

describe("delegate", () => {
  it("climbs the parents to the layer being drawn", () => {
    const byKey: Record<string, MapNode> = {
      terra: node({ key: "terra", layer: "space" }),
      city: node({ key: "city", layer: "planet", parent: "terra" }),
      gate: node({ key: "gate", layer: "city", parent: "city" }),
    };
    expect(delegate(byKey, "gate", "planet")).toBe("city");
    expect(delegate(byKey, "gate", "city")).toBe("gate");
    expect(delegate(byKey, "gate", "space")).toBe("terra");
    expect(delegate(byKey, "gate", "location")).toBe(null);
  });
});

describe("homeCity", () => {
  //: A city, its gate, and a ship moored at its pier: the hull hangs under the
  //: planet, so nothing above it is a city at all.
  const byKey: Record<string, MapNode> = {
    terra: node({ key: "terra", layer: "space" }),
    capital: node({ key: "capital", layer: "planet", parent: "terra" }),
    pier: node({ key: "pier", layer: "city", parent: "capital", port: true }),
    hull: node({ key: "hull", layer: "space", parent: "terra", aboard: true }),
    cabin: node({ key: "cabin", layer: "location", parent: "hull", aboard: true }),
    far: node({ key: "far", layer: "planet", parent: "terra" }),
    veyr: node({ key: "veyr", layer: "planet", parent: "aurora", planet: "aurora" }),
    dock: node({ key: "dock", layer: "city", parent: "veyr", planet: "aurora" }),
  };

  it("names the city above where you stand", () => {
    expect(homeCity(byKey, "pier", [])).toBe("capital");
  });

  it("follows the gangway when there is no city above the hull", () => {
    //: Without this the moored crew fell through to whatever city came first
    //: in the world by name, and could not walk off their own ship.
    expect(homeCity(byKey, "cabin", [])).toBe(null);
    expect(homeCity(byKey, "cabin", [{ key: "pier" }, { key: "cabin" }])).toBe("capital");
  });

  it("prefers what is underfoot to what an exit leads to", () => {
    expect(homeCity(byKey, "pier", [{ key: "dock" }])).toBe("capital");
  });

  it("answers null when neither underfoot nor any exit is in a city", () => {
    expect(homeCity(byKey, "far", [{ key: "capital" }])).toBe(null);
  });

  it("holds on a map that does not have the node yet", () => {
    //: The world is reread after the body moves, so for a frame the key is
    //: ahead of the map -- and that frame is exactly where this used to break.
    expect(homeCity(byKey, "nowhere", [])).toBe(null);
    expect(homeCity(byKey, "nowhere", [{ key: "nowhere-either" }])).toBe(null);
  });
});

describe("offworld", () => {
  const byKey: Record<string, MapNode> = {
    gate: node({ key: "gate", planet: "terra" }),
    field: node({ key: "field", layer: "planet", planet: "pyroxis" }),
    pier: node({ key: "pier", planet: "terra", port: true }),
    hull: node({ key: "hull", planet: "terra", aboard: true }),
  };

  it("marks a node of another planet: walked-to never, flown-to only", () => {
    expect(offworld(byKey, "gate", byKey.field)).toBe(true);
    expect(offworld(byKey, "gate", byKey.gate)).toBe(false);
  });

  it("lets a moored ship be boarded: the engine moves its planet to the port's", () => {
    //: `ship/flight.py` rewrites every aboard node's planet on arrival, so the
    //: hull at your pier compares equal and the gangway stays offered.
    expect(offworld(byKey, "pier", byKey.hull)).toBe(false);
  });

  it("answers false when either planet is unknown: absence must not lock the map", () => {
    expect(offworld(byKey, "unknown", byKey.field)).toBe(false);
    expect(offworld(byKey, "gate", node({ key: "bare", planet: "" }))).toBe(false);
  });
});

describe("settle", () => {
  const given = new Map([
    ["n0", { x: 0, y: 0 }],
    ["n1", { x: 150, y: 0 }],
  ]);

  it("leaves the places the server gave exactly where they are", () => {
    const out = settle(keys(4), chain(4), given);
    expect(out.get("n0")).toEqual({ x: 0, y: 0 });
    expect(out.get("n1")).toEqual({ x: 150, y: 0 });
  });

  it("gives the same answer every time: one map for every player", () => {
    const once = settle(keys(5), chain(5), given);
    const twice = settle(keys(5), chain(5), given);
    for (const key of keys(5)) expect(once.get(key)).toEqual(twice.get(key));
  });

  it("does not depend on the order the nodes arrive in", () => {
    const forward = settle(keys(5), chain(5), given);
    const backward = settle([...keys(5)].reverse(), [...chain(5)].reverse(), given);
    for (const key of keys(5)) {
      expect(backward.get(key)!.x).toBeCloseTo(forward.get(key)!.x, 6);
      expect(backward.get(key)!.y).toBeCloseTo(forward.get(key)!.y, 6);
    }
  });

  it("leaves a node with no edges where it started instead of flinging it away", () => {
    //: Soft walls used to push whatever was unconnected to the frame's edge --
    //: as far from everybody as the frame allowed -- and the map lied about the
    //: shape of the world.
    const alone = settle(["lone"], [], new Map());
    const point = alone.get("lone")!;
    expect(Math.hypot(point.x, point.y)).toBeLessThan(700);
  });

  it("settles before it returns: the free nodes have actually spread", () => {
    //: The map appears laid out rather than crawling into place, so by the time
    //: settle returns the chain must already be a chain -- not six nodes still
    //: sitting where they were seeded.
    const out = settle(keys(6), chain(6), given);
    for (let i = 1; i < 6; i++) {
      const gap = Math.hypot(
        out.get(`n${i}`)!.x - out.get(`n${i - 1}`)!.x,
        out.get(`n${i}`)!.y - out.get(`n${i - 1}`)!.y,
      );
      expect(gap).toBeGreaterThan(60);
      expect(gap).toBeLessThan(400);
    }
  });
});

describe("words", () => {
  //: The hour is the border between two units and both sides of it want their
  //: own: "60 мин" reads worse than "1 ч", and "0.7 ч" worse than "40 мин".
  it("keeps minutes up to the hour and goes over to hours after it", () => {
    expect(long(40)).toBe("40 мин");
    expect(long(59)).toBe("59 мин");
    expect(long(60)).toBe("1 ч");
    expect(long(90)).toBe("1.5 ч");
  });

  it("names the unit once while there is only one of them", () => {
    expect(spread(10, 40)).toBe("10–40 мин");
    expect(spread(60, 120)).toBe("1–2 ч");
    //: Across the border both ends are spelled whole, or it reads "40–2 ч".
    expect(spread(40, 120)).toBe("40 мин – 2 ч");
  });

  it("prints an empty spread as the term it is", () => {
    expect(spread(30, 30)).toBe("30–30 мин");
  });

  //: A step across town costs a fraction of a unit, and "0.0" would lie about
  //: it: there is a price.
  it("does not round the road's price down to nothing", () => {
    expect(price(0)).toBe("0");
    expect(price(0.02)).toBe("<0.1");
    expect(price(0.34)).toBe("0.3");
  });
});

describe("orbits", () => {
  it("counts a term in hours up to a day and in days after it", () => {
    expect(term(3.25)).toBe("3.3 ч");
    expect(term(12)).toBe("12 ч");
    //: Rounded before the unit is chosen: the other way round 23.9 gave "24 ч"
    //: and 24 gave "1.0 сут", the earlier term reading as the longer one.
    expect(term(23.9)).toBe("1.0 сут");
    expect(term(23.4)).toBe("23 ч");
    expect(term(24)).toBe("1.0 сут");
    expect(term(36)).toBe("1.5 сут");
  });

  //: The corridor's calendar is the engine's (D-271): the client leafs to the
  //: day shown and reads the cheapest arc off it, never recomputes it.
  it("leafs the calendar to the day shown and clamps at its ends", () => {
    const route = {
      a: "terra",
      b: "pyroxis",
      days: [
        { day: 10, dv: 30, hours: 200 },
        { day: 11, dv: 12, hours: 240 },
        { day: 12, dv: 20, hours: 220 },
      ],
    } as never;
    expect(forecast(route, 11.4)?.dv).toBe(12);
    expect(forecast(route, 3)?.day).toBe(10);
    expect(forecast(route, 99)?.day).toBe(12);
    expect(forecast({ a: "a", b: "b", days: [] } as never, 11)).toBeUndefined();
  });

  it("calls the window open within a tenth of the spread above the dip", () => {
    const route = {
      a: "terra",
      b: "pyroxis",
      days: [
        { day: 0, dv: 30, hours: 1 },
        { day: 1, dv: 12, hours: 1 },
        { day: 2, dv: 13, hours: 1 },
        { day: 3, dv: 20, hours: 1 },
      ],
    } as never;
    expect(windowOpen(route, 1)).toBe(true);
    expect(windowOpen(route, 2)).toBe(true);
    expect(windowOpen(route, 3)).toBe(false);
  });

  //: The arc's points are at equal time steps: a share of the time is a
  //: place on the polyline, interpolated inside its segment.
  it("finds the place along an arc by the share of the time gone", () => {
    const arc: [number, number][] = [
      [0, 0],
      [10, 0],
      [10, 10],
    ];
    expect(along(arc, 0)).toEqual([0, 0]);
    expect(along(arc, 0.25)).toEqual([5, 0]);
    expect(along(arc, 0.5)).toEqual([10, 0]);
    expect(along(arc, 1)).toEqual([10, 10]);
    expect(along(arc, 2)).toEqual([10, 10]);
    expect(along([[3, 4]], 0.5)).toEqual([3, 4]);
  });

  //: A ship's mooring is its own and does not move: otherwise the hull would
  //: jump around its planet on every reread of the map.
  it("gives a ship a steady mooring somewhere on the circle", () => {
    expect(mooring("ship.node.abc")).toBe(mooring("ship.node.abc"));
    expect(mooring("ship.node.abc")).not.toBe(mooring("ship.node.abd"));
    for (const key of ["a", "ship.node.abc", "", "длинный ключ"]) {
      expect(mooring(key)).toBeGreaterThanOrEqual(0);
      expect(mooring(key)).toBeLessThan(Math.PI * 2);
    }
  });
});

describe("the pinch", () => {
  it("scales by the fingers' spread against where the pinch began", () => {
    //: Fingers twice as far apart: twice as near. Measured from the start,
    //: so the same spread asks for the same scale however many moves between.
    expect(pinchScale(1, 100, 200)).toBe(2);
    expect(pinchScale(1, 100, 50)).toBe(0.5);
    expect(pinchScale(2, 100, 150)).toBe(3);
  });

  it("stays within what the map may show", () => {
    expect(pinchScale(1, 10, 1000)).toBe(clampScale(100));
    expect(pinchScale(1, 1000, 10)).toBe(clampScale(0.01));
    //: Fingers that began on one point are no measure: the scale holds.
    expect(pinchScale(1.5, 0, 80)).toBe(1.5);
  });

  it("keeps what lay under the fingers' middle under it, move after move", () => {
    //: A field of the world's own proportions, and a loose camera that
    //: paints nowhere: the invariant is about the frame, not the svg.
    const box = { left: 0, top: 0, width: W, height: H };
    const cam = createCamera({ onFrame: () => {}, raf: () => 0, cancel: () => {} });
    const under = (at: Point) => worldAt(box, cam.frame(), { clientX: at.x, clientY: at.y });
    let mid = { x: 100, y: 80 };
    const held = under(mid);
    //: Three moves that drift and zoom at once, one of them zooming back out:
    //: after each the same world point lies under the fingers' middle.
    for (const [to, scale] of [
      [{ x: 150, y: 120 }, 2],
      [{ x: 160, y: 130 }, 3],
      [{ x: 120, y: 100 }, 1.5],
    ] as const) {
      pinchTo(cam, box, mid, to, scale);
      mid = to;
      expect(cam.frame().scale).toBe(scale);
      expect(under(mid).x).toBeCloseTo(held.x, 6);
      expect(under(mid).y).toBeCloseTo(held.y, 6);
    }
  });
});
