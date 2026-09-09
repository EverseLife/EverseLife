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
 * `map/layout`, the springs that once seated what had no place, is gone
 * (wave 6): a node without a place is not drawn, and the hull in the sky is
 * laid by the clock (`useSky`).
 */

import { useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api";
import { type Look, type MapNode, type WorldMap } from "../api";
import { useActions, useBook, useSession } from "../actions";
import { createCamera, viewBoxOf, type Camera } from "./map/camera";
import { UNFLAG, useKept } from "../kept";
import { t } from "../locale";
import { PHONE } from "../narrow";
import { Inspector } from "./map/Inspector";
import { NodeMenu } from "./map/NodeMenu";
import { Edges, Nodes, Outlines, Stubs } from "./map/Nodes";
import { ScoutLayer, ScoutPanel, ScoutRun } from "./map/ScoutLayer";
import { cityOutlines } from "./map/territory";
import { useHand } from "./map/hand";
import { flatten, oneEach, withCityScene } from "./map/geo";
import { placeAt, projectAll, UNITS_PER_METRE } from "./map/globe";
import { firstOnGlobe, needsTurn } from "./map/follow";
import { Ground } from "./map/Ground";
import { GroundGL, type GroundGLHandle, type GroundGLState } from "./map/GroundGL";
import { Lines } from "./map/Lines";
import { supportsShadedGround } from "./map/shade";
import { useArcs, useGlobe, radiusOf } from "./map/useGlobe";
import { factsOf, useBands, useHandOver, type Sphere } from "./map/useBands";
import { SkyBackdrop, SkyClock } from "./map/Sky";
import { Switcher, Zoom, notchOf, scaleOf } from "./map/Switcher";
import { useScene } from "./map/useScene";
import { useScout } from "./map/useScout";
import { useWalker } from "./map/useWalker";
import { useSky } from "./map/useSky";
import {
  STREET_SCALE,
  boundsOf,
  groundReach,
  nodeRadius,
  tilted,
} from "./map/bands";
import {
  delegate,
  drawnAt,
  journeyOf,
  offworld,
  sceneKey,
  type LayerId,
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

export function GraphMap({
  look,
  onEnter,
  initialLayer,
}: Omit<Props, "busy" | "act">) {
  //: The map itself performs nothing: it draws, pans and picks. Every action --
  //: setting off, laying a road -- belongs to the
  //: inspector beside it, which keeps its own waiting and its own refusal.
  const { busy, act, trouble } = useActions();
  //: The map is answered from where the body stands (D-240), so the read
  //: carries the session's token: without it the server shows the sky alone.
  const session = useSession();

  const [world, setWorld] = useState<WorldMap | null>(null);
  const here = look.node?.key ?? "";
  //: The map opens by walking (D-319): what one sees changes with one's own
  //: node and the set of exits from it, so those are the reasons to reread.
  const exits = (look.exits ?? []).map((path) => path.key).join("|");
  useEffect(() => {
    //: Shared with the ship's console, which wants the same map from the same
    //: stand: one walk of the graph, not one per window (`standingMap`).
    void api.standingMap(session.token, `${here}|${exits}`).then(setWorld);
    //: The token is read inside and is the session's own for its whole life:
    //: it is not a reason to reread the map, and the reasons are listed here.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [here, exits]);
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
    if (!world) return world;
    //: The sighted rows last, so a hull the map has as a point of the sky is
    //: the pier's point here (`oneEach`).
    const nodes = oneEach([...world.nodes, ...(seen?.nodes ?? [])]);
    return {
      ...world,
      nodes: withCityScene(nodes),
      edges: [...world.edges, ...(seen?.edges ?? [])],
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
    () =>
      (key: string, layer: LayerId): string | null =>
        delegate(byKey, key, layer),
    [byKey],
  );

  const book = useBook();
  //: The node the inspector talks about. Where you stand, until you pick another.
  const [picked, setPicked] = useState<string | null>(null);
  //: A right-click menu on a node. A left click picks -- which is what makes a
  //: click predictable -- and this is the shortcut for whoever already knows
  //: where they are going and does not want the column in between.
  const [menu, setMenu] = useState<{
    key: string;
    x: number;
    y: number;
  } | null>(null);
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
  //: Whose surface the planet layer shows. There are four planets in the sky
  //: now, and "everything of layer `planet`" would mix their nodes into one
  //: heap the first time a second planet gets a node of its own.
  const [planetFocus, setPlanetFocus] = useState<string | null>(null);
  useEffect(() => {
    setPlanetFocus(null);
    setPicked(null);
    setMenu(null);
  }, [here]);

  const locationBase =
    byKey[here]?.layer === "location" ? (byKey[here]?.parent ?? here) : here;
  const hasSubnodes = useMemo(
    () =>
      (map?.nodes ?? []).some(
        (node) => node.layer === "location" && node.parent === locationBase,
      ),
    [map, locationBase],
  );

  const mySphere =
    byKey[repr(here, "space") ?? ""]?.planet ?? byKey[here]?.planet ?? null;
  const sphereShown = planetFocus ?? mySphere;
  //: The band of scale the map is in (D-319, wave 4) and what the last frame
  //: decided -- `map/useBands`. Not remembered past the panel -- see `CAMERA`.
  const sphereRadius = useMemo(
    () => radiusOf(book, sphereShown),
    [book, sphereShown],
  );
  const { band, bandRef, enter, surface, surfaceRef, zoomed, tell } = useBands({
    book,
    initialLayer: initialLayer ?? "planet",
    hasSubnodes,
    radius: sphereRadius,
  });
  const epoch = look.clock?.epoch ?? null;
  //: The scene the band draws -- which nodes, which edges, who stands for
  //: whom -- lives in `map/useScene`; the map keeps the camera and the hand.
  const scene = useScene({
    map,
    byKey,
    band,
    citiesOpen: zoomed.cities,
    locationBase,
    sphereShown,
    here,
  });
  const {
    orbiting,
    inside,
    citiesOpen,
    reprScene,
    myRepr,
    visible,
    shownEdges,
  } = scene;

  // --- where everything stands ----------------------------------------------

  const svgRef = useRef<SVGSVGElement | null>(null);
  const zoomRef = useRef<HTMLInputElement | null>(null);
  //: The ground the GPU draws under the svg (landscape plan wave 5): asked
  //: to redraw with every frame the camera paints, off React like the
  //: viewBox. Without WebGL2 the svg ground stands whole, as before.
  const shadedRef = useRef<GroundGLHandle | null>(null);
  const shadeable = useMemo(supportsShadedGround, []);
  //: How the GPU ground is doing: the SVG ground draws the land until the
  //: textures are up, and for good once the GPU has given up -- a player
  //: must not see an empty disk where the globe used to be.
  const [shading, setShading] = useState<GroundGLState>("loading");
  const shaded = shadeable && shading !== "failed";
  //: The warmth as a layer (plan §9.5): the colour is the biome's, and the
  //: three tones of the climate are laid over it only when asked.
  const [warmth, setWarmth] = useState(false);

  /**
   * The camera (`map/camera`): outside React, painted straight onto the
   * `viewBox`. The render reads the same object, so a render that happens for
   * its own reasons never puts back a frame the animation has moved on from.
   */
  const camera = useRef<Camera | null>(null);
  //: Where the planets are, read at frame time: the sky lays them out below.
  const spheres = useRef<() => Sphere[]>(() => []);
  //: A phone's field is a third of a desktop's width, and the same frame
  //: over it drew a node's name at five pixels. The frame starts twice as
  //: close there: the body's neighbourhood, legible, and the rest a pan away.
  //: Asked once, at creation, not subscribed to: the map is remounted when
  //: a phone changes section, and a desktop that narrows keeps the frame it
  //: had -- a hook here would redraw forty nodes for a value read once.
  if (!camera.current) {
    camera.current = createCamera({
      onFrame: (f) => {
        svgRef.current?.setAttribute("viewBox", viewBoxOf(f));
        shadedRef.current?.draw();
        //: The slider rides with the frame, off React like the viewBox.
        if (zoomRef.current) {
          zoomRef.current.value = String(
            notchOf(f.scale, boundsOf(bandRef.current, surfaceRef.current)),
          );
        }
        //: What the frame decides is React's business only when it flips:
        //: cities opening, a band's edge reached, a planet under the middle.
        tell(factsOf(f, surfaceRef.current, spheres.current()));
      },
      scale: window.matchMedia(PHONE).matches ? PHONE_SCALE : 1,
      still: () =>
        window.matchMedia("(prefers-reduced-motion: reduce)").matches,
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
  /**
   * The globe (D-319, wave 3): a surface scene is the planet seen from above
   * one point of it -- the eye -- and the hand turns it (`map/useGlobe`).
   */
  const globe = useGlobe({
    book,
    planet: sphereShown,
    active: !orbiting && !inside,
    descent: zoomed.descent,
  });
  const { globeScene, radius } = globe;
  //: The eye as the descent shows it (D-319, wave 5): from over the pole at
  //: the floor, tilting to where it stands as the frame comes down.
  const eye = useMemo(
    () => (globe.eye ? tilted(globe.eye, zoomed.descent) : null),
    [globe.eye, zoomed.descent],
  );
  const ground = useMemo(() => {
    //: The sky is nobody's ground: there every point comes from the clock.
    if (orbiting) return new Map<string, Point>();
    //: A closed city goes in standing on its bioprinter (`model.drawnAt`):
    //: the swap is made before the projection, so the circle, the roads, the
    //: name and the camera's aim all read one point and cannot part.
    const laid = drawnAt(byKey, visible);
    //: Every place is the server's (D-237): what has none, or faces away
    //: from the eye, is not drawn -- nothing is made up for it (wave 6).
    return globeScene && eye && radius
      ? projectAll(eye, radius, laid)
      : flatten(laid);
  }, [visible, byKey, orbiting, globeScene, eye, radius]);
  const { curve, stubCurve } = useArcs({
    globeScene,
    eye,
    radius,
    byKey,
    reprScene,
  });

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
  spheres.current = () =>
    (map?.nodes ?? [])
      .filter((node) => node.orbit)
      .map((node) => ({
        key: node.key,
        planet: node.planet,
        at: sky.places.current.get(node.key),
      }))
      .filter((s): s is Sphere => Boolean(s.at));

  // --- mouse: pan, zoom, pick -----------------------------------------------

  //: What a hand may do to the frame lives in `map/hand`: the rule differs by
  //: whether the camera is tied to the body, and it is one rule in one place
  //: rather than a check repeated at every handler.
  //: The scout's aim (D-321): where one may look and where the finger has
  //: pointed. All of it is `map/useScout` -- the ring, the water under the
  //: cursor and the line to it are one question, and the map has enough of
  //: its own.
  const scout = useScout({
    book,
    byKey,
    here,
    ground,
    edges: shownEdges,
    planet: sphereShown,
    eye,
    radius,
    sphere: Boolean(globeScene && !orbiting && !inside),
    frame: cam.frame,
  });
  const { grabField, movePointer, releasePointer, zoom, zoomToScale } = useHand(
    {
      cam,
      svg: svgRef,
      tethered,
      ready: Boolean(map) && visible.length > 0,
      rotate: globe.rotate,
      onTap: scout.tap,
      bounds: () => boundsOf(bandRef.current, surfaceRef.current),
    },
  );

  // --- node behaviour -------------------------------------------------------

  const walkTargets = useMemo(() => {
    const out: Record<string, { key: string; seconds: number }> = {};
    for (const exit of look.exits ?? []) {
      const p = reprScene(exit.key);
      if (!p || p === reprScene(here)) continue;
      const known = out[p];
      if (!known || exit.seconds < known.seconds) {
        out[p] = { key: exit.key, seconds: exit.seconds };
      }
    }
    return out;
  }, [look.exits, reprScene, here]);

  const groups = useMemo(() => {
    const out = new Set<string>();
    for (const node of map?.nodes ?? []) if (node.parent) out.add(node.parent);
    return out;
  }, [map]);
  /** The outlines of the cities, in degrees: once per map, projected as
   *  the globe turns. */
  //: The shown planet's cities and no others. The public map carries every
  //: planet's surface, and an outline is projected by the eye and radius of
  //: the one under it -- so Aurora's towns, drawn by Terra's globe, landed
  //: as blots on Terra. The nodes are already filtered this way (`visible`);
  //: their outlines were not.
  const outlines = useMemo(
    () =>
      radius && sphereShown
        ? cityOutlines(
            (map?.nodes ?? []).filter((node) => node.planet === sphereShown),
            radius / UNITS_PER_METRE,
          )
        : new Map(),
    [map, radius, sphereShown],
  );
  /** How many nodes hang under each: a closed city is drawn as large as it
   *  is, so a town and the capital are told apart from afar. */
  const sizes = useMemo(() => {
    const out = new Map<string, number>();
    for (const node of map?.nodes ?? []) {
      if (node.parent) out.set(node.parent, (out.get(node.parent) ?? 0) + 1);
    }
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
  const visibleRef = useRef(visible);
  visibleRef.current = visible;
  //: Every node the map knows, not only the drawn ones: a body inside a
  //: closed city is not itself in the scene, and the tether has to find a
  //: place to turn to anyway.
  const byKeyRef = useRef(byKey);
  byKeyRef.current = byKey;
  const hereRef = useRef(here);
  hereRef.current = here;
  const drawn = Boolean(map);
  useEffect(() => {
    const laid = groundRef.current;
    const middle = orbiting
      ? (skyPlaces.current.get(myRepr ?? "") ?? STAR)
      : (laid.get(myRepr ?? "") ?? [...laid.values()][0]);
    if (!middle) return;
    const scene = sceneKey(band, inside ? locationBase : null, sphereShown);
    const cut = shownScene.current !== scene;
    shownScene.current = scene;
    //: On a globe the eye goes to where the body stands whenever the scene is
    //: new: the origin of the frame is the eye, so the middle is the origin.
    if (cut && globeScene) {
      //: Where the body stands, if this scene shows it; somebody else's city
      //: opened from outside has no node of yours, and then the scene's first
      //: place -- a window with no centre would be an empty field.
      const shown = visibleRef.current;
      const stand = firstOnGlobe([
        shown.find((node) => node.key === myRepr)?.place,
        byKeyRef.current[myRepr ?? ""]?.place,
        byKeyRef.current[hereRef.current]?.place,
        shown.find((node) => node.place && "lat" in node.place)?.place,
      ]);
      if (stand) {
        globe.lookAt(stand);
        cam.cut({ x: 0, y: 0 });
        return;
      }
    }
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
    //: On the globe the body comes to the middle by the globe turning under
    //: the eye, and the frame stays on the eye: a frame slid to the body's
    //: projection would leave the planet off centre, and the hand -- which
    //: turns, and does not slide -- could never bring it back.
    if (globeScene) {
      //: Where to turn to, in the order the question is really asked: the
      //: node the scene shows for the body, then that node wherever the map
      //: has it, then the body's own node. The first alone left the globe
      //: still whenever the body's representative was not among the drawn --
      //: a closed city too small to draw, a scene that shows the city and not
      //: the flat -- and the tether then slid a frame whose origin is the
      //: eye, which is no movement at all (owner, 2026-09-08: «планета не
      //: всегда прокручивается до игрока»).
      const stand = firstOnGlobe([
        visibleRef.current.find((node) => node.key === myRepr)?.place,
        byKeyRef.current[myRepr ?? ""]?.place,
        byKeyRef.current[hereRef.current]?.place,
      ]);
      if (stand) {
        globe.aimAt(stand);
        cam.aimAt({ x: 0, y: 0 });
        return;
      }
    }
    cam.aimAt(middle);
    //: Every reason the frame may move by itself: you moved, the scene
    //: changed, the tether was tied back on, or the map has just landed and
    //: there is at last a place to aim at. A push from the server is not one.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [myRepr, band, orbiting, locationBase, sphereShown, drawn, tethered]);

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

  //: Followed on the globe, the dot is kept in the middle by turning the
  //: eye -- only once it has strayed half a pixel, so a walk seen from afar
  //: does not redraw the whole ground at every frame for nothing.
  //:
  //: **One correction per eye.** The walker's loop runs every frame and reads
  //: the dot through `where`, which projects with the eye of the last
  //: **render**; a turn asked for is a `setEye`, and React lands it a frame or
  //: two later. Until it does, every frame sees the same stray and asks for
  //: the same correction again -- and `rotate` sums what it is given, so the
  //: eye overshot by as many frames as the render took and came back the next
  //: time round. That beat was the shake the owner saw while walking
  //: (2026-09-08). Remembering which eye was corrected for makes the second
  //: ask a no-op without slowing the first.
  const rotate = globe.rotate;
  const eyeShown = useRef(globe.eye);
  eyeShown.current = globe.eye;
  const corrected = useRef<typeof globe.eye>(null);
  const turn = useMemo(
    () =>
      globeScene && rotate
        ? (dot: Point) => {
            if (!cam.following()) return;
            const eye = eyeShown.current;
            if (
              !needsTurn({
                dot,
                scale: cam.frame().scale,
                eye,
                corrected: corrected.current,
              })
            ) {
              return;
            }
            corrected.current = eye;
            rotate(-dot.x, -dot.y);
          }
        : undefined,
    [globeScene, rotate, cam],
  );
  //: A run of the scout under way (D-327): the body is out on the way, so
  //: the node it left stops wearing the mark and the run draws its own.
  const run = look.scouting ?? null;
  const { walkerRef, walker, standingAt } = useWalker({
    ongoing,
    where,
    reprScene,
    cam,
    turn,
    myRepr,
    scouting: run !== null,
  });

  const { descend, enterBand } = useHandOver({
    band,
    zoomed,
    enter,
    cam,
    book,
    mySphere,
    setPlanetFocus,
    surface,
  });
  if (!map) {
    return (
      <section className="map-pane">
        <p className="note">{t("ui-map-loading")}</p>
      </section>
    );
  }

  const at = (key: string) => where.current(key);

  /**
   * Whether a step leads to the node -- the map's judgement, drawn by `Nodes`.
   *
   * To a planet one does not walk at all: it is reached by ship from a
   * spaceport (D-201) -- a step across the void is not a road the map may draw.
   * A button the server will refuse anyway is a promise the interface may not
   * make.
   */
  const reachable = (node: MapNode) =>
    !ongoing &&
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
    if (band === "sky") {
      //: Opening a planet means opening **this** planet: without that the
      //: surface below would be somebody else's. The click flies down.
      descend(node.planet);
      return;
    }
    //: Before the city: one's own hull is a group too -- its rooms hang
    //: under it -- and the city's branch would answer a click on the door
    //: with a zoom, which is not a way in.
    if (band === "surface" && hasSubnodes && node.key === locationBase) {
      enterBand("inside");
      return;
    }
    if (
      band === "surface" &&
      groups.has(node.key) &&
      node.place &&
      "lat" in node.place
    ) {
      //: A city opens by coming near: the eye goes over it and the frame
      //: zooms to the scale its streets are drawn at.
      globe.lookAt(node.place);
      cam.cut({ x: 0, y: 0 });
      cam.zoomOnMiddle(Math.max(cam.frame().scale, STREET_SCALE));
      return;
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
          {/* The bar and the slider stand after the svg in the DOM: positioned
              siblings paint in DOM order, and the svg is positioned now so
              that it paints over the GPU's canvas before it. */}
          {visible.length === 0 ? (
            <p className="note">{t("ui-map-empty")}</p>
          ) : (
            <>
            {shaded && globeScene && eye && radius && sphereShown && (
              <GroundGL
                ref={shadedRef}
                planet={sphereShown}
                eye={eye}
                radius={radius}
                svg={svgRef}
                onState={setShading}
              />
            )}
            <svg
              ref={svgRef}
              viewBox={vb}
              role="img"
              aria-label={t("ui-map-world")}
              className={tethered ? "tethered" : undefined}
              onPointerDown={grabField}
              onPointerMove={(e) => {
                movePointer(e);
                scout.followCursor(e);
              }}
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

              {globeScene && eye && radius && sphereShown && (
                <Ground
                  planet={sphereShown}
                  eye={eye}
                  radius={radius}
                  book={book}
                  clock={look.clock}
                  mode={
                    shaded && shading === "ready" ? (warmth ? "warmth" : "under") : "svg"
                  }
                  detailed={zoomed.ground}
                  coarse={zoomed.descent > 0}
                  unit={zoomed.descent > 0 ? undefined : zoomed.unit}
                  within={
                    zoomed.descent > 0
                      ? undefined
                      : groundReach(zoomed.unit, radius)
                  }
                />
              )}
              {globeScene && eye && radius && sphereShown && zoomed.descent === 0 && shaded && shading === "ready" && (
                <Lines
                  planet={sphereShown}
                  eye={eye}
                  radius={radius}
                  within={groundReach(zoomed.unit, radius)}
                />
              )}
              {globeScene && eye && radius && (
                <Outlines
                  outlines={outlines}
                  eye={eye}
                  radius={radius}
                  open={citiesOpen}
                />
              )}
              {!run && (
                <ScoutLayer
                  scout={scout}
                  here={here}
                  globe={globeScene}
                  eye={eye}
                  radius={radius}
                />
              )}
              {run && globeScene && (
                <ScoutRun
                  run={run}
                  from={at(reprScene(run.from_key) ?? "")}
                  eye={eye}
                  radius={radius}
                />
              )}
              <Edges
                edges={shownEdges}
                at={at}
                labelled={!orbiting}
                curve={curve}
              />
              {stubCurve && <Stubs stubs={map.stubs} curve={stubCurve} />}
              <Nodes
                nodes={visible}
                at={at}
                standingAt={standingAt}
                picked={picked}
                reachable={reachable}
                group={(key) => groups.has(key)}
                //: The city the player is in: its point is drawn however
                //: small it is. Asked through the delegate, because one does
                //: not stand on a city but in its market or its forge.
                home={here ? reprScene(here) : null}
                here={here || null}
                closed={!citiesOpen}
                radius={nodeRadius(book)}
                globe={globeScene ? radius : null}
                size={(key) => sizes.get(key) ?? 0}
                far={citiesOpen ? 0 : zoomed.far}
                onPick={click}
                onMenu={(node, spot) => {
                  setPicked(node.key);
                  setMenu({ key: node.key, ...spot });
                }}
              />

              {/* The first frame is drawn from the reckoning; after it the dot is
              led by rAF, outside React. */}
              {/* Stood by a matrix like every node (`placeAt`): a road on the far
              side of the globe runs past what a `cx` can hold. */}
              {walker && (
                <g ref={walkerRef} transform={placeAt(walker)}>
                  <circle cx={0} cy={0} r={5} className="walker" />
                </g>
              )}
            </svg>
            </>
          )}
          <Switcher
            inside={hasSubnodes ? inside : null}
            onInside={(on) => enterBand(on ? "inside" : "surface")}
            tethered={tethered}
            onTether={tether}
            //: While a run lasts there is nothing to aim at: a second survey
            //: is refused (`explore-already-out`), and an armed field would
            //: promise what will not happen.
            scouting={scout.onGround && !run ? scout.scouting : null}
            onScout={scout.arm}
            warmth={shaded && globeScene ? warmth : null}
            onWarmth={setWarmth}
          />
          <Zoom
            slider={zoomRef}
            onZoom={(notch) =>
              zoomToScale(
                scaleOf(notch, boundsOf(bandRef.current, surfaceRef.current)),
              )
            }
          />

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
            offworld={Boolean(
              byKey[menu.key] && offworld(byKey, here, byKey[menu.key]),
            )}
            onExpand={() => {
              const it = byKey[menu.key];
              if (it) expand(it);
              setMenu(null);
            }}
            onDone={() => setMenu(null)}
          />
        )}

        {!run && (
          <ScoutPanel scout={scout} busy={busy} trouble={trouble} act={act} />
        )}
        <Inspector
          look={look}
          picked={picked}
          byKey={byKey}
          groups={groups}
          walkTargets={walkTargets}
          onExpand={expand}
          onEnter={onEnter}
        />
      </div>
    </section>
  );
}
