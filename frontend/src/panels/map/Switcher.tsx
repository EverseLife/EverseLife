// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The bar that floats over the top of the map (D-238).
 *
 * It holds the two questions that are about the view rather than about the
 * world -- from what height am I looking, and does the camera come with me --
 * and it holds them together because they are one question asked twice --
 * and, beside the tether, the two loupe buttons: a phone has no wheel to turn
 * and a pinch is a gesture one has to know about. It
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
 * hidden by CSS only: it stays in the button for a screen reader, it is the
 * button's `aria-label` in every case, and the hint at the end of the bar
 * says in words what the wheel and the door do.
 */

import { Glyph } from "../../Glyph";
import { Hint } from "../../Hint";
import { t } from "../../locale";

export function Switcher({
  inside,
  onInside,
  tethered,
  onTether,
  onZoom,
}: {
  /** Whether there is an inside to open from here -- floors, a hull's rooms
   *  (D-319, wave 4) -- and whether it is open now. Null: nothing inside. */
  inside: boolean | null;
  onInside: (on: boolean) => void;
  /** Whether the camera is tied to the body -- see `GraphMap`. */
  tethered: boolean;
  onTether: (on: boolean) => void;
  /** A notch nearer (`1`) or farther (`-1`), about the middle of the frame. */
  onZoom: (direction: 1 | -1) => void;
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
      {/* Marks alone at every width: a loupe reads without a word, and the
          word would make the bar wrap on the very screens these are for. A
          `title` where the others have none, for the same reason. */}
      <button
        className="quiet"
        aria-label={t("ui-zoom-in")}
        title={t("ui-zoom-in")}
        onClick={() => onZoom(1)}
      >
        <Glyph name="nearer" />
      </button>
      <button
        className="quiet"
        aria-label={t("ui-zoom-out")}
        title={t("ui-zoom-out")}
        onClick={() => onZoom(-1)}
      >
        <Glyph name="farther" />
      </button>
      <Hint>{t("ui-map-switcher-rule")}</Hint>
    </nav>
  );
}
