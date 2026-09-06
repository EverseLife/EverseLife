// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * What a node and an edge look like. Nothing here decides anything.
 *
 * The map settles who wears the player, who is a step away, what is picked --
 * questions about the world -- and hands the answers down as flags. This file
 * turns a flag into a circle, a glyph and a label, and that is the whole of its
 * business: a place on the map looks the same whatever reasoning put it there.
 *
 * The shapes are worth naming, because they are the map's vocabulary:
 *
 * - a **planet** is a body with a corona in its own colour, not a circle on a
 *   spring: Terra is that blue seen from Terra and from Pyroxis (D-230);
 * - a **ship** is a hull -- neither a planet nor a place (D-201) -- and wears
 *   its name below, where a planet's name is not already written;
 * - a **settlement** carries a halo: it opens into a layer of its own;
 * - everything else is a circle with the glyph of its kind inside (D-238), so
 *   the node says what it is before it is clicked.
 */

import { SHAPES } from "../../glyphs";
import { nodeGlyph } from "../../marks";
import { placeAt, project, type Eye, type Geo } from "./globe";
import { SURFACE, spell, type MapNode } from "../../api";
import { t } from "../../locale";
import { cityRadius, citySeen } from "./bands";
import { DASH, SPHERE_R, type Link, type Point } from "./model";
import type { MapStub } from "../../api";

type Place = (key: string) => Point | undefined;

/** The glyph of the node's kind, inside its circle. Nothing for what has no kind.
 *  About the node's origin: the node itself is stood by `placeAt`. */
