// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The scout's aim on the map (D-321): where one may look, and where the
 * finger has pointed.
 *
 * Scouting is a **mode**, not a click on empty ground (owner, 2026-09-06):
 * armed, the next tap on the ground names the point to survey; unarmed, a
 * tap is a tap. What may be named is the ring of the standing node's reach,
 * less the land of every node drawn and the shadow of every way between
 * them (`scout.fieldOf`), and less the water -- which is read off the relief
 * under the point rather than off the graph.
 *
 * Everything here is about that one question, and it is a hook rather than
 * a part of the map because the map already holds the camera, the hand, the
 * sky, the bands and the walker. What the map keeps of the scout is two
 * pieces of state and a line it moves by the ref; the arithmetic of the
 * field, the water and the cursor's line is this module's.
 */

import { useEffect, useMemo, useRef, useState } from "react";

import { useSession } from "../../actions";
import type { MapNode, Peek, RecipeBook } from "../../api";
import { Refused } from "../../session";
import { tilesHeld, useTerrain } from "./Ground";
import {
  geoUnder,
  project,
  UNITS_PER_METRE,
  type Eye,
  type Geo,
} from "./globe";
import { worldAt } from "./hand";
import type { Frame } from "./camera";
import type { Link, Point } from "./model";
import { kindAt, type Warmth } from "./relief";
import {
  fieldOf,
  joinAim,
  metresBetween,
  nearestCell,
  rulesOf,
  scoutable,
  warmthOf,
  type Field,
  type Way,
} from "./scout";

/** What the field says at the aimed point (D-321 addendum): on its way,
 *  here, or refused -- the run's own refusal, said before the run. */
export type Preview = {
  peek: Peek | null;
  trouble: string | null;
  /** Whether the trouble is the world's refusal of the aim -- then the
   *  walk is not offered -- or the wire's, which says nothing of the aim. */
  refused: boolean;
  pending: boolean;
};
const NO_PREVIEW: Preview = { peek: null, trouble: null, refused: false, pending: false };

export type Scout = {
  /** The point named, or nothing: the panel below the map speaks about it. */
  aim: Geo | null;
  /** What the field says there, asked of the server on every aim. */
  preview: Preview;
  /** How far it is from where one stands, metres; null with no aim. */
  metres: number | null;
  /** Whether the next tap on the ground would be taken as an aim. */
  scouting: boolean;
  arm: (on: boolean) => void;
  /** Whether the next tap on a known node names it as the way's far end
   *  (D-321 addendum): the run then lays the way. Armed apart from the
   *  scout's aim, and the one puts the other away. */
  joining: boolean;
  armJoin: (on: boolean) => void;
  /** The node the way is aimed at, or null: the panel's verb reads it. */
  target: string | null;
  /** The hand's tap on a node while the way's aim is armed. */
  join: (node: MapNode) => void;
  /** Take the point back and leave the mode armed: the hand is choosing
   *  again, not done. */
  unaim: () => void;
  /** Done: the point is spent and the mode goes with it. */
  clear: () => void;
  /** Whether there is ground here to scout at all. */
  onGround: boolean;
  /** The field as it is drawn, or null while the mode is off. */
  field: Field | null;
  /** The line from the standing node to the cursor: moved by the ref, off
   *  React, the way the viewBox and the walker's dot are. */
  line: React.RefObject<SVGLineElement | null>;
  followCursor: (e: React.PointerEvent<SVGSVGElement>) => void;
  /** The hand's tap, judged and taken. Does nothing where nothing is armed. */
  tap: (point: Point) => void;
};

