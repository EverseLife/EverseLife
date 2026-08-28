// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The camera of the map (D-238): the frame, and everything that moves it.
 *
 * The frame moves for three reasons -- a hand, a new middle to stand on, and
 * the walker it follows while one is under way -- and until this module they
 * moved it by three different rules. Two of them wrote the frame on the same
 * animation frames, and the map shook at the first step of every walk.
 *
 * So the rule is one: whoever wants the frame elsewhere names a **point**,
 * and the frame chases it. Chasing is exponential smoothing -- each frame the
 * gap shrinks by a share of itself -- and that gives both of the things a cut
 * cannot: a soft start when the aim jumps far away (the frame does not
 * teleport onto a walker one had panned away from) and an imperceptible tail
 * when the aim creeps (a walking dot is trailed by a fraction of a pixel).
 *
 * It lives outside the component, and outside React, for two reasons. The
 * frame is one string of one attribute: re-rendering a map of forty nodes
 * sixty times a second to write it is work for nothing -- the walking dot was
 * taken out of React for exactly that reason, and the camera followed it out.
 * And the orchestration -- who holds the frame, when the follow resumes, when
 * a chase is cut -- is where the bugs were, so it is here, on an injected
 * clock and an injected `requestAnimationFrame`, where tests can hold it.
 */

import { H, W, type Point } from "./model";

/** Where the frame stands and how much of the world it covers. */
export type Frame = Point & { scale: number };

/**
 * How fast the frame closes the gap: the share left after `tau` milliseconds
 * is `1/e`, about a third. At 120ms half a screen is crossed in a fifth of a
 * second and the eye reads it as one movement rather than a jump.
 */
export const CHASE_TAU = 120;

/**
 * Below this the chase is over. Without a floor the frame would creep for
 * ever, repainting for a hundredth of a pixel and never arriving.
 */
export const ARRIVED = 0.2;

/** The longest step the smoothing takes in one go. A tab that was hidden
 *  comes back with a gap of seconds, and one step must not swallow it. */
export const LONGEST_STEP = 64;

/** Where the frame's top-left stands for `middle` to be in its centre. */
export function frameOn(middle: Point, scale: number): Point {
  return { x: middle.x - W / (2 * scale), y: middle.y - H / (2 * scale) };
}

/**
 * One step of the chase, `dt` milliseconds after the last one.
 *
 * Framerate-independent: the same span of time moves the frame the same
 * distance whether it arrives in one long frame or four short ones, so a slow
 * machine gets the same motion, only coarser.
 */
export function chase(from: Point, to: Point, dt: number, tau = CHASE_TAU): Point {
  const share = 1 - Math.exp(-Math.max(0, dt) / tau);
  return {
    x: from.x + (to.x - from.x) * share,
    y: from.y + (to.y - from.y) * share,
  };
}

/** Whether the frame is close enough to its aim to stop chasing. */
export function arrived(from: Point, to: Point): boolean {
  return Math.hypot(to.x - from.x, to.y - from.y) < ARRIVED;
}

/** The frame as the `viewBox` attribute spells it. */
export function viewBoxOf(frame: Frame): string {
  return `${frame.x} ${frame.y} ${W / frame.scale} ${H / frame.scale}`;
}

type Wiring = {
  /** Show the frame: the component paints it onto the svg. */
  onFrame: (frame: Frame) => void;
  /** Whether motion is unwanted altogether (`prefers-reduced-motion`). */
  still?: () => boolean;
  now?: () => number;
  raf?: (step: (t: number) => void) => number;
  cancel?: (id: number) => void;
};

export type Camera = ReturnType<typeof createCamera>;

/**
 * The camera itself: state nobody else may write, and the few things one can
 * ask of it.
 *
 * The hand outranks every autopilot: `takeFrame` stops the chase **and** the
 * follow, and the follow comes back only when a new walk begins -- not when
 * the walk's next leg does, which is why `follow` is told about the journey
 * rather than deduced from the legs.
 */
