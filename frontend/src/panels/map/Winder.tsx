// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Winding time forward on the map: the one control the sky (D-271) and
 * the planet's year (D-334) share. A map showing anything but now must
 * say so, and it says so here: the day ahead, and a way back to now. A
 * winder that runs at more than one pace shows its paces as pressed
 * buttons, the way the account shows its density.
 */

import { Hint } from "../../Hint";
import { t } from "../../locale";

export type Winding = {
  /** How many real days ahead is shown. Zero is now. */
  ahead: number;
  /** How far it may be wound. */
  horizon: number;
  winding: boolean;
  setWinding: (on: boolean | ((was: boolean) => boolean)) => void;
  /** Wind to a given day ahead: the slider and the "now" button. */
  wind: (day: number) => void;
};

/** The paces a winder offers: each by its key and its word, the one
 *  pressed, and what pressing another does. */
export type Paces = {
  label: string;
  options: readonly { key: string; word: string }[];
  current: string;
  pick: (key: string) => void;
};

export function Winder({
  state,
  wind,
  slider,
  rule,
  paces,
}: {
  state: Winding;
  /** The message that starts the wind, the slider's label and the rule
   *  the hint tells: each winder's own words; the rest are shared. */
  wind: string;
  slider: string;
  rule: string;
  paces?: Paces;
}) {
  const { ahead, winding, setWinding } = state;
  return (
    <div className="row sky">
      <button className="quiet" onClick={() => setWinding((on) => !on)}>
        {winding ? t("ui-map-sky-stop") : wind}
      </button>
      {paces && (
        <div className="row pace" role="group" aria-label={paces.label}>
          {paces.options.map(({ key, word }) => (
            <button
              key={key}
              type="button"
              className={paces.current === key ? "" : "quiet"}
              aria-pressed={paces.current === key}
              onClick={() => paces.pick(key)}
            >
              {word}
            </button>
          ))}
        </div>
      )}
      <input
        type="range"
        min={0}
        max={state.horizon}
        step={0.25}
        value={ahead}
        aria-label={slider}
        onChange={(e) => state.wind(Number(e.target.value))}
      />
      <span className="note">
        {ahead < 0.05 ? t("ui-map-sky-now-note") : t("ui-map-sky-ahead", { days: ahead.toFixed(1) })}
      </span>
      {ahead >= 0.05 && (
        <button className="quiet" onClick={() => state.wind(0)}>
          {t("ui-map-sky-now")}
        </button>
      )}
      <Hint>{rule}</Hint>
    </div>
  );
}