export function useScout({
  book,
  byKey,
  here,
  ground,
  edges,
  planet,
  eye,
  radius,
  sphere,
  frame,
}: {
  book: RecipeBook | null;
  byKey: Record<string, MapNode>;
  /** Where the body stands. */
  here: string;
  /** Every drawn node's point, as the scene projected it. */
  ground: ReadonlyMap<string, Point>;
  edges: Link[];
  /** Whose surface is shown, for the relief under the cursor. */
  planet: string | null;
  eye: Eye | null;
  radius: number | null;
  /** Whether the scene is a planet's surface at all: not the sky, not an
   *  inside, and drawn on a globe. */
  sphere: boolean;
  frame: () => Frame;
}): Scout {
  const [aim, setAim] = useState<Geo | null>(null);
  const [scouting, setScouting] = useState(false);
  const [joining, setJoining] = useState(false);
  const [target, setTarget] = useState<string | null>(null);
  //: The peek (D-321 addendum): asked once per aim, forgotten with it. The
  //: answer that comes for a point already taken back is dropped.
  const session = useSession();
  const [preview, setPreview] = useState<Preview>(NO_PREVIEW);
  useEffect(() => {
    if (!aim) {
      setPreview(NO_PREVIEW);
      return;
    }
    let live = true;
    setPreview({ peek: null, trouble: null, refused: false, pending: true });
    session.send<Peek>("explore.peek", { lat: aim.lat, lon: aim.lon }).then(
      (told) => {
        if (live) setPreview({ peek: told, trouble: null, refused: false, pending: false });
      },
      (error: unknown) => {
        if (live)
          setPreview({
            peek: null,
            trouble: error instanceof Error ? error.message : String(error),
            refused: error instanceof Refused,
            pending: false,
          });
      },
    );
    return () => {
      live = false;
    };
  }, [aim, session]);

  const stand = byKey[here]?.place;
  const standing = stand && "lat" in stand ? (stand as Geo) : null;
  const onGround = Boolean(sphere && standing);
  //: How far the standing node can be scouted from: the server's own reach
  //: for it (D-321 item 4), on the personal map's row.
  const reach = byKey[here]?.reach;

  //: What a find needs round a node and how the planet's lattice is laid, off
  //: the vault (D-065): the first widens the circles the field keeps off, the
  //: second decides which point a tap really names.
  const rules = useMemo(() => rulesOf(book, radius), [book, radius]);

  const terrain = useTerrain(planet);
  //: The two thresholds the relief is read by. Off the book rather than
  //: named here: they are the vault's (D-065).
  const bands = useMemo<Warmth | null>(
    () => warmthOf(book?.constants?.["biome.zonal"]),
    [book],
  );

  //: A point named on one ground means nothing on another: walking indoors,
  //: climbing to orbit or opening another planet takes it back. The panel
  //: hides itself either way -- a room has no latitude -- but the point used
  //: to come back on the way out, and its distance would have been measured
  //: against whatever radius was under it by then.
  useEffect(() => {
    if (!onGround) setAim(null);
  }, [onGround]);

  //: A node's point in the frame, whether or not the scene drew it: the
  //: field must keep off ground it cannot see.
  const projected = (place: { lat: number; lon: number }): Point | null => {
    if (!eye || !radius) return null;
    const seen = project(eye, radius, place);
    return seen.front ? { x: seen.x, y: seen.y } : null;
  };

  const field = useMemo(() => {
    if (!scouting || !onGround || !reach) return null;
    const origin = ground.get(here);
    if (!origin) return null;
    //: Every node of this surface the client has been told about, not only
    //: the ones the scene draws. The server measures the room to the nearest
    //: node of the whole surface (`aim.check` over `explore.aim_window_m`),
    //: and a node the scene left out -- a city's member with the cities
    //: closed, a place beyond the two steps the map draws (D-045) -- still
    //: takes its ground. Drawn or not, it is projected the same way, so the
    //: circles land where the eye would put them.
    const placed: { key: string; at: Point; area?: number | null }[] = [];
    for (const node of Object.values(byKey)) {
      const place = node.place;
      if (!place || !("lat" in place) || node.planet !== planet) continue;
      const seen = ground.get(node.key) ?? projected(place);
      if (seen) placed.push({ key: node.key, at: seen, area: node.area });
    }
    const ways: Way[] = [];
    for (const edge of edges) {
      const a = ground.get(edge.a);
      const b = ground.get(edge.b);
      if (a && b) ways.push([a, b]);
    }
    return fieldOf(origin, reach, placed, ways, rules);
    //: `projected` is rebuilt every render and would defeat the memo; the eye
    //: and the radius it closes over are listed instead.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    scouting,
    onGround,
    reach,
    ground,
    byKey,
    planet,
    edges,
    here,
    rules,
    eye,
    radius,
  ]);

  //: Water is the server's refusal to walk on, and the client's to aim at:
  //: read under the point, not off the graph, because the graph has no
  //: nodes out there -- that is the whole point of scouting.
  const isLand = (point: Point): boolean => {
    if (!terrain || !bands || !eye || !radius) return true;
    const geo = geoUnder(eye, radius, point);
    if (!geo) return false;
    const kind = kindAt(terrain, geo, bands, tilesHeld(planet));
    return kind !== "sea" && kind !== "water";
  };
  const mayAim = (point: Point): boolean =>
    field ? scoutable(field, point, isLand(point)) : true;

  const line = useRef<SVGLineElement | null>(null);
  const followCursor = (e: React.PointerEvent<SVGSVGElement>) => {
    const held = line.current;
    if (!held || !field) return;
    const point = worldAt(e.currentTarget.getBoundingClientRect(), frame(), e);
    const ok = mayAim(point);
    held.setAttribute("x1", String(field.origin.x));
    held.setAttribute("y1", String(field.origin.y));
    held.setAttribute("x2", String(point.x));
    held.setAttribute("y2", String(point.y));
    held.setAttribute("visibility", ok ? "visible" : "hidden");
  };

  return {
    aim,
    preview,
    metres:
      aim && standing && radius ? metresBetween(standing, aim, radius) : null,
    scouting,
    arm: (on: boolean) => {
      setScouting(on);
      //: Disarming takes the aim with it: the panel spoke about a point
      //: nobody was going to walk to any more. Arming puts the way's aim
      //: away: one hand, one question.
      if (on) setJoining(false);
      setAim(null);
      setTarget(null);
    },
    joining,
    armJoin: (on: boolean) => {
      setJoining(on);
      if (on) setScouting(false);
      setAim(null);
      setTarget(null);
    },
    target,
    join: (node: MapNode) => {
      //: A known node of this ground, and not the one underfoot (`joinAim`):
      //: the aim is its place, and the server judges it as any aim -- the
      //: reach, the water, the ways and the nodes on the straight line
      //: (`aim.check`) -- and answers with the node already found there
      //: (`explore.peek`).
      if (!joining || !onGround) return;
      const at = joinAim(node, { here, planet });
      if (!at) return;
      setTarget(node.key);
      setAim(at);
    },
    unaim: () => {
      setAim(null);
      setTarget(null);
    },
    clear: () => {
      setAim(null);
      setTarget(null);
      setScouting(false);
      setJoining(false);
    },
    onGround,
    field,
    line,
    followCursor,
    tap: (point: Point) => {
      //: Only with the aim armed, on the ground of one's own planet, and only
      //: within the field: a tap elsewhere is a tap on nothing.
      if (!scouting || !onGround || !eye || !radius || !mayAim(point)) return;
      const at = geoUnder(eye, radius, point);
      if (!at) return;
      //: Pressed to the lattice, as the engine will press it (D-321 item 3):
      //: the cross marks the place that will open, and the panel measures the
      //: distance the server will price. The cell a finger lands on is not
      //: always lawful even inside the ring -- the lattice is coarse against a
      //: reach of tens of metres -- so the nearest lawful one round it is
      //: taken instead of answering the hand with nothing.
      //: With no lattice known there is nothing to press to, and the tap
      //: stands as it is -- the judgement above already passed it.
      if (!rules.step || !rules.cell || !field) {
        setAim(at);
        return;
      }
      //: As deep as the field is wide: a lawful cell anywhere in the ring is
      //: better than none, and the ring is tens of metres across at most.
      const rings = Math.ceil(field.far / UNITS_PER_METRE / rules.cell) + 1;
      const cell = nearestCell(
        rules,
        at,
        (geo) => projected(geo),
        mayAim,
        rings,
      );
      //: Nothing lawful in the whole ring: the tap does nothing. Aiming at
      //: the raw point instead would send a run the server judges by the
      //: **cell** and refuses -- the very "слишком тесно" from inside the
      //: green that this is here to end (owner, 2026-09-09).
      if (cell) setAim(cell.geo);
    },
  };
}
