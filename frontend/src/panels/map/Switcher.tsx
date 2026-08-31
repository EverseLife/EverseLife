// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The bar that floats over the top of the map (D-238).
 *
 * It holds the two questions that are about the view rather than about the
 * world -- from what height am I looking, and does the camera come with me --
 * and it holds them together because they are one question asked twice. It
 * stands on the map itself, in the middle of its top edge, where the eye
 * already is: a strip above the field would cost a line of the height the map
 * is the whole point of.
 *
 * Every layer wears the mark of what it opens, and the city's is the colonnade
 * the map already draws on a settlement -- a switcher whose icons were invented
 * apart from the map would be a second vocabulary to learn.
 *
 * The marks stand **beside** the words, never instead of them (D-238) -- with
 * one exception, and it is written down rather than assumed: on a screen
 * narrower than 56rem the words are hidden and the bar is marks alone. The
 * alternative was worse in the same brief's terms. At 375px the words wrap the
 * bar onto three lines over a map 214px tall, and a control that hides its own
 * subject fails harder than a mark somebody has to learn once. The word is
 * hidden by CSS only: it stays in the button for a screen reader, it is the
 * button's `aria-label` in every case, and the hint at the end of the bar
 * names the layers in words.
 */

import { Glyph } from "../../Glyph";
import { Hint } from "../../Hint";
import type { GlyphName } from "../../glyphs";
import { t } from "../../locale";
import type { LayerId } from "./model";

type Layer = { id: LayerId; label: string; mark: GlyphName };

export function Switcher({
  layers,
  current,
  onLayer,
  tethered,
  onTether,
}: {
  /** The layers worth offering: an empty one is not shown at all. */
  layers: readonly Layer[];
  current: LayerId;
  onLayer: (id: LayerId) => void;
  /** Whether the camera is tied to the body -- see `GraphMap`. */
  tethered: boolean;
  onTether: (on: boolean) => void;
}) {
  const word = t(tethered ? "ui-map-cam-tied" : "ui-map-cam-free");
  return (
    <nav className="row tabs map-layers">
      {layers.map((option) => (
        <button
          key={option.id}
          className={current === option.id ? "" : "quiet"}
          aria-current={current === option.id || undefined}
          //: The word is hidden on a narrow screen, not removed: the button
          //: keeps its name for a reader, and for the hint that lists the
          //: layers beside it. No `title`: on a wide screen the word is right
          //: there, and a tooltip repeating it would pop over the map.
          aria-label={option.label}
          onClick={() => onLayer(option.id)}
        >
          <Glyph name={option.mark} />
          <span className="tab-word">{option.label}</span>
        </button>
      ))}
      {/* The two questions are not a row of equals: the height is a set of
          alternatives, the tether is a state of its own. */}
      <span className="map-sep" aria-hidden="true" />
      <button
        className={`cam-tie${tethered ? "" : " quiet"}`}
        aria-pressed={tethered}
        aria-label={word}
        onClick={() => onTether(!tethered)}
      >
        <Glyph name={tethered ? "pinned" : "loose"} />
        <span className="tab-word">{word}</span>
      </button>
      <Hint>{t("ui-map-switcher-rule")}</Hint>
    </nav>
  );
}