function Sign({ node, settlement, moored, big }: {
  node: MapNode;
  settlement: boolean;
  /** A ship lies at this port. */
  moored: boolean;
  big: boolean;
}) {
  const sign = nodeGlyph({
    emblem: node.emblem,
    features: node.features,
    settlement,
    port: node.port,
    moored,
  });
  if (!sign) return null;
  //: Small, as the node is (owner, 2026-09-06).
  const size = big ? 10 : 8;
  return (
    <svg
      x={-size / 2}
      y={-size / 2}
      width={size}
      height={size}
      viewBox="0 0 16 16"
      className="node-mark"
      aria-hidden="true"
    >
      <path
        d={SHAPES[sign]}
        fill="none"
        strokeWidth={1.6}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

//: `Edges`, not `Roads`: the panel of roadworks next door is `map/Roads.tsx`,
//: and two things called the same in one directory is a minute lost every time
//: an import is written. This one draws the graph's edges, road or gangway.
export function Edges({ edges, at, labelled, curve }: {
  edges: Link[];
  at: Place;
  /** In space an edge carries no label -- see below. */
  labelled: boolean;
  /** On a globe an edge is a great-circle arc, cut at the horizon (D-319):
   *  the visible part of it, or nothing where the scene is flat or the edge
   *  has an end with no place on the sphere -- a gangway from a hull. */
  curve?: (edge: Link) => Point[] | null;
}) {
  return (
    <>
      {edges.map((edge) => {
        const run = curve?.(edge) ?? null;
        const a = at(edge.a);
        const b = at(edge.b);
        if (!run && (!a || !b)) return null;
        return (
          <g key={`${edge.a}|${edge.b}`} className="road">
            {run ? (
              <polyline
                points={run.map((p) => `${p.x},${p.y}`).join(" ")}
                className={`edge ${edge.surface}`}
                strokeDasharray={DASH[edge.surface]}
              />
            ) : (
              <line
                x1={a!.x} y1={a!.y} x2={b!.x} y2={b!.y}
                className={`edge ${edge.surface}`}
                strokeDasharray={DASH[edge.surface]}
              />
            )}
            {/* The way's time and kind are told on hover, not written on the
                map (owner, 2026-09-06): with honest metres the eye sees the
                distance, and the kind is the line's own drawing. In space an
                edge is a gangway and nothing else (D-201), and says nothing. */}
            {labelled && (
              <title>
                {spell(edge.seconds)} · {t(SURFACE[edge.surface as keyof typeof SURFACE])}
              </title>
            )}
          </g>
        );
      })}
    </>
  );
}

/** The ways out of sight (D-319 item 6): a short dashed piece of each edge
 *  that leads into the fog, from its seen end. Drawn only where the scene
 *  gives it a run -- on the globe; the inside has no fog. */
export function Stubs({ stubs, curve }: {
  stubs: MapStub[];
  curve: (stub: MapStub) => Point[] | null;
}) {
  return (
    <g className="stubs">
      {stubs.map((stub, i) => {
        const run = curve(stub);
        if (!run) return null;
        return (
          <polyline
            key={`${stub.from}|${i}`}
            points={run.map((p) => `${p.x},${p.y}`).join(" ")}
            className={`edge stub ${stub.surface}`}
            strokeDasharray={DASH[stub.surface]}
          />
        );
      })}
    </g>
  );
}

export function Nodes({
  nodes,
  at,
  standingAt,
  picked,
  reachable,
  group,
  size = () => 0,
  far = 0,
  onPick,
  onMenu,
}: {
  nodes: MapNode[];
  at: Place;
  /** Which node wears the player, if any -- on the road that is none (D-107). */
  standingAt: string | null;
  picked: string | null;
  /** Whether a step leads there. The map knows; the drawing only lights up. */
  reachable: (node: MapNode) => boolean;
  /** Whether the node opens into a layer of its own. */
  group: (key: string) => boolean;
  /** How many nodes hang under it: a closed city's circle is that many
   *  pixels in radius, so its size is read from afar. */
  size?: (key: string) => number;
  /** How far out the frame is past the cities' closing (`bands.farOf`):
   *  the circles grow with it, and the small cities fade. */
  far?: number;
  onPick: (node: MapNode) => void;
  onMenu: (node: MapNode, spot: { x: number; y: number }) => void;
}) {
  return (
    <>
      {nodes.map((node) => {
        const p = at(node.key);
        if (!p) return null;
        //: Not the player's own node: on the road the body stands in no node at
        //: all (D-107), and the node one walked out of must stop wearing the
        //: player. Where the player is, is the dot on the road.
        const mine = node.key === standingAt;
        const near = reachable(node);
        const settlement = group(node.key);
        //: A closed city is drawn as large as it is, larger the farther out,
        //: and a small one not at all from afar -- but one's own always.
        if (settlement && !mine && !citySeen(size(node.key), far)) return null;
        const spread = settlement ? cityRadius(size(node.key), far) : 0;
        const chosen = node.key === picked;
        const sphere = Boolean(node.orbit);
        const hull = node.aboard;
        return (
          <g
            key={node.key}
            //: Stood by a matrix, and drawn about its own origin (`placeAt`):
            //: the circles below take CSS lengths, and half a globe away from
            //: the eye those saturate.
            transform={placeAt(p)}
            style={
              sphere
                ? ({ "--pc": `var(--planet-${node.planet})` } as React.CSSProperties)
                : undefined
            }
            className={`node ${sphere ? "sphere" : ""} ${hull ? "ship" : ""} ${
              node.faded ? "faded" : ""
            } ${
              node.deferred ? "later" : ""
            } ${mine ? "me" : ""} ${near || settlement ? "near" : ""}${
              chosen ? " picked" : ""
            }`}
            //: A press on a node is only ever a pick -- a node is not dragged
            //: (D-237) -- and it does not reach the field beneath, so picking
            //: never pans the map by the two pixels a hand moves while clicking.
            onPointerDown={(e) => {
              e.stopPropagation();
              onPick(node);
            }}
            onContextMenu={(e) => {
              e.preventDefault();
              onMenu(node, { x: e.clientX, y: e.clientY });
            }}
          >
            {hull ? (
              <path className="hull" d="M0 -8 L6 0 L0 8 L-6 0 Z" />
            ) : sphere ? (
              <>
                <circle cx={0} cy={0} r={mine ? SPHERE_R + 2 : SPHERE_R} className="corona" />
                <circle cx={0} cy={0} r={mine ? 9 : 7} className="orb" />
              </>
            ) : (
              <>
                {/* Small: the nodes of a city stand a few metres apart (D-323
                    addendum), and a wide circle over each would cover its
                    neighbour's. */}
                <circle cx={0} cy={0} r={settlement ? Math.max(spread, mine ? 9 : 0) : mine ? 9 : 6} />
                {settlement && (
                  <circle cx={0} cy={0} r={Math.max(spread, mine ? 9 : 0) + 4} className="halo" />
                )}
                <Sign
                  node={node}
                  settlement={settlement}
                  moored={Boolean(node.moored)}
                  big={mine}
                />
              </>
            )}
            {chosen && (
              <circle cx={0} cy={0} r={Math.max(spread + 6, mine ? 13 : 11)} className="ring" />
            )}
            {/* A ship's name hangs below the hull: above it there is already a
                planet's name, and two ships at one port would write over it
                and over each other. */}
            {/* A find has no name (D-321): its sign inside the circle is the
                whole of what it is called, and an empty label is not drawn. */}
            {node.name && (
              <text x={0} y={hull ? 21 : -(Math.max(spread, 6) + 3)} className="node-label">
                {node.name}
              </text>
            )}
            {/* Aquatica is drawn precisely because one cannot go there (D-104):
                the map shows the unreachable and says so. */}
            {node.deferred && (
              <text x={0} y={30} className="node-door">
                {t("ui-map-node-alpha")}
              </text>
            )}
            {/* The spaceport is the one door left (D-206, D-319): every ship
                couples to it, and a port unmarked reads as any other yard. */}
            {node.port && (
              <text x={0} y={30} className="node-door">
                {t("ui-map-node-spaceport")}
              </text>
            )}
          </g>
        );
      })}
    </>
  );
}


/** The outlines of the cities (D-323 addendum): each city's land, the
 *  discs of its nodes joined and rounded, as a contour -- no fill, no
 *  circle. Drawn at every scale: from afar a hairline blot beside the
 *  city's circle, close up the city's own edge among its streets. */
export function Outlines({
  outlines,
  eye,
  radius,
  open,
}: {
  outlines: ReadonlyMap<string, Geo[][]>;
  eye: Eye;
  radius: number;
  /** Whether the cities are open: near, the edge is dashed among the
   *  streets; from afar it is a line, a dash being no line at all. */
  open: boolean;
}) {
  return (
    <g className={`territory ${open ? "near" : "far"}`} aria-hidden="true">
      {[...outlines.entries()].map(([city, loops]) => {
        const d = loops
          .map((loop) => {
            const seen = loop.map((p) => project(eye, radius, p));
            //: A city is small: on the far side whole, or seen whole.
            if (!seen.every((p) => p.front)) return "";
            return `M${seen.map((p) => `${p.x},${p.y}`).join("L")}Z`;
          })
          .join("");
        return d ? <path key={city} className="city-edge" d={d} /> : null;
      })}
    </g>
  );
}

/** The scout's aim on the ground (D-321): a small cross where the finger
 *  tapped, stood like a node, the same size at any zoom. */
export function Aim({ at }: { at: { x: number; y: number; front: boolean } }) {
  if (!at.front) return null;
  return (
    <g className="aim" transform={placeAt(at)} aria-hidden="true">
      <circle cx={0} cy={0} r={7} />
      <path d="M-11 0H-4M4 0H11M0 -11V-4M0 4V11" />
    </g>
  );
}
