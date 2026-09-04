// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The glass of the bridge display: everything on it that is not the sky.
 *
 * The rest of this client is deliberately not a spacecraft HUD, because the
 * game is about labour, money and arguments, and a cockpit read on every
 * screen would lie about all three (D-055, D-238). The bridge is the one place
 * where the player is not looking at *our* interface but at the ship's own
 * instrument, and an instrument that looks like a table of numbers is the
 * thing out of place there -- so D-317 stops the ban at the window's edge and
 * names this one panel. What may be drawn here, and how slowly each of it may
 * move, is a closed list, and the list lives in the vault (`50-interface/09`
 * P3) so that it is widened by editing a document rather than a stylesheet.
 *
 * Not one colour is added by any of it: the graticule, the sweep, the brackets
 * and the scan are hairlines in the same greys every other panel is drawn with
 * (D-055). Everything below is decoration in the strict sense: it carries no
 * fact the sky does not already carry, it takes no pointer, and a reader is
 * told nothing by it (`aria-hidden`).
 *
 * The two halves are ordered around the world: `Screen` is under the sky --
 * things drawn *on* the tube -- and `Bezel` is over it, the housing and the
 * readouts that no orbit may cross.
 */

import { t } from "../../locale";
import { CENTER, H, W } from "./scope";

/** How long the ticks on the hull's cross are, and where they start. */
const CROSS = { gap: 10, arm: 7 };
/** The bezel's corner brackets: how far in they sit and how long the arms are. */
const BRACKET = { inset: 5, arm: 16 };

/**
 * What is drawn on the tube, under the sky: the graticule and the sweep.
 *
 * The graticule is ruled on the **sky**, not on the glass: `grid` gives the
 * spacing the zoom asks for and the offset that pins the lines to the world's
 * own origin, so the ruling slides under the hull as it moves and opens up as
 * one looks nearer. Ruled on the glass instead it was a decoration that said
 * nothing -- the same squares at every scale, over a display that never pans.
 *
 * There used to be three graduation rings round the middle here as well. They
 * were fixed pixels: they did not move with the zoom, so they measured nothing
 * and could not be made to. The one ring left on this display is a fact and is
 * drawn with the sky (`Chart`) -- how far this hull sees another.
 */
export function Screen({
  mark,
  grid,
}: {
  mark: string;
  /** The graticule's spacing on the glass, and where the world's origin puts
   *  its first line: both in pixels, both moving with the hull and the zoom. */
  grid: { step: number; x: number; y: number };
}) {
  const ruling = `${mark}-grid`;
  const sweep = `${mark}-sweep`;
  return (
    <g aria-hidden="true">
      <defs>
        {/* The graticule is fixed to the glass rather than to the sky: an
            instrument's ruled screen, not a grid the world is laid on. */}
        <pattern
          id={ruling}
          width={grid.step}
          height={grid.step}
          patternUnits="userSpaceOnUse"
          patternTransform={`translate(${grid.x} ${grid.y})`}
        >
          <path className="chart-graticule" d={`M${grid.step} 0H0v${grid.step}`} />
        </pattern>
        <linearGradient id={sweep} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor="var(--base-50)" stopOpacity="0.1" />
          <stop offset="100%" stopColor="var(--base-50)" stopOpacity="0" />
        </linearGradient>
      </defs>

      <rect className="chart-grid" x="0" y="0" width={W} height={H} fill={`url(#${ruling})`} />

      {/* The sweep. It detects nothing -- there is nothing here to detect that
          is not already drawn -- and it is not pretending to: it is the one
          thing on the glass that says the display is on. */}
      <g className="chart-sweep">
        <path
          d={`M${CENTER.x} ${CENTER.y} L${W} ${CENTER.y} L${W} ${CENTER.y - 150} Z`}
          fill={`url(#${sweep})`}
        />
        <line x1={CENTER.x} y1={CENTER.y} x2={W} y2={CENTER.y} />
      </g>

      {/* The cross under the hull: an instrument's own middle, drawn with a gap
          so that the mark of the ship is never buried in it. */}
      <g className="chart-cross">
        <line
          x1={CENTER.x - CROSS.gap - CROSS.arm}
          y1={CENTER.y}
          x2={CENTER.x - CROSS.gap}
          y2={CENTER.y}
        />
        <line
          x1={CENTER.x + CROSS.gap}
          y1={CENTER.y}
          x2={CENTER.x + CROSS.gap + CROSS.arm}
          y2={CENTER.y}
        />
        <line
          x1={CENTER.x}
          y1={CENTER.y - CROSS.gap - CROSS.arm}
          x2={CENTER.x}
          y2={CENTER.y - CROSS.gap}
        />
        <line
          x1={CENTER.x}
          y1={CENTER.y + CROSS.gap}
          x2={CENTER.x}
          y2={CENTER.y + CROSS.gap + CROSS.arm}
        />
      </g>
    </g>
  );
}

