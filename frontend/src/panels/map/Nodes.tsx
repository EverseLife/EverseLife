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
import { fieldBox, ringPath, wayShadow, type Field } from "./scout";
import { SURFACE, spell, type MapNode } from "../../api";
import { t } from "../../locale";
import { cityLabelEm, cityRadius, citySeen, hereRadius, NODE_R, standsAlone } from "./bands";
import { DOOR_EM, HULL_EM, LABEL_EM, legible } from "./labels";
import { DASH, SPHERE_R, type Link, type Point } from "./model";
import { markWord } from "./words";

/** How far under a hull its name hangs: above it there is already a planet's
 *  name, and two ships at one port would write over it and over each other. */
const HULL_LABEL_Y = 21;
/** How far under a node its door's caption hangs (`.node-door`). */
const DOOR_LABEL_Y = 30;
/** The key a door's caption is weighed under: its node's, and a suffix no
 *  node key can carry -- keys are letters, digits and dots (D-251). */
const DOOR_KEY = (key: string) => `${key} door`;
/** Last of everything for room: see where it is used. */
const DOOR_RANK = 5;

/** What a node's door says of it, if it says anything. Aquatica is named as
 *  out of reach (D-104), a spaceport as the door every ship couples to
 *  (D-206, D-319); anything else has no caption at all. */
function doorWord(node: MapNode): string | null {
  if (node.deferred) return t("ui-map-node-alpha");
  return node.port ? t("ui-map-node-spaceport") : null;
}
import type { MapStub } from "../../api";

type Place = (key: string) => Point | undefined;

/** The glyph of the node's kind, inside its circle. Nothing for what has no kind.
 *  About the node's origin: the node itself is stood by `placeAt`. */
