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
import { midOf } from "./globe";
import { SURFACE, spell, type MapNode } from "../../api";
import { t } from "../../locale";
import { DASH, SPHERE_R, type Link, type Point } from "./model";
import type { MapStub } from "../../api";

type Place = (key: string) => Point | undefined;

/** The glyph of the node's kind, inside its circle. Nothing for what has no kind. */
function Sign({ node, at, settlement, moored, big }: {
  node: MapNode;
  at: Point;
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
  const size = big ? 14 : 12;
  return (
    <svg
      x={at.x - size / 2}
      y={at.y - size / 2}
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
        //: The label sits on the middle of what is drawn: the arc's, or the
        //: chord's where the edge is a line.
        const mid = run ? midOf(run) : { x: (a!.x + b!.x) / 2, y: (a!.y + b!.y) / 2 };
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
            {/* In space an edge is a gangway and nothing else: the only thing
                coupled to a planet is a ship standing at its port (D-201).
                "21 s of paved highway" would be a road's label on something
                that is not a road, so the tie is drawn bare. */}
            {labelled && (
              <text x={mid.x} y={mid.y - 6} className="edge-label">
                {spell(edge.seconds)} · {t(SURFACE[edge.surface as keyof typeof SURFACE])}
              </text>
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
        const chosen = node.key === picked;
        const sphere = Boolean(node.orbit);
        const hull = node.aboard;
        return (
          <g
            key={node.key}
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
              <path
                className="hull"
                d={`M${p.x} ${p.y - 8} L${p.x + 6} ${p.y} L${p.x} ${p.y + 8} L${
                  p.x - 6
                } ${p.y} Z`}
              />
            ) : sphere ? (
              <>
                <circle cx={p.x} cy={p.y} r={mine ? SPHERE_R + 2 : SPHERE_R} className="corona" />
                <circle cx={p.x} cy={p.y} r={mine ? 9 : 7} className="orb" />
              </>
            ) : (
              <>
                <circle cx={p.x} cy={p.y} r={mine ? 14 : settlement ? 12 : 10} />
                {settlement && (
                  <circle cx={p.x} cy={p.y} r={mine ? 18 : 16} className="halo" />
                )}
                <Sign
                  node={node}
                  at={p}
                  settlement={settlement}
                  moored={Boolean(node.moored)}
                  big={mine}
                />
              </>
            )}
            {chosen && <circle cx={p.x} cy={p.y} r={mine ? 20 : 18} className="ring" />}
            {/* A ship's name hangs below the hull: above it there is already a
                planet's name, and two ships at one port would write over it
                and over each other. */}
            {/* A find has no name (D-321): its sign inside the circle is the
                whole of what it is called, and an empty label is not drawn. */}
            {node.name && (
              <text x={p.x} y={hull ? p.y + 21 : p.y - 20} className="node-label">
                {node.name}
              </text>
            )}
            {/* Aquatica is drawn precisely because one cannot go there (D-104):
                the map shows the unreachable and says so. */}
            {node.deferred && (
              <text x={p.x} y={p.y + 30} className="node-door">
                {t("ui-map-node-alpha")}
              </text>
            )}
            {/* The spaceport is the one door left (D-206, D-319): every ship
                couples to it, and a port unmarked reads as any other yard. */}
            {node.port && (
              <text x={p.x} y={p.y + 30} className="node-door">
                {t("ui-map-node-spaceport")}
              </text>
            )}
          </g>
        );
      })}
    </>
  );
}

/** The borders of the open cities: where a city ends (plan §2). */
export function Borders({ rings }: { rings: readonly { city: string; ring: { cx: number; cy: number; r: number } }[] }) {
  return (
    <g className="borders" aria-hidden="true">
      {rings.map(({ city, ring }) => (
        <circle key={city} className="border" cx={ring.cx} cy={ring.cy} r={ring.r} />
      ))}
    </g>
  );
}