export function createCamera({
  onFrame,
  still = () => false,
  now = () => performance.now(),
  raf = requestAnimationFrame,
  cancel = cancelAnimationFrame,
}: Wiring) {
  let frame: Frame = { x: 0, y: 0, scale: 1 };
  //: The aim is a **middle**, not a ready-made frame: the scale can change
  //: under a chase (the wheel turns while the walker is followed), and a frame
  //: worked out for the old scale would land the body off centre.
  let aim: Point | null = null;
  let chasing = 0;
  let chasedAt = 0;
  let following = false;

  const show = () => onFrame(frame);

  const step = (t: number) => {
    //: The frame it was scheduled for has fired: the invariant "chasing means
    //: a frame is booked" holds again from here.
    chasing = 0;
    if (!aim) return;
    const dt = Math.min(LONGEST_STEP, Math.max(0, t - chasedAt));
    chasedAt = t;
    const target = frameOn(aim, frame.scale);
    if (arrived(frame, target)) {
      frame = { ...frame, ...target };
      aim = null;
      show();
      return;
    }
    frame = { ...frame, ...chase(frame, target, dt) };
    show();
    chasing = raf(step);
  };

  const book = () => {
    if (chasing) return;
    chasedAt = now();
    chasing = raf(step);
  };

  const drop = () => {
    if (chasing) cancel(chasing);
    chasing = 0;
    aim = null;
  };

  /** Put the frame on a place at once: no chase, nothing to see on the way. */
  const cut = (middle: Point) => {
    drop();
    frame = { ...frame, ...frameOn(middle, frame.scale) };
    show();
  };

  /** Aim the frame at a place. Cut where a chase would sweep the frame
   *  through coordinates that hold nothing -- a layer or a city change. */
  const aimAt = (middle: Point, atOnce = false) => {
    if (atOnce || still()) return cut(middle);
    aim = middle;
    book();
  };

  /** Where the frame is looking now: the world point in its middle. */
  const middleOf = (f: Frame): Point => ({
    x: f.x + W / (2 * f.scale),
    y: f.y + H / (2 * f.scale),
  });

  return {
    frame: () => frame,
    viewBox: () => viewBoxOf(frame),
    aimAt,
    cut,

    /** A journey begins or ends. Told, not deduced: a walk of five legs is
     *  one journey, and a hand that took the frame on the first leg keeps it
     *  to the last. */
    follow(journey: boolean) {
      following = journey && !still();
    },
    following: () => following,

    /** The walker names where it is; the frame chases it if it is following. */
    toDot(dot: Point) {
      if (!following) return;
      aim = dot;
      book();
    },

    /** The hand takes the frame: every autopilot lets go at once. */
    takeFrame() {
      following = false;
      drop();
    },

    /** A pan: the hand puts the frame exactly where it drags it. */
    panTo(x: number, y: number) {
      frame = { ...frame, x, y };
      show();
    },

    /**
     * A zoom that keeps the middle (D-238): what is centred stays centred.
     *
     * This is the whole of what a hand may do to a tethered camera. Zooming to
     * the cursor would slide the body out of the frame, and a camera that is
     * held to the body and does not hold it is worse than either mode.
     */
    zoomOnMiddle(scale: number) {
      const middle = middleOf(frame);
      frame = { scale, ...frameOn(middle, scale) };
      show();
    },

    /** A zoom to the cursor: the point under it stays under it. */
    zoomTo(under: Point, scale: number) {
      frame = {
        scale,
        x: under.x - (under.x - frame.x) * (frame.scale / scale),
        y: under.y - (under.y - frame.y) * (frame.scale / scale),
      };
      show();
    },

    /** The map is gone: nothing of this outlives it, the follow included --
     *  a camera that came back still following would refuse to be re-aimed. */
    stop() {
      drop();
      following = false;
    },
  };
}