function Sign({
  node,
  settlement,
  moored,
  big,
}: {
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
export function Edges({
  edges,
  at,
  labelled,
  curve,
}: {
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
                x1={a!.x}
                y1={a!.y}
                x2={b!.x}
                y2={b!.y}
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
                {spell(edge.seconds)} ·{" "}
                {t(SURFACE[edge.surface as keyof typeof SURFACE])}
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
export function Stubs({
  stubs,
  curve,
}: {
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
  home = null,
  here = null,
  closed = false,
  radius = NODE_R,
  size = () => 0,
  far = 0,
  scale = 0,
  onPick,
  onMenu,
}: {
  nodes: MapNode[];
  at: Place;
  /** Which node wears the player, if any -- on the road that is none (D-107). */
  standingAt: string | null;
  /** The city the player is in, closed or open: its point is drawn however
   *  small the city is. */
  home?: string | null;
  /** The node the player stands in. Not `standingAt`, which is that node's
   *  delegate in this scene -- with the cities closed that is the city. */
  here?: string | null;
  /** Whether the cities are closed. Not `far > 0`: `far` counts half-octaves
   *  **past** the closing and is nought at the closing itself, so the first
   *  notch out reads the same as the streets. */
  closed?: boolean;
  /** The radius a node is drawn with, map units (`bands.nodeRadius`): a fifth
   *  of the gap the engine seats by, so a circle never covers its neighbour. */
  radius?: number;
  /** The frame's own scale. A closed city's mark and name are drawn at a
   *  size in **pixels**, and pixels are what this divides them into: the
   *  band's `scaleAt(far)` steps by half-octaves, and a mark held to it
   *  swelled by half between one notch and the next. */
  scale?: number;
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
  //: Every node the scene draws, settled before anything is drawn: the names
  //: have to be weighed against each other (`labels`), and that cannot be
  //: done one node at a time inside the map.
  const drawn = nodes.flatMap((node) => {
    const p = at(node.key);
    if (!p) return [];
    //: Not the player's own node: on the road the body stands in no node at
    //: all (D-107), and the node one walked out of must stop wearing the
    //: player. Where the player is, is the dot on the road.
    const mine = node.key === standingAt;
    const near = reachable(node);
    //: Drawn as a city, not "is a city": since D-330 the city's row is an
    //: ordinary node -- the one its bioprinter stands on -- and with the
    //: cities open it is drawn among its own streets, its own size and under
    //: its own name. The big mark and the city's name are what the map puts
    //: on it once the whole city is one point.
    const settlement = group(node.key) && closed;
    //: A closed city is drawn as large as it is, larger the farther out,
    //: and a small one not at all from afar -- but one's own always. **One's
    //: own** is the city one is standing in, not the city node one is
    //: standing on: nobody stands on a city, one stands in its market or its
    //: forge, so read against `mine` alone this exception never fired and the
    //: only city of an alpha world vanished at the first notch out, leaving
    //: an empty map.
    const own = mine || node.key === home;
    if (settlement && !own && !citySeen(size(node.key), far)) return [];
    //: With the cities closed everything on the map is a city, drawn at a
    //: size in pixels; the node underfoot is the one ordinary node among
    //: them, and its six map units shrank below a pixel at the first notch
    //: out. Keeping it in the scene is half the promise -- it has to be seen.
    //:
    //: Unless it stands **for** something already drawn (`bands.standsAlone`).
    const spread = settlement
      ? cityRadius(size(node.key), far, scale)
      : node.key === here && closed && standsAlone(here, home)
        ? hereRadius(far)
        : 0;
    return [{ node, p, mine, near, settlement, spread }];
  });
  //: Which names there is room for. Underfoot first, then the hulls, then
  //: the cities, then what a step reaches, then the rest: a name written over
  //: another is worth less than the map's own bearings.
  //:
  //: A hull comes second because its name is the only thing it has. Every
  //: ship is the same diamond, and one moored among the flats of a domed
  //: city -- twenty of them, all called «Квартира» -- is unfindable without
  //: the word «Заря» beside it.
  const named = legible(
    drawn.flatMap(({ node, p, mine, near, settlement, spread }) => {
      //: A closed city is read from outside itself, and its name is drawn at
      //: a size in pixels like its circle (`bands.cityLabelEm`): weighed for
      //: room at that size too, or the declutter would judge a name three
      //: times the height it is drawn at by the height of a street's.
      const em = settlement
        ? cityLabelEm(far, scale)
        : node.aboard
          ? HULL_EM
          : LABEL_EM;
      const name = {
        key: node.key,
        x: p.x,
        y: node.aboard
          ? p.y + HULL_LABEL_Y
          : p.y - (Math.max(spread, 6) + em / 2),
        text: markWord(node, settlement),
        em,
        rank: mine ? 0 : node.aboard ? 1 : settlement ? 2 : near ? 3 : 4,
      };
      const door = doorWord(node);
      //: A caption is weighed with the names and comes last of all: it says
      //: what a node is, not which one it is, and a hull's name lost under
      //: «КОСМОДРОМ» costs more than the word costs when it is left off.
      return door
        ? [
            name,
            {
              key: DOOR_KEY(node.key),
              x: p.x,
              y: p.y + DOOR_LABEL_Y,
              text: door,
              em: DOOR_EM,
              door: true,
              rank: DOOR_RANK,
            },
          ]
        : [name];
    }),
  );
  return (
    <>
      {drawn.map(({ node, p, mine, near, settlement, spread }) => {
        const chosen = node.key === picked;
        const sphere = Boolean(node.orbit);
        const hull = node.aboard;
        //: How much the stylesheet's own label has to grow for a closed city
        //: to keep its size on the glass (`bands.cityLabelEm`); one for
        //: everything else, and then nothing is transformed at all.
        const grown = settlement ? cityLabelEm(far, scale) / LABEL_EM : 1;
        return (
          <g
            key={node.key}
            //: Stood by a matrix, and drawn about its own origin (`placeAt`):
            //: the circles below take CSS lengths, and half a globe away from
            //: the eye those saturate.
            transform={placeAt(p)}
            style={
              sphere
                ? ({
                    "--pc": `var(--planet-${node.planet})`,
                  } as React.CSSProperties)
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
                <circle
                  cx={0}
                  cy={0}
                  r={mine ? SPHERE_R + 2 : SPHERE_R}
                  className="corona"
                />
                <circle cx={0} cy={0} r={mine ? 9 : 7} className="orb" />
              </>
            ) : (
              <>
                {/* Small, and how small is not a taste: the nodes of a city
                    stand a few metres apart (D-323 addendum), and the circle
                    is a fifth of that gap (`bands.nodeRadius`) so that it
                    never covers its neighbour's. One's own node used to be
                    drawn half again as wide and broke the rule by itself --
                    it is told apart by colour (`.node.me circle`), which
                    costs no room. */}
                <circle
                  cx={0}
                  cy={0}
                  r={
                    settlement || spread
                      ? Math.max(spread, mine ? radius : 0)
                      : radius
                  }
                />
                {settlement && (
                  <circle
                    cx={0}
                    cy={0}
                    r={Math.max(spread, mine ? radius : 0) + 4}
                    className="halo"
                  />
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
              <circle
                cx={0}
                cy={0}
                r={Math.max(spread + 6, mine ? 13 : 11)}
                className="ring"
              />
            )}
            {/* A ship's name hangs below the hull: above it there is already a
                planet's name, and two ships at one port would write over it
                and over each other. */}
            {/* A find has no name (D-321): its sign inside the circle is the
                whole of what it is called, and an empty label is not drawn. */}
            {/* And a name with nowhere to be written is not written: see
                `labels`. */}
            {markWord(node, settlement) && named.has(node.key) && (
              //: A closed city's name is a length in **pixels** and the map's
              //: units are pixels only at scale one, so from far out it wants
              //: to be many units tall. As a `font-size` that fails silently:
              //: past some thousands of units the browser draws no glyphs at
              //: all, and the name vanished exactly where it was needed. The
              //: same size as a transform draws fine -- the stylesheet's own
              //: eight units, scaled.
              <text
                x={0}
                y={
                  hull
                    ? HULL_LABEL_Y
                    : -(
                        Math.max(spread, 6) +
                        (settlement ? cityLabelEm(far, scale) : LABEL_EM) / 2
                      ) / grown
                }
                transform={grown === 1 ? undefined : `scale(${grown})`}
                className="node-label"
              >
                {markWord(node, settlement)}
              </text>
            )}
            {/* Aquatica is drawn precisely because one cannot go there
                (D-104): the map shows the unreachable and says so. The
                spaceport is the one door left (D-206, D-319): every ship
                couples to it, and a port unmarked reads as any other yard.
                Both are weighed for room with the names (`labels`) and both
                give way to one: a word about what a node is is worth less
                than the word for which node it is. */}
            {doorWord(node) && named.has(DOOR_KEY(node.key)) && (
              <text x={0} y={DOOR_LABEL_Y} className="node-door">
                {doorWord(node)}
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

/** The scout's field (D-321 item 4): the ring of the reach in green, the
 *  land of every node and the shadow of every way cut out of it by a mask.
 *  Where the ground is water the server still refuses: the shore is read
 *  at the cursor, not drawn here. */
export function ScoutField({ field, id }: { field: Field; id: string }) {
  //: The mask's box is explicit and in the field's own units
  //: (`scout.fieldBox`): without it the box is taken from the viewport and
  //: rides with the camera.
  const box = fieldBox(field);
  return (
    <g className="scout-field" aria-hidden="true">
      <mask
        id={id}
        maskUnits="userSpaceOnUse"
        x={box.x}
        y={box.y}
        width={box.size}
        height={box.size}
      >
        <path d={ringPath(field)} fill="white" fillRule="evenodd" />
        {field.blocks.map((block, i) => (
          <circle
            key={i}
            transform={placeAt(block.at)}
            r={block.r}
            fill="black"
          />
        ))}
        {field.ways.map((way, i) => {
          const d = wayShadow(field, way);
          return d ? <path key={`w${i}`} d={d} fill="black" /> : null;
        })}
      </mask>
      <path d={ringPath(field)} fillRule="evenodd" mask={`url(#${id})`} />
    </g>
  );
}
