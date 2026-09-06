// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The bar that floats over the top of the map (D-238).
 *
 * It holds the two questions that are about the view rather than about the
 * world -- from what height am I looking, and does the camera come with me --
 * and it holds them together because they are one question asked twice.
 * The zoom is not here: it is the slider at the map's right edge
 * (`Zoom`), where a thumb can reach it on a phone. The bar
 * stands on the map itself, in the middle of its top edge, where the eye
 * already is: a strip above the field would cost a line of the height the map
 * is the whole point of.
 *
 * The marks stand **beside** the words, never instead of them (D-238) -- with
 * one exception, and it is written down rather than assumed: on a screen
 * narrower than 56rem the words are hidden and the bar is marks alone. The
 * alternative was worse in the same brief's terms. At 375px the words wrap the
 * bar onto three lines over a map 214px tall, and a control that hides its own
 * subject fails harder than a mark somebody has to learn once. The word is
 * hidden by CSS only: it stays in the button for a screen reader, and it is
 * the button's `aria-label` in every case.
 */

import { Glyph } from "../../Glyph";
import { t } from "../../locale";

export function Switcher({
  inside,
  onInside,
  tethered,
  onTether,
}: {
  /** Whether there is an inside to open from here -- floors, a hull's rooms
   *  (D-319, wave 4) -- and whether it is open now. Null: nothing inside. */
  inside: boolean | null;
  onInside: (on: boolean) => void;
  /** Whether the camera is tied to the body -- see `GraphMap`. */
  tethered: boolean;
  onTether: (on: boolean) => void;
}) {
  const word = t(tethered ? "ui-map-cam-tied" : "ui-map-cam-free");
  const door = t(inside ? "ui-map-outside" : "ui-map-inside");
  return (
    <nav className="row tabs map-layers">
      {/* The heights are the wheel's now (D-319, wave 4): far out the sky,
          close in the surface, and a city opens as one comes near. The one
          thing that is not a height is the inside -- floors, rooms -- and
          it is a door, not a tab: the button names the way through it, in
          or out, and is not a pressed state like the camera's. */}
      {inside !== null && (
        <button className="quiet" aria-label={door} onClick={() => onInside(!inside)}>
          <Glyph name="rooms" />
          <span className="tab-word">{door}</span>
        </button>
      )}
      {inside !== null && <span className="map-sep" aria-hidden="true" />}
      <button
        className={`cam-tie${tethered ? "" : " quiet"}`}
        aria-pressed={tethered}
        aria-label={word}
        onClick={() => onTether(!tethered)}
      >
        <Glyph name={tethered ? "pinned" : "loose"} />
        <span className="tab-word">{word}</span>
      </button>
    </nav>
  );
}

/** How finely the slider is stepped: a thousand notches from the farthest
 *  to the nearest, on the log of the scale, so every notch is one share of
 *  the way and the wheel's own steps land between them. */
export const ZOOM_STEPS = 1000;

/** The slider's notch for a scale within its bounds, and back. */
export function notchOf(scale: number, bounds: { nearest: number; furthest: number }): number {
  const span = Math.log(bounds.nearest / bounds.furthest);
  if (!(span > 0)) return 0;
  const share = Math.log(scale / bounds.furthest) / span;
  return Math.round(Math.min(1, Math.max(0, share)) * ZOOM_STEPS);
}
export function scaleOf(notch: number, bounds: { nearest: number; furthest: number }): number {
  const share = Math.min(1, Math.max(0, notch / ZOOM_STEPS));
  return bounds.furthest * (bounds.nearest / bounds.furthest) ** share;
}

/**
 * The zoom slider at the map's right edge (owner, 2026-09-06): up is
 * nearer. Its notch follows the frame by the ref -- set from the camera's
 * own frame loop, not through React, as the viewBox is -- and a drag of the
 * thumb zooms about the middle like the wheel does.
 */
export function Zoom({
  slider,
  onZoom,
}: {
  slider: React.RefObject<HTMLInputElement | null>;
  onZoom: (notch: number) => void;
}) {
  return (
    <input
      ref={slider}
      className="map-zoom"
      type="range"
      min={0}
      max={ZOOM_STEPS}
      step={1}
      defaultValue={0}
      aria-label={t("ui-map-zoom")}
      title={t("ui-map-zoom")}
      onInput={(e) => onZoom(Number((e.target as HTMLInputElement).value))}
    />
  );
}
