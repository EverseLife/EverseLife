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
import { Hint } from "../Hint";
import { SURFACE, spell, type Look, type MapNode, type WorldMap } from "../api";
import { useActions } from "../actions";
import { SHAPES } from "../glyphs";
import { nodeGlyph } from "../marks";
import { cityWord } from "../planets";
import { Inspector } from "./map/Inspector";
import { NodeMenu } from "./map/NodeMenu";
import { settle } from "./map/layout";
import { SkyBackdrop, SkyClock } from "./map/Sky";
import { useSky } from "./map/useSky";
import {
  DASH,
  H,
  LAYERS,
  W,
  delegate,
  homeCity,
  nearby,
  offworld,
  type LayerId,
  type Link,
  type Point,
} from "./map/model";
import { STAR } from "./map/orbits";

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

  const [world, setWorld] = useState<WorldMap | null>(null);
  const here = look.node?.key ?? "";
  //: The map grows by exploration (D-152), and a found node must appear by
  //: itself. We reread it when what could have changed the map changes: own
  //: node, the set of exits from it and the scout's return. One load on first
  //: show lasted exactly until the first find.
  const exits = (look.exits ?? []).map((path) => path.key).join("|");
  const exploring = look.survey?.returns_at ?? "";
  useEffect(() => {
    void api.worldMap().then(setWorld);
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

  //: The default layer is the one you stand on; explicit expansion lives until the transit.
  const [layer, setLayer] = useState<LayerId | null>(initialLayer ?? null);
  //: The node the inspector talks about. Where you stand, until you pick another.
  const [picked, setPicked] = useState<string | null>(null);
  //: A right-click menu on a node. A left click picks -- which is what makes a
  //: click predictable -- and this is the shortcut for whoever already knows
  //: where they are going and does not want the column in between.
  const [menu, setMenu] = useState<{ key: string; x: number; y: number } | null>(null);
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
  ).map((option) =>
    option.id === "city" ? { ...option, label: cityWord(sphereShown).name } : option,
  );
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

  //: A grab on the field is a pan; a grab on a node is only ever a click. The
  //: nodes themselves are not moved by hand -- the map is the same map for
  //: everybody (D-237), and a rearranged one would be the one exception.
  const dragging = useRef<{
    moved: boolean;
    startX: number;
    startY: number;
    panX0: number;
    panY0: number;
  } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  //: Pan and zoom: the viewBox is the camera.
  const [camera, setCamera] = useState({ x: 0, y: 0, scale: 1 });

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
  });
  const { fit } = sky;

  // --- mouse: pan, zoom, pick -----------------------------------------------

  /** Pixels -> world. The svg is elastic, and the viewBox keeps proportions (`meet`),
   *  so margins appear at the edges -- without accounting for them a click misses the node. */
  const lens = () => {
    const svg = svgRef.current;
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    const worldW = W / camera.scale;
    const worldH = H / camera.scale;
    const k = Math.min(rect.width / worldW, rect.height / worldH);
    return {
      rect,
      k,
      offX: (rect.width - worldW * k) / 2,
      offY: (rect.height - worldH * k) / 2,
    };
  };

  const toWorld = (e: { clientX: number; clientY: number }) => {
    const m = lens();
    if (!m) return { x: 0, y: 0 };
    return {
      x: camera.x + (e.clientX - m.rect.left - m.offX) / m.k,
      y: camera.y + (e.clientY - m.rect.top - m.offY) / m.k,
    };
  };

  //: Pointer capture is a convenience (the pan does not break at the edge),
  //: not a condition: a pointer without capture (touch emulation, tests) must
  //: not break panning.
  const capture = (e: React.PointerEvent) => {
    try {
      (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    } catch {
      /* no pointer with that id: panning works without the capture too */
    }
  };

  const grabField = (e: React.PointerEvent) => {
    capture(e);
    dragging.current = {
      moved: false,
      startX: e.clientX,
      startY: e.clientY,
      panX0: camera.x,
      panY0: camera.y,
    };
  };

  const movePointer = (e: React.PointerEvent) => {
    const drag = dragging.current;
    if (!drag) return;
    const dx = e.clientX - drag.startX;
    const dy = e.clientY - drag.startY;
    if (Math.hypot(dx, dy) > 4) drag.moved = true;
    if (!drag.moved) return;
    const m = lens();
    const k = m ? 1 / m.k : 1;
    setCamera((cam) => ({ ...cam, x: drag.panX0 - dx * k, y: drag.panY0 - dy * k }));
  };

  const releasePointer = () => {
    dragging.current = null;
  };

  /**
   * A press on a node. Only ever a pick -- a node is not dragged (D-237).
   *
   * The press does not reach the field beneath, so picking a node never pans
   * the map by the two pixels a hand moves while clicking.
   */
  const grabNode = (node: MapNode) => (e: React.PointerEvent) => {
    e.stopPropagation();
    click(node);
  };

  //: The wheel over the map is zoom, and only zoom. React attaches wheel
  //: passively, and preventDefault from there does not work -- the page
  //: scrolled along with the zoom. Suppressed by a native listener with passive: false.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const block = (e: WheelEvent) => e.preventDefault();
    svg.addEventListener("wheel", block, { passive: false });
    return () => svg.removeEventListener("wheel", block);
  }, [map, visible.length]);

  const zoom = (e: React.WheelEvent) => {
    const p = toWorld(e);
    setCamera((cam) => {
      const scale = Math.min(4, Math.max(0.4, cam.scale * (e.deltaY < 0 ? 1.15 : 1 / 1.15)));
      //: Zoom to the cursor: the point under the mouse stays under the mouse.
      return {
        scale,
        x: p.x - (p.x - cam.x) * (cam.scale / scale),
        y: p.y - (p.y - cam.y) * (cam.scale / scale),
      };
    });
  };

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
  useEffect(() => {
    const middle = orbiting
      ? (skyPlaces.current.get(myRepr ?? "") ?? STAR)
      : (ground.get(myRepr ?? "") ?? [...ground.values()][0]);
    if (!middle) return;
    setCamera((cam) => ({
      ...cam,
      x: middle.x - W / (2 * cam.scale),
      y: middle.y - H / (2 * cam.scale),
    }));
  }, [myRepr, currentLayer, orbiting, ground, skyPlaces]);

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
        circle.setAttribute("cx", String(from.x + (to.x - from.x) * share));
        circle.setAttribute("cy", String(from.y + (to.y - from.y) * share));
      }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
    //: В зависимостях поля перехода, а не сам `идёт`: объект приходит новым с
    //: каждым опросом сервера, и эффект пересоздавался бы дважды в секунду —
    //: то самое дёрганье, от которого мы уходим. Все читаемые поля перечислены,
    //: поэтому замыкание не устаревает; линтеру этого не доказать.
  }, [
    ongoing?.from_key,
    ongoing?.to_key,
    ongoing?.started_at,
    ongoing?.arrives_at,
    currentLayer,
    repr,
  ]);

  if (!map) {
    return (
      <section className="map-pane">
        <p className="note">карта грузится…</p>
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

  const expand = (node: MapNode) => {
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

  const vb = `${camera.x} ${camera.y} ${W / camera.scale} ${H / camera.scale}`;

  return (
    <section className="map-pane">
      <nav className="row tabs">
        {layers.map((option) => (
          <button
            key={option.id}
            className={currentLayer === option.id ? "" : "quiet"}
            aria-current={currentLayer === option.id || undefined}
            onClick={() => setLayer(option.id)}
          >
            {option.label}
          </button>
        ))}
        <Hint>
          Вы всегда в середине карты, и она едет за вами. Видно два шага графа
          вокруг — куда можно дойти и что видно оттуда; остальное открывается
          ходьбой. Узлы стоят там, где стоят: место узла одно и то же у всех
          игроков и завтра, поэтому мышью их не двигают. Фон — панорама, колесо
          — зум. Слои: космос, планета, город — один и тот же граф с разной
          высоты.
        </Hint>
      </nav>

      {orbiting && <SkyClock sky={sky} />}

      {/* The face and its inspector stand side by side: the map keeps the whole
          height it can get, and what used to be three strips beneath it is now
          one column that speaks about the node you picked. */}
      <div className="map-face">
      {visible.length === 0 ? (
        <p className="note">На этом слое пока ничего нет.</p>
      ) : (
        <svg
          ref={svgRef}
          viewBox={vb}
          role="img"
          aria-label="карта мира"
          onPointerDown={grabField}
          onPointerMove={movePointer}
          onPointerUp={releasePointer}
          onPointerLeave={releasePointer}
          onWheel={zoom}
        >
          {orbiting && (
            <SkyBackdrop map={map} visible={visible} at={at} repr={repr} fit={fit} />
          )}

          {shownEdges.map((edge) => {
            const a = at(edge.a);
            const b = at(edge.b);
            if (!a || !b) return null;
            return (
              <g key={`${edge.a}|${edge.b}`}>
                <line
                  x1={a.x} y1={a.y} x2={b.x} y2={b.y}
                  className={`edge ${edge.surface}`}
                  strokeDasharray={DASH[edge.surface]}
                />
                {/* In space an edge is a gangway and nothing else: the only
                    thing coupled to a planet is a ship standing at its port
                    (D-201). "21 s of paved highway" would be a road's label on
                    something that is not a road, so the tie is drawn bare. */}
                {!orbiting && (
                  <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 6} className="edge-label">
                    {spell(edge.seconds)} · {SURFACE[edge.surface as keyof typeof SURFACE]}
                  </text>
                )}
              </g>
            );
          })}

          {visible.map((node) => {
            const p = at(node.key);
            if (!p) return null;
            const mine = node.key === myRepr;
            //: The scout goes nowhere: they are in the field, and not in the
            //: node (D-152). A button the server will refuse anyway is a
            //: promise the interface may not make.
            //: And to a planet one does not walk at all: it is reached by ship
            //: from a spaceport (D-201). A step across the void is not a road
            //: the map may draw.
            const near =
              !ongoing &&
              !look.survey &&
              !mine &&
              !node.orbit &&
              //: Another planet's surface is looked at, not walked to (D-201):
              //: its nodes must not light up as reachable.
              !offworld(byKey, here, node) &&
              (groups.has(node.key) ? Boolean(walkTargets[node.key]) : true);
            const group = groups.has(node.key);
            const chosen = node.key === picked;
            //: A planet is not a city node and must not look like one: a body
            //: with its own colour, on its own ring, instead of a circle on a
            //: spring. The colour is the planet's identity and the same from
            //: everywhere -- Terra is that blue seen from Terra and from Pyroxis.
            const sphere = Boolean(node.orbit);
            //: A ship is neither a planet nor a place: a hull, drawn by its own
            //: mark, standing beside its port or somewhere along a passage.
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
                  node.deferred ? "later" : ""
                } ${mine ? "me" : ""} ${near || group ? "near" : ""}${
                  chosen ? " picked" : ""
                }`}
                onPointerDown={grabNode(node)}
                onContextMenu={(e) => {
                  e.preventDefault();
                  setPicked(node.key);
                  setMenu({ key: node.key, x: e.clientX, y: e.clientY });
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
                    <circle cx={p.x} cy={p.y} r={mine ? 13 : 11} className="corona" />
                    <circle cx={p.x} cy={p.y} r={mine ? 9 : 7} className="orb" />
                  </>
                ) : (
                  <>
                    <circle cx={p.x} cy={p.y} r={mine ? 14 : group ? 12 : 10} />
                    {group && (
                      <circle cx={p.x} cy={p.y} r={mine ? 18 : 16} className="halo" />
                    )}
                    {/* The node says what it is before the click (D-238): the
                        type's glyph inside the circle -- a fir for a forest,
                        a colonnade for a settlement. Bare circles stay for
                        what has no type to speak of. */}
                    {(() => {
                      const sign = nodeGlyph({
                        emblem: node.emblem,
                        features: node.features,
                        settlement: group,
                        port: node.port,
                      });
                      if (!sign) return null;
                      const size = mine ? 14 : 12;
                      return (
                        <svg
                          x={p.x - size / 2}
                          y={p.y - size / 2}
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
                    })()}
                  </>
                )}
                {chosen && (
                  <circle cx={p.x} cy={p.y} r={mine ? 20 : 18} className="ring" />
                )}
                {/* A ship's name hangs below the hull: above it there is
                    already a planet's name, and two ships at one port would
                    write over it and over each other. */}
                <text x={p.x} y={hull ? p.y + 21 : p.y - 20} className="node-label">
                  {node.name}
                </text>
                {/* Aquatica is drawn precisely because one cannot go there
                    (D-104): the map shows the unreachable and says so. */}
                {node.deferred && (
                  <text x={p.x} y={p.y + 30} className="node-door">
                    вне альфы
                  </text>
                )}
                {/* The city's two doors (D-206): every road beyond the walls
                    starts at the gate, every ship couples to the spaceport.
                    Unmarked, the graph reads as an arbitrary tangle -- and it
                    is not one. */}
                {(node.exit || node.port) && (
                  <text x={p.x} y={p.y + 30} className="node-door">
                    {node.exit ? "ворота" : "космодром"}
                  </text>
                )}
              </g>
            );
          })}

          {/* Первый кадр рисуется по расчёту, дальше кружок ведёт rAF. */}
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