/**
 * The housing over the sky: the dimming at the edges, the corner brackets, the
 * edge ruler, the scale, and the legend of the lines ahead.
 *
 * The legend is the one part of this that carries a fact, and it carries it
 * only because three lines that mean three different futures (D-289) cannot be
 * told apart by dash pattern alone. It names what is drawn and nothing else:
 * a line absent from the sky is absent from the legend.
 */
export function Bezel({
  mark,
  zoom,
  sight,
  inertia,
  course,
  plan,
}: {
  mark: string;
  zoom: number;
  sight: boolean;
  inertia: boolean;
  course: boolean;
  plan: boolean;
}) {
  const dim = `${mark}-dim`;
  const legend: { key: string; word: string }[] = [
    ...(sight ? [{ key: "chart-sight", word: t("ui-ship-chart-sight") }] : []),
    ...(inertia ? [{ key: "chart-inertia", word: t("ui-ship-chart-inertia") }] : []),
    ...(course ? [{ key: "chart-course", word: t("ui-ship-chart-course") }] : []),
    ...(plan ? [{ key: "chart-plan", word: t("ui-ship-chart-choice") }] : []),
  ];
  return (
    <g aria-hidden="true" className="chart-bezel">
      <defs>
        {/* The tube is brightest where it is looked at. Faint enough that a
            corridor's readout at the edge is still read, strong enough that
            the frame stops feeling like a rectangle cut out of a page. */}
        <radialGradient id={dim} cx="50%" cy="50%" r="72%">
          <stop offset="55%" stopColor="var(--base-950)" stopOpacity="0" />
          <stop offset="100%" stopColor="var(--base-950)" stopOpacity="0.72" />
        </radialGradient>
      </defs>
      <rect x="0" y="0" width={W} height={H} fill={`url(#${dim})`} />

      {/* The ruler along the top edge: bearing marks, coarser every fifth. */}
      <g className="chart-ruler">
        {Array.from({ length: 40 }, (_, i) => 8 + i * 16).map((x, i) => (
          <line key={x} x1={x} y1="0" x2={x} y2={i % 5 === 0 ? 7 : 4} />
        ))}
      </g>

      {/* The corners. A housing, not a border: the frame's own line is the
          panel's, and these say where the glass ends. */}
      {[
        [BRACKET.inset, BRACKET.inset, 1, 1],
        [W - BRACKET.inset, BRACKET.inset, -1, 1],
        [BRACKET.inset, H - BRACKET.inset, 1, -1],
        [W - BRACKET.inset, H - BRACKET.inset, -1, -1],
      ].map(([x, y, dx, dy]) => (
        <path
          key={`${x}:${y}`}
          className="chart-bracket"
          d={`M${x} ${y + dy * BRACKET.arm}L${x} ${y}L${x + dx * BRACKET.arm} ${y}`}
        />
      ))}

      <text className="chart-scale" x={W - 14} y="22" textAnchor="end">
        {t("ui-ship-chart-scale", { zoom })}
      </text>

      {/* Stacked upwards from the foot, so the list reads top to bottom in the
          order it is written; and clear of the corner bracket beside it. */}
      {legend.map((one, i) => (
        <g
          key={one.key}
          className="chart-key"
          transform={`translate(28 ${H - 16 - (legend.length - 1 - i) * 14})`}
        >
          <line className={one.key} x1="0" y1="-4" x2="22" y2="-4" />
          <text x="28" y="0">
            {one.word}
          </text>
        </g>
      ))}
    </g>
  );
}
