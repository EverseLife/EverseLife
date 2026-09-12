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
 * narrower than 76rem the words are hidden and the bar is marks alone. The
 * alternative was worse in the same brief's terms. At 375px the words wrap the
 * bar onto three lines over a map 214px tall, and a control that hides its own
 * subject fails harder than a mark somebody has to learn once. The word is
 * hidden by CSS only: it stays in the button for a screen reader, and it is
 * the button's `aria-label` in every case. The layers' button is the one
 * exception to the exception (owner, 2026-09-12): its word is not its name
 * but its **state** -- which layer the ground is coloured by -- and a state
 * the mark cannot carry stays written at every width.
 */

import { useCallback, useId, useRef, useState } from "react";

import { Glyph } from "../../Glyph";
import { t } from "../../locale";
import { usePopover } from "../../popover";
import { LAYERS, type Layer } from "./shade";

/** The overlays a viewer can switch on the map over any layer (D-331). */
export type Overlays = { provinces: boolean; cities: boolean; contours: boolean; figures: boolean };
export const OVERLAY_NAMES = ["provinces", "cities", "contours", "figures"] as const;

/** The word for a layer or an overlay, each key written out so that
 *  `locale.test.ts` ("writes no message nobody asks for") sees every message
 *  asked for -- a key built from a name is a key it cannot see. */
export function wordOf(name: Layer | keyof Overlays): string {
  switch (name) {
    case "terrain": return t("ui-map-layer-terrain");
    case "relief": return t("ui-map-layer-relief");
    case "biomes": return t("ui-map-layer-biomes");
    case "temperature": return t("ui-map-layer-temperature");
    case "rain": return t("ui-map-layer-rain");
    case "moisture": return t("ui-map-layer-moisture");
    case "provinces": return t("ui-map-layer-provinces");
    case "cities": return t("ui-map-layer-city-lands");
    case "contours": return t("ui-map-layer-contours");
    case "figures": return t("ui-map-layer-figures");
  }
}

export function Switcher({
  inside,
  onInside,
  tethered,
  onTether,
  scouting,
  onScout,
  joining,
  onJoin,
  layer,
  onLayer,
  overlays,
  onOverlays,
}: {
  /** Whether there is an inside to open from here -- floors, a hull's rooms
   *  (D-319, wave 4) -- and whether it is open now. Null: nothing inside. */
  inside: boolean | null;
  onInside: (on: boolean) => void;
  /** Whether the camera is tied to the body -- see `GraphMap`. */
  tethered: boolean;
  onTether: (on: boolean) => void;
  /** Whether the scout's aim is armed: then a tap on the ground names the
   *  point to survey (D-321). Null off the ground -- the sky, a house. */
  scouting: boolean | null;
  onScout: (on: boolean) => void;
  /** Whether the way's aim is armed (D-321 addendum, owner 2026-09-12):
   *  then a tap on a known node names it, and the run lays a way to it.
   *  Null off the ground, as the scout's. */
  joining: boolean | null;
  onJoin: (on: boolean) => void;
  /** What the ground is coloured by, and which overlays are on (D-331). */
  layer: Layer;
  onLayer: (layer: Layer) => void;
  overlays: Overlays;
  onOverlays: (next: Overlays) => void;
}) {
  const word = t(tethered ? "ui-map-cam-tied" : "ui-map-cam-free");
  const scout = t("ui-map-scout");
  const join = t("ui-map-join");
  const door = t(inside ? "ui-map-outside" : "ui-map-inside");
  const layers = t("ui-map-layers");
  //: The layers' menu is the system's one popping layer (`menu.css`,
  //: `popover.ts`): the same thing the header's overflow and the row's
  //: menu are, opened off a button that names the state it holds, shut by
  //: Escape, by a press outside the bar and by the choice itself (owner,
  //: 2026-09-12: it had been a box of its own, of another colour than
  //: every other menu, and on a phone it never showed at all).
  const [menu, setMenu] = useState(false);
  const menuId = useId();
  const bar = useRef<HTMLElement | null>(null);
  const toggle = useRef<HTMLButtonElement | null>(null);
  const pop = useRef<HTMLDivElement | null>(null);
  const close = useCallback(() => setMenu(false), []);
  usePopover({ open: menu, close, anchor: bar, toggle, pop });
  //: A layer chosen shuts the menu -- the button says the choice -- and
  //: the focus goes back to it; an overlay toggled leaves the menu open, as
  //: a check does, so two can be turned in one visit.
  const pickLayer = (name: Layer) => {
    setMenu(false);
    toggle.current?.focus();
    onLayer(name);
  };
  return (
    <nav className="row tabs map-layers" ref={bar}>
      <button
        ref={toggle}
        className={`layers${menu ? "" : " quiet"}`}
        aria-label={t("ui-map-layers-now", { layer: wordOf(layer) })}
        aria-haspopup="menu"
        aria-expanded={menu}
        aria-controls={menuId}
        onClick={() => setMenu((was) => !was)}
      >
        <Glyph name="layers" />
        <span className="tab-word named">{wordOf(layer)}</span>
      </button>
      {menu && (
        <div id={menuId} ref={pop} className="menu map-layer-menu" role="menu" aria-label={layers}>
          {/* Two sets, each a group the role knows -- a menu holds items,
              groups and separators, and a caption is a group's name, not a
              line of its own: the radios make one choice of six, the checks
              are each their own. */}
          <div role="group" aria-labelledby={`${menuId}-ground`}>
            <p id={`${menuId}-ground`} className="menu-ask">{t("ui-map-layers-ground")}</p>
            {LAYERS.map((name) => (
              <button
                key={name}
                role="menuitemradio"
                aria-checked={layer === name}
                onClick={() => pickLayer(name)}
              >
                {wordOf(name)}
              </button>
            ))}
          </div>
          <div role="group" aria-labelledby={`${menuId}-over`}>
            <p id={`${menuId}-over`} className="menu-ask">{t("ui-map-layers-over")}</p>
            {OVERLAY_NAMES.map((name) => (
              <button
                key={name}
                role="menuitemcheckbox"
                aria-checked={overlays[name]}
                onClick={() => onOverlays({ ...overlays, [name]: !overlays[name] })}
              >
                {wordOf(name)}
              </button>
            ))}
          </div>
        </div>
      )}
      <span className="map-sep" aria-hidden="true" />
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
      {/* Scouting is a mode, not a click on empty ground (owner, 2026-09-06):
          pressed, the next tap on the ground is the aim; a tap otherwise is
          nothing but a tap. */}
      {scouting !== null && (
        <button
          className={`scout${scouting ? "" : " quiet"}`}
          aria-pressed={scouting}
          aria-label={scout}
          onClick={() => onScout(!scouting)}
        >
          <Glyph name="eye" />
          <span className="tab-word">{scout}</span>
        </button>
      )}
      {/* A way to a known node is a run with the node for its aim (D-321
          item 3: the second scout of a cell brings home the way): the
          same mode, armed apart, so a tap on a node names it rather than
          opening it. */}
      {joining !== null && (
        <button
          className={`join${joining ? "" : " quiet"}`}
          aria-pressed={joining}
          aria-label={join}
          onClick={() => onJoin(!joining)}
        >
          <Glyph name="way" />
          <span className="tab-word">{join}</span>
        </button>
      )}
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
