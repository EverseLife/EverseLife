// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The map is one graph, four display layers (D-045, D-097).
 *
 * The whole universe is a graph of locations, and one walks only on it.
 * Layers are a display abstraction so that the graph can be navigated:
 *
 * - **Space** -- planets and ships;
 * - **Planet** -- cities and large solitary locations;
 * - **City** -- the built-up area: rings around the bioprinter;
 * - **Location** -- sub-nodes: floors of a house, rooms of a complex.
 *
 * An upper-layer node is its group's delegate: a click on "Terra's Capital" on
 * the planet layer expands its built-up area, and the road "to the capital"
 * actually leads to a specific entry node. Edges between groups are projected
 * onto delegates -- the graph stays one and the same.
 *
 * ## The map is a place, not a picture of one (D-237)
 *
 * A node stands where the server says it stands, and it says the same thing to
 * everybody and the same thing tomorrow. This used not to be so: the layout was
 * settled by springs in the client, and a spring layout has no preferred
 * orientation -- the same three nodes came out turned differently on two
 * openings, turned differently again for the neighbour looking at the same
 * city, and turned differently once more after a find. Nobody could say "the
 * mine is north of the gate", because there was no north and no gate to be
 * north of, and nothing anyone remembered about the way they had come was worth
 * remembering. Now there is: the map is the same map, and walking it teaches
 * it. Hence also no dragging -- a map somebody rearranged is a map only they
 * have -- and no rotation to be confused by.
 *
 * `map/layout` still exists for the two cases the server has no place for: a
 * world caught mid-deploy, and a hull in the sky. It settles in one synchronous
 * pass, before the first frame, so the map never appears crawling into place.
 *
 * ## Where you stand is the middle of it
 *
 * The camera follows the body: your node is in the centre of the frame, and it
 * stays there when you walk. Around it the map reaches `DEPTH` steps of the
 * graph and no further -- where you can go, and what you would see from there.
 * The rest of the planet is not hidden out of secrecy: it is simply not the
 * decision in front of you, and its labels were overwriting the three nodes
 * that were.
 *
 * While walking, the dot creeps along the edge, and nowhere can be entered.
 * Arrived -- "Enter".
 */

import { useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api";
import { type Look, type MapNode, type WorldMap } from "../api";
import { useActions, useSession } from "../actions";
import { createCamera, viewBoxOf, type Camera } from "./map/camera";
import { UNFLAG, useKept } from "../kept";
import { t } from "../locale";
import { PHONE } from "../narrow";
import { cityWord } from "../planets";
import { Inspector } from "./map/Inspector";
import { NodeMenu } from "./map/NodeMenu";
import { Edges, Nodes } from "./map/Nodes";
import { useHand } from "./map/hand";
import { settle } from "./map/layout";
import { SkyBackdrop, SkyClock } from "./map/Sky";
import { Switcher } from "./map/Switcher";
import { useSky } from "./map/useSky";
import {
  LAYERS,
  delegate,
  homeCity,
  journeyOf,
  nearby,
  offworld,
  sceneKey,
  type LayerId,
  type Link,
  type Point,
} from "./map/model";
import { STAR, horizon } from "./map/orbits";

/**
 * Whether the camera was left tied to the body (D-238; that it survives a
 * reload is D-298).
 *
 * It outlives the panel, which is unmounted every time one looks at the
 * location tab -- a player who set the camera loose to watch a road would find
 * it tied again on coming back, and a setting that has to be made anew after
 * every glance elsewhere is a setting nobody uses -- and it outlives the
 * reload as well (`kept.ts`): it is a way of looking that the player chose,
 * the same kind of choice as the sidebar's fold.
 *
 * **The layer beside it is not**, and the difference is the whole of what
 * `kept.ts` will and will not remember. `layer === null` is not "no choice
 * made": it is the working state "show the height I am standing at", and
 * nothing ever writes it back -- the switcher hands a layer, `expand` hands
 * "planet" or "city". While the panel is unmounted on every look at the
 * location tab, the null returns by itself; in storage it never would, so one
 * glance at the sky would open the map on the sky in every node afterwards,
 * with nothing in the interface able to undo it. The city it was focused on
 * would not come back with it either: `cityFocus` is cleared on every move.
 * A pointer relative to the body is not a setting.
 */
const CAMERA = "everselife.map.tethered";

/**
 * How close the frame starts on a phone (brief section 9). Twice: the field
 * there is 375px against a desktop's ~1000, and at 2 a node's name comes out
 * at the size the desktop reads it. Well inside what the hand may zoom to
 * (`map/hand`), so nothing about panning or pinching is special-cased.
 */
const PHONE_SCALE = 2;

type Props = {
  look: Look;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  onEnter: () => void;
  /** Which layer to open on: the ship's console opens on space (D-230). */
  initialLayer?: LayerId;
};

export function GraphMap({ look, onEnter, initialLayer }: Omit<Props, "busy" | "act">) {
  //: The map itself performs nothing: it draws, pans and picks. Every action --
  //: setting off, laying a road, going out to explore -- belongs to the
  //: inspector beside it, which keeps its own waiting and its own refusal.
  const { busy } = useActions();
  //: The map is answered from where the body stands (D-240), so the read
  //: carries the session's token: without it the server shows the sky alone.
  const session = useSession();

  const [world, setWorld] = useState<WorldMap | null>(null);
  const here = look.node?.key ?? "";
  //: The map grows by exploration (D-152), and a found node must appear by
  //: itself. We reread it when what could have changed the map changes: own
  //: node, the set of exits from it and the scout's return. One load on first
  //: show lasted exactly until the first find.
  const exits = (look.exits ?? []).map((path) => path.key).join("|");
  const exploring = look.survey?.returns_at ?? "";
  useEffect(() => {
    void api.worldMap(session.token).then(setWorld);
    //: The token is read inside and is the session's own for its whole life:
    //: it is not a reason to reread the map, and the reasons are listed here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [here, exits, exploring]);
  const ongoing = look.travel ?? null;
  //: Ships are not on the public map at all (D-201): from a distance a ship is
  //: a single hull on the space layer and nothing more. What is close enough
  //: to see arrives with `look` -- the ship moored at the pier one stands on,
  //: or the rooms of the one being stood in -- so a ship appears on walking up
  //: to it and is gone on walking away.
  //: Keyed by what the ships **are**, not by the object carrying them: `look`
  //: arrives anew every few seconds, and merging on its identity rebuilt the
  //: whole map -- and with it the layout and the simulation -- on every poll.
  const sighted = (look.ships?.nodes ?? []).map((node) => node.key).join("|");
  const map = useMemo<WorldMap | null>(() => {
    const seen = look.ships;
    if (!world || !seen) return world;
    return {
      ...world,
      nodes: [...world.nodes, ...seen.nodes],
      edges: [...world.edges, ...seen.edges],
    };
    //: `look.ships` is read inside and keyed by `sighted` outside: the same
    //: keys mean the same ships, and the linter cannot be shown that.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [world, sighted]);
  const byKey = useMemo(() => {
    const out: Record<string, MapNode> = {};
    for (const node of map?.nodes ?? []) out[node.key] = node;
    return out;
  }, [map]);

  /** The node's delegate on the layer: climb the parents up to a node of this layer. */
  const repr = useMemo(
    () => (key: string, layer: LayerId): string | null => delegate(byKey, key, layer),
    [byKey],
  );

  //: The default layer is the one you stand on; explicit expansion lives until
  //: the transit. Deliberately **not** remembered past the panel -- see the
  //: note on `CAMERA` above for what storing it did to the map.
  const [layer, setLayer] = useState<LayerId | null>(initialLayer ?? null);
  //: The node the inspector talks about. Where you stand, until you pick another.
  const [picked, setPicked] = useState<string | null>(null);
  //: A right-click menu on a node. A left click picks -- which is what makes a
  //: click predictable -- and this is the shortcut for whoever already knows
  //: where they are going and does not want the column in between.
  const [menu, setMenu] = useState<{ key: string; x: number; y: number } | null>(null);
  /**
   * Whether the camera is tied to the body (D-238).
   *
   * Tethered -- the default, and what the map has always done: you are the
   * middle of the frame, the frame comes with you, and the hand may only
   * change how much of the world is in it. Loose, the frame is the hand's:
   * it stays where it was put, and a walk does not drag it along -- which is
   * how one watches a caravan arrive at a node one is not standing in.
   */
  //: Tied is the default, hence the wire whose default is yes: with `FLAG` a
  //: deliberate "loose" would leave no key and read back as tied.
  const [tethered, tether] = useKept(CAMERA, true, UNFLAG);
  const [cityFocus, setCityFocus] = useState<string | null>(null);
  //: Whose surface the planet layer shows. There are four planets in the sky
  //: now, and "everything of layer `planet`" would mix their nodes into one
  //: heap the first time a second planet gets a node of its own.
  const [planetFocus, setPlanetFocus] = useState<string | null>(null);
  useEffect(() => {
    setCityFocus(null);
    setPlanetFocus(null);
    setPicked(null);
    setMenu(null);
  }, [here]);

  const cities = useMemo(() => {
    const out = new Set<string>();
    for (const node of map?.nodes ?? []) {
      if (node.layer === "city" && node.parent) out.add(node.parent);
    }
    return out;
  }, [map]);
  //: The city above where you stand, or -- aboard a moored ship, where there
  //: is no city above the hull at all -- the one the gangway leads into.
  const myCity = homeCity(byKey, here, look.exits ?? []);
  const focus = cityFocus ?? myCity ?? [...cities].sort()[0] ?? null;

  const locationBase =
    byKey[here]?.layer === "location" ? (byKey[here]?.parent ?? here) : here;
  const hasSubnodes = useMemo(
    () =>
      (map?.nodes ?? []).some(
        (node) => node.layer === "location" && node.parent === locationBase,
      ),
    [map, locationBase],
  );

  const mySphere = byKey[repr(here, "space") ?? ""]?.planet ?? byKey[here]?.planet ?? null;
  const sphereShown = planetFocus ?? mySphere;
  //: The built-up layer is named by the planet it is on (D-230): a camp on
  //: Pyroxis, an abandoned city on Aurora. The word follows the planet whose
  //: surface is shown, which is the one the city tab would open.
  const layers = LAYERS.filter(
    (option) =>
      (option.id !== "location" || hasSubnodes) &&
      (option.id !== "city" || cities.size > 0),
  ).map((option) => ({
    id: option.id,
    mark: option.mark,
    //: The switcher is handed words, not keys: the city's is not in the locale
    //: at all -- it follows the planet whose surface is shown (D-230).
    label: option.id === "city" ? cityWord(sphereShown).name : t(option.word),
  }));
  const desired: LayerId =
    layer ?? ((byKey[here]?.layer as LayerId | undefined) ?? "planet");
  const currentLayer: LayerId = layers.some((s) => s.id === desired)
    ? desired
    : "planet";

  /** Where you stand, as this layer draws it. Null when you are not on it at all. */
  const myRepr = repr(here, currentLayer);
  //: The sky is a layer apart at every step below: it is not laid out, not
  //: windowed by distance in edges, and it moves on its own.
  const orbiting = currentLayer === "space";
  const epoch = look.clock?.epoch ?? null;

  //: Everything this layer holds: one planet's surface, one city, one house.
  const onLayer = useMemo(() => {
    return (map?.nodes ?? []).filter((node) => {
      if (node.layer !== currentLayer) return false;
      if (currentLayer === "city") return node.parent === focus;
      if (currentLayer === "location") return node.parent === locationBase;
      if (currentLayer === "planet") return !sphereShown || node.planet === sphereShown;
      return true;
    });
  }, [map, currentLayer, focus, locationBase, sphereShown]);

  //: Every edge of the world projected onto this layer: a road from a city
  //: gate to a field joins, here, the city and the field. The shortest of
  //: several, because two nodes joined twice are drawn once.
  const layerEdges = useMemo(() => {
    const seen = new Map<string, Link>();
    const keys = new Set(onLayer.map((node) => node.key));
    for (const edge of map?.edges ?? []) {
      const pa = repr(edge.a, currentLayer);
      const pb = repr(edge.b, currentLayer);
      if (!pa || !pb || pa === pb) continue;
      if (!keys.has(pa) || !keys.has(pb)) continue;
      const id = [pa, pb].sort().join("|");
      const known = seen.get(id);
      if (!known || edge.seconds < known.seconds) {
        seen.set(id, { a: pa, b: pb, surface: edge.surface, seconds: edge.seconds });
      }
    }
    return [...seen.values()];
  }, [map, onLayer, repr, currentLayer]);

  /**
   * What is actually drawn: `DEPTH` steps of the graph around where you stand.
   *
   * The sky is the exception and has to be: there is no walking between
   * planets, so a distance in edges means nothing there -- the whole system is
   * one view, and always was.
   *
   * Looking at somebody else's city or another planet there is no node of
   * yours to measure from, and then the group is shown whole: a window with no
   * centre would be an empty screen.
   */
  const visible = useMemo(() => {
    if (currentLayer === "space") return onLayer;
    const near = nearby(
      myRepr,
      onLayer.map((node) => node.key),
      layerEdges,
    );
    return onLayer.filter((node) => near.has(node.key));
  }, [onLayer, layerEdges, myRepr, currentLayer]);

  const shownEdges = useMemo(() => {
    const keys = new Set(visible.map((node) => node.key));
    return layerEdges.filter((edge) => keys.has(edge.a) && keys.has(edge.b));
  }, [layerEdges, visible]);

  // --- where everything stands ----------------------------------------------

  const svgRef = useRef<SVGSVGElement | null>(null);

  /**
   * The camera (`map/camera`): outside React, painted straight onto the
   * `viewBox`. The render reads the same object, so a render that happens for
   * its own reasons never puts back a frame the animation has moved on from.
   */
  const camera = useRef<Camera | null>(null);
  //: A phone's field is a third of a desktop's width, and the same frame
  //: over it drew a node's name at five pixels. The frame starts twice as
  //: close there: the body's neighbourhood, legible, and the rest a pan away.
  //: Asked once, at creation, not subscribed to: the map is remounted when
  //: a phone changes section, and a desktop that narrows keeps the frame it
  //: had -- a hook here would redraw forty nodes for a value read once.
  if (!camera.current) {
    camera.current = createCamera({
      onFrame: (f) => svgRef.current?.setAttribute("viewBox", viewBoxOf(f)),
      scale: window.matchMedia(PHONE).matches ? PHONE_SCALE : 1,
      still: () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    });
  }
  const cam = camera.current;
  //: Nothing of the camera outlives the map.
  useEffect(() => () => cam.stop(), [cam]);

  /**
   * The layer's layout, whole and finished before it is drawn.
   *
   * Almost all of it is simply read off the nodes: the server gives every one
   * of them a place when it is created (D-237). What is left over -- a world
   * caught between the deploy and the catching-up seed -- is settled around
   * those in one synchronous pass, so the map is never seen crawling.
   */
  const ground = useMemo(() => {
    //: The sky is nobody's ground: there every point comes from the clock, and
    //: settling springs whose result is thrown away is pure work.
    if (orbiting) return new Map<string, Point>();
    const given = new Map<string, Point>();
    for (const node of visible) if (node.place) given.set(node.key, node.place);
    return settle(
      visible.map((node) => node.key),
      shownEdges,
      given,
    );
  }, [visible, shownEdges, orbiting]);

  useEffect(() => {
    if (!menu) return;
    const shut = () => setMenu(null);
    const key = (e: KeyboardEvent) => e.key === "Escape" && setMenu(null);
    window.addEventListener("pointerdown", shut);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("pointerdown", shut);
      window.removeEventListener("keydown", key);
    };
  }, [menu]);

  // --- the system: orbits instead of springs --------------------------------

  //: The sky keeps its own places and its own clock: a planet is where the
  //: hour puts it, and no layout has an opinion about that (`map/useSky`).
  const sky = useSky({
    visible,
    epoch,
    orbiting,
    spaceRepr: (key: string) => repr(key, "space"),
    horizon: horizon(map?.routes),
  });
  const { fit } = sky;

  // --- mouse: pan, zoom, pick -----------------------------------------------

  //: What a hand may do to the frame lives in `map/hand`: the rule differs by
  //: whether the camera is tied to the body, and it is one rule in one place
  //: rather than a check repeated at every handler.
  const { grabField, movePointer, releasePointer, zoom, zoomBy } = useHand({
    cam,
    svg: svgRef,
    tethered,
    ready: Boolean(map) && visible.length > 0,
  });

  // --- node behaviour -------------------------------------------------------

  const walkTargets = useMemo(() => {
    const out: Record<string, { key: string; seconds: number }> = {};
    for (const exit of look.exits ?? []) {
      const p = repr(exit.key, currentLayer);
      if (!p || p === repr(here, currentLayer)) continue;
      const known = out[p];
      if (!known || exit.seconds < known.seconds) {
        out[p] = { key: exit.key, seconds: exit.seconds };
      }
    }
    return out;
  }, [look.exits, repr, here, currentLayer]);

  const groups = useMemo(() => {
    const out = new Set<string>();
    for (const node of map?.nodes ?? []) if (node.parent) out.add(node.parent);
    return out;
  }, [map]);

  /**
   * Where a key is drawn: the ground for the layers one walks, the clock for
   * the sky. Kept in a ref as well as read directly, because the walker's own
   * frame loop asks outside of React's rendering.
   */
  const where = useRef<(key: string) => Point | undefined>(() => undefined);
  where.current = (key: string) =>
    orbiting ? sky.places.current.get(key) : ground.get(key);

  /**
   * The camera follows the body (D-237): your node is the middle of the frame.
   *
   * Re-aimed when you move, when the layer changes and when another city or
   * planet is opened -- and at no other moment, so a hand that panned or zoomed
   * keeps what it did until the next step. Standing on no node of this layer at
   * all -- somebody else's city -- the frame opens on its first node, because a
   * camera aimed at nothing shows nothing.
   */
  const skyPlaces = sky.places;
  //: Which scene the frame was last aimed at. A different one shares no
  //: coordinates with this one -- another layer, another city, another
  //: planet -- so the frame is cut to it rather than flown across nothing.
  const shownScene = useRef<string | null>(null);

  //: Read through a ref, and deliberately not depended on: the layout is
  //: rebuilt on every push from the server, and depending on it re-aimed the
  //: frame every few seconds -- dragging back, unasked, the hand that had
  //: just panned somewhere to look. The **reasons** to re-aim are below.
  const groundRef = useRef(ground);
  groundRef.current = ground;
  const drawn = Boolean(map);
  useEffect(() => {
    const laid = groundRef.current;
    const middle = orbiting
      ? (skyPlaces.current.get(myRepr ?? "") ?? STAR)
      : (laid.get(myRepr ?? "") ?? [...laid.values()][0]);
    if (!middle) return;
    const scene = sceneKey(currentLayer, focus, sphereShown);
    const cut = shownScene.current !== scene;
    shownScene.current = scene;
    //: A new scene is moved to **whatever else is going on**, walking or not:
    //: its coordinates are not the old ones, and a frame left in them shows
    //: an empty field. The walker cannot bring it back either -- on somebody
    //: else's city the legs of the transit are not drawn at all.
    if (cut) {
      cam.cut(middle);
      return;
    }
    //: A loose camera moves for nothing but a new scene -- not for a step, not
    //: for a walk. That is what loose means, and the tether coming back is
    //: itself a reason to re-aim: the frame glides home the moment it is tied.
    if (!tethered) return;
    //: Within one scene, while the walk is being followed, the frame already
    //: has its aim: the dot.
    if (cam.following()) return;
    cam.aimAt(middle);
    //: Every reason the frame may move by itself: you moved, the scene
    //: changed, the tether was tied back on, or the map has just landed and
    //: there is at last a place to aim at. A push from the server is not one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [myRepr, currentLayer, orbiting, focus, sphereShown, drawn, tethered]);

  /**
   * A journey: the frame follows the dot while it lasts (D-238).
   *
   * Told to the camera as one journey, not deduced from the legs: a walk of
   * five nodes remounts the walker's loop five times, and a hand that took
   * the frame on the first leg must keep it to the last.
   *
   * A loose camera follows nothing: the walk goes on without it, and the dot
   * leaves the frame if that is where the road goes.
   */
  const journey = journeyOf(ongoing);
  useEffect(() => {
    //: Letting the tether go stops the frame **where it is**: `follow(false)`
    //: alone leaves a chase already booked to play out, and the map would
    //: coast for another half second after the very click that said stop.
    if (!tethered) return cam.takeFrame();
    cam.follow(journey !== null);
  }, [journey, cam, tethered]);

  /**
   * The walker moves by frames, not renders.
   *
   * Previously a timer recomputed its position every half second -- on a
   * six-second transit that is a dozen jumps instead of movement. Now the dot
   * moves right in `requestAnimationFrame`, bypassing React: React re-renders
   * the map when the map changed, not sixty times a second for one dot.
   *
   * The leg's endpoints are asked for on every frame through `where`: on the
   * space layer the planets under the dot are moving even while it walks.
   */
  const walkerRef = useRef<SVGCircleElement | null>(null);
  //: While walking the camera follows the dot, frame by frame (D-238): the
  //: player watches themselves go, and arrival lands with nothing left to
  //: jump. A grab or a zoom hands the frame back to the hand.
  useEffect(() => {
    if (!ongoing) return;
    let raf = 0;
    const step = () => {
      const circle = walkerRef.current;
      const from = where.current(repr(ongoing.from_key, currentLayer) ?? "");
      const to = where.current(repr(ongoing.to_key, currentLayer) ?? "");
      if (circle && from && to) {
        const t0 = new Date(ongoing.started_at).getTime();
        const t1 = new Date(ongoing.arrives_at).getTime();
        const share = Math.min(1, Math.max(0, (Date.now() - t0) / Math.max(1, t1 - t0)));
        const dot = {
          x: from.x + (to.x - from.x) * share,
          y: from.y + (to.y - from.y) * share,
        };
        circle.setAttribute("cx", String(dot.x));
        circle.setAttribute("cy", String(dot.y));
        //: The dot names where it is; the camera decides whether to chase it
        //: -- it does, unless the hand has taken the frame for this journey.
        cam.toDot(dot);
      }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
    //: The legs of the transit rather than `ongoing` itself: the object
    //: arrives new with every poll, and the effect would be rebuilt twice a
    //: second -- the very stutter this is here to avoid. Every field the
    //: closure reads is listed, so it never goes stale, and the camera is one
    //: object for the life of the map; the linter cannot be shown either.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    ongoing?.from_key,
    ongoing?.to_key,
    ongoing?.started_at,
    ongoing?.arrives_at,
    currentLayer,
    repr,
    cam,
  ]);

  if (!map) {
    return (
      <section className="map-pane">
        <p className="note">{t("ui-map-loading")}</p>
      </section>
    );
  }

  const at = (key: string) => where.current(key);

  const walker = (() => {
    if (!ongoing) return null;
    const from = at(repr(ongoing.from_key, currentLayer) ?? "");
    const to = at(repr(ongoing.to_key, currentLayer) ?? "");
    if (!from || !to) return null;
    const t0 = new Date(ongoing.started_at).getTime();
    const t1 = new Date(ongoing.arrives_at).getTime();
    const share = Math.min(1, Math.max(0, (Date.now() - t0) / Math.max(1, t1 - t0)));
    return { x: from.x + (to.x - from.x) * share, y: from.y + (to.y - from.y) * share };
  })();

  /**
   * Which node wears the player, if any.
   *
   * On the road the body stands in no node at all (D-107), so the node one
   * walked out of must stop wearing them -- but only where the dot on the
   * road says where they are instead. On a layer that draws neither end of
   * the leg there is no dot, and a map that says nothing at all is worse than
   * one that says where the walk began. A scout in the field (D-152) keeps
   * the mark for the same reason: they went **from** the node, they come back
   * to it, and no dot is drawn for them.
   */
  const standingAt = walker ? null : myRepr;

  /**
   * Whether a step leads to the node -- the map's judgement, drawn by `Nodes`.
   *
   * The scout goes nowhere: they are in the field, and not in the node (D-152).
   * And to a planet one does not walk at all: it is reached by ship from a
   * spaceport (D-201) -- a step across the void is not a road the map may draw.
   * A button the server will refuse anyway is a promise the interface may not
   * make.
   */
  const reachable = (node: MapNode) =>
    !ongoing &&
    !look.survey &&
    node.key !== standingAt &&
    !node.orbit &&
    //: Another planet's surface is looked at, not walked to (D-201): its nodes
    //: must not light up as reachable.
    !offworld(byKey, here, node) &&
    (groups.has(node.key) ? Boolean(walkTargets[node.key]) : true);

  const expand = (node: MapNode) => {
    //: Another planet does not open (D-240): its surface is not in the answer
    //: at all, and switching to an empty layer would read as a broken map
    //: rather than as a place one has still to fly to.
    if (offworld(byKey, here, node)) return;
    if (currentLayer === "space") {
      //: Opening a planet means opening **this** planet: without that the
      //: layer below would show somebody else's surface.
      setPlanetFocus(node.planet);
      setLayer("planet");
    } else if (currentLayer === "planet") {
      setCityFocus(node.key);
      setLayer("city");
    }
  };

  const click = (node: MapNode) => {
    if (busy) return;
    setPicked(node.key);
  };

  //: Read off the live frame rather than a state that lags it: a render that
  //: happens for its own reasons -- a push, a pick -- must not put back a
  //: camera the chase has already moved on from.
  const vb = cam.viewBox();

  return (
    <section className="map-pane">
      {/* The face and its inspector stand side by side: the map keeps the whole
          height it can get, and what used to be three strips beneath it is now
          one column that speaks about the node you picked. */}
      <div className="map-face">
      <div className="map-field">
      <Switcher
        layers={layers}
        current={currentLayer}
        onLayer={setLayer}
        tethered={tethered}
        onTether={tether}
        onZoom={zoomBy}
      />

      {visible.length === 0 ? (
        <p className="note">{t("ui-map-layer-empty")}</p>
      ) : (
        <svg
          ref={svgRef}
          viewBox={vb}
          role="img"
          aria-label={t("ui-map-world")}
          className={tethered ? "tethered" : undefined}
          onPointerDown={grabField}
          onPointerMove={movePointer}
          onPointerUp={releasePointer}
          onPointerLeave={releasePointer}
          onPointerCancel={releasePointer}
          onWheel={zoom}
        >
          {orbiting && (
            <SkyBackdrop
              map={map}
              visible={visible}
              at={at}
              repr={repr}
              fit={fit}
              day={sky.day}
            />
          )}

          <Edges edges={shownEdges} at={at} labelled={!orbiting} />
          <Nodes
            nodes={visible}
            at={at}
            standingAt={standingAt}
            picked={picked}
            reachable={reachable}
            group={(key) => groups.has(key)}
            onPick={click}
            onMenu={(node, spot) => {
              setPicked(node.key);
              setMenu({ key: node.key, ...spot });
            }}
          />

          {/* The first frame is drawn from the reckoning; after it the dot is
              led by rAF, outside React. */}
          {walker && (
            <circle
              ref={walkerRef}
              cx={walker.x}
              cy={walker.y}
              r={5}
              className="walker"
            />
          )}
        </svg>
      )}

      {/* The winder belongs to the sky it winds, so it floats on it -- opposite
          the switcher, along the bottom edge, where a scrubber is looked for.
          In flow it stole a line of the map's height on the one layer whose
          whole subject is where the bodies stand at a given hour. */}
      {orbiting && <SkyClock sky={sky} />}
      </div>

      {menu && (
        <NodeMenu
          at={menu}
          node={byKey[menu.key]}
          look={look}
          step={walkTargets[menu.key]}
          group={groups.has(menu.key)}
          offworld={Boolean(byKey[menu.key] && offworld(byKey, here, byKey[menu.key]))}
          onExpand={() => {
            const it = byKey[menu.key];
            if (it) expand(it);
            setMenu(null);
          }}
          onDone={() => setMenu(null)}
        />
      )}

      <Inspector
        look={look}
        picked={picked}
        byKey={byKey}
        groups={groups}
        walkTargets={walkTargets}
        layer={currentLayer}
        onExpand={expand}
        onEnter={onEnter}
      />
      </div>
    </section>
  );
}
