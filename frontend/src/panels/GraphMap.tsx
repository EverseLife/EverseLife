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
 * ## The map is physical
 *
 * Nodes are bodies in a force simulation, like the graph in Obsidian: edges
 * are springs, nodes repel, and the live layout can be adjusted by hand --
 * grab a node and drag it if it settled badly. The background pans, the wheel
 * zooms. A click stays a click: a short press without movement is "expand" or
 * "walk".
 *
 * While walking, the dot creeps along the edge, and nowhere can be entered.
 * Arrived -- "Enter".
 */

import { useEffect, useMemo, useRef, useState } from "react";
import * as api from "../api";
import { Deadline } from "../Deadline";
import { Hint } from "../Hint";
import {
  SURFACE,
  spell,
  type Look,
  type MapNode,
  type Outlook,
  type RoadWork,
  type Session,
  type WorldMap,
} from "../api";
import { Refusal, useActions } from "../actions";
import { Rule } from "../Rule";

type Props = {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  onEnter: () => void;
};

const W = 880;
const H = 540;

const LAYERS = [
  { id: "space", label: "космос" },
  { id: "planet", label: "планета" },
  { id: "city", label: "город" },
  { id: "location", label: "локация" },
] as const;
type LayerId = (typeof LAYERS)[number]["id"];

/** A node's body in the simulation: position, velocity and "pinned by hand". */
type Mass = { x: number; y: number; vx: number; vy: number; pinned: boolean };

//: Physics knobs. Interface numbers, not world numbers: they do not belong to
//: balance. The spring is soft, damping strong, speed bounded: the layout
//: spreads smoothly rather than in jumps.
const SPRING = 0.014;
const REPULSE = 7500;
const REPULSE_RADIUS = 250;
const DAMPING = 0.8;
const SLEEP_SPEED = 0.02;
const MAX_SPEED = 4;
//: Nodes are pulled together by edges and nothing else: what is not connected
//: is not attracted. A common pull to the centre used to gather unrelated
//: nodes into one clot, and the map lied about the shape of the world.
//: Instead of that pull -- soft walls: they keep a lone component in frame
//: without dragging it towards anybody.
const WALL_PUSH = 0.02;
const WALL_MARGIN = 60;

/**
 * Layout memory: a node that once settled remembers its place by key --
 * across layer changes, leaving the map tab and the road. Reseeding from
 * scratch looks like "the map got shaken", and it now happens only with truly
 * new nodes. Lives at module level: the component's life is shorter than the client's.
 */
const REMEMBERED = new Map<string, { x: number; y: number }>();

/** A seeded starting point: the same map wakes up looking similar. */
function seedPoint(key: string): { x: number; y: number } {
  let seed = 0;
  for (const ch of key) seed = (seed * 31 + ch.charCodeAt(0)) % 997;
  return {
    x: W / 2 + Math.cos(seed) * 150,
    y: H / 2 + Math.sin(seed * 7) * 120,
  };
}

const DASH: Record<string, string | undefined> = { trail: "4 6" };

export function GraphMap({ look, session, onEnter }: Omit<Props, "busy" | "act">) {
  //: The map itself performs nothing: it draws, pans and picks. Every action --
  //: setting off, laying a road, going out to explore -- belongs to the
  //: inspector beside it, which keeps its own waiting and its own refusal.
  const { busy } = useActions();

  const [map, setMap] = useState<WorldMap | null>(null);
  const here = look.node?.key ?? "";
  //: The map grows by exploration (D-152), and a found node must appear by
  //: itself. We reread it when what could have changed the map changes: own
  //: node, the set of exits from it and the scout's return. One load on first
  //: show lasted exactly until the first find.
  const exits = (look.exits ?? []).map((path) => path.key).join("|");
  const exploring = look.survey?.returns_at ?? "";
  useEffect(() => {
    void api.worldMap().then(setMap);
  }, [here, exits, exploring]);
  const ongoing = look.travel ?? null;
  const byKey = useMemo(() => {
    const out: Record<string, MapNode> = {};
    for (const node of map?.nodes ?? []) out[node.key] = node;
    return out;
  }, [map]);

  /** The node's delegate on the layer: climb the parents up to a node of this layer. */
  const repr = useMemo(() => {
    return (key: string, layer: LayerId): string | null => {
      let cursor: MapNode | undefined = byKey[key];
      while (cursor) {
        if (cursor.layer === layer) return cursor.key;
        cursor = cursor.parent ? byKey[cursor.parent] : undefined;
      }
      return null;
    };
  }, [byKey]);

  //: The default layer is the one you stand on; explicit expansion lives until the transit.
  const [layer, setLayer] = useState<LayerId | null>(null);
  //: The node the inspector talks about. Where you stand, until you pick another.
  const [picked, setPicked] = useState<string | null>(null);
  //: A right-click menu on a node. A left click picks -- which is what makes a
  //: click predictable -- and this is the shortcut for whoever already knows
  //: where they are going and does not want the column in between.
  const [menu, setMenu] = useState<{ key: string; x: number; y: number } | null>(null);
  const [cityFocus, setCityFocus] = useState<string | null>(null);
  useEffect(() => {
    setCityFocus(null);
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
  const myCity = byKey[repr(here, "city") ?? ""]?.parent ?? null;
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

  const layers = LAYERS.filter(
    (option) =>
      (option.id !== "location" || hasSubnodes) &&
      (option.id !== "city" || cities.size > 0),
  );
  const desired: LayerId =
    layer ?? ((byKey[here]?.layer as LayerId | undefined) ?? "planet");
  const currentLayer: LayerId = layers.some((s) => s.id === desired)
    ? desired
    : "planet";

  const visible = useMemo(() => {
    return (map?.nodes ?? []).filter((node) => {
      if (node.layer !== currentLayer) return false;
      if (currentLayer === "city") return node.parent === focus;
      if (currentLayer === "location") return node.parent === locationBase;
      return true;
    });
  }, [map, currentLayer, focus, locationBase]);

  const shownEdges = useMemo(() => {
    const seen = new Map<string, { a: string; b: string; surface: string; seconds: number }>();
    const keys = new Set(visible.map((node) => node.key));
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
  }, [map, visible, repr, currentLayer]);

  // --- physics --------------------------------------------------------------

  //: Bodies live in a ref: the simulation runs by frames, not React renders.
  const bodies = useRef<Map<string, Mass>>(new Map());
  const [, setFrame] = useState(0);
  //: While we hold a node it is "pinned" and obeys the mouse, not the springs.
  const dragging = useRef<{
    key: string | null;
    moved: boolean;
    startX: number;
    startY: number;
    panX0: number;
    panY0: number;
  } | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);

  //: Pan and zoom: the viewBox is the camera.
  const [camera, setCamera] = useState({ x: 0, y: 0, scale: 1 });

  //: New nodes take remembered places, and without memory -- seeded ones.
  //: Those leaving the layer are remembered first: come back -- they lie as they lay.
  useEffect(() => {
    const alive = new Set(visible.map((n) => n.key));
    for (const [key, body] of [...bodies.current]) {
      if (!alive.has(key)) {
        REMEMBERED.set(key, { x: body.x, y: body.y });
        bodies.current.delete(key);
      }
    }
    for (const node of visible) {
      if (!bodies.current.has(node.key)) {
        const p = REMEMBERED.get(node.key) ?? seedPoint(node.key);
        bodies.current.set(node.key, { ...p, vx: 0, vy: 0, pinned: false });
      }
    }
  }, [visible]);

  //: Leaving the map (another tab, unmount) also saves the layout.
  useEffect(() => {
    const held = bodies.current;
    return () => {
      for (const [key, body] of held) REMEMBERED.set(key, { x: body.x, y: body.y });
    };
  }, []);

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

  //: Anyone may wake the simulation (dragging, arrival), while the loop itself
  //: lives in the effect below -- the ref stitches them without recreating closures.
  const kick = useRef<() => void>(() => {});

  //: The simulation itself: springs along edges, repulsion, a slight pull to
  //: the centre. Falls asleep when everything settled, and wakes from any touch.
  useEffect(() => {
    let raf = 0;
    let alive = true;
    let running = false;
    const step = () => {
      const items = [...bodies.current.entries()];
      //: Springs: an edge pulls toward the rest length derived from travel time.
      for (const edge of shownEdges) {
        const a = bodies.current.get(edge.a);
        const b = bodies.current.get(edge.b);
        if (!a || !b) continue;
        const rest = 140 + Math.min(110, Math.sqrt(edge.seconds) * 2);
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.max(1, Math.hypot(dx, dy));
        const force = (dist - rest) * SPRING;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        if (!a.pinned) {
          a.vx += fx;
          a.vy += fy;
        }
        if (!b.pinned) {
          b.vx -= fx;
          b.vy -= fy;
        }
      }
      //: Repulsion: nodes do not clump into a blob.
      for (let i = 0; i < items.length; i++) {
        for (let j = i + 1; j < items.length; j++) {
          const a = items[i][1];
          const b = items[j][1];
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const dist = Math.max(12, Math.hypot(dx, dy));
          if (dist > REPULSE_RADIUS) continue;
          const push = REPULSE / (dist * dist);
          const fx = (dx / dist) * push;
          const fy = (dy / dist) * push;
          if (!a.pinned) {
            a.vx -= fx;
            a.vy -= fy;
          }
          if (!b.pinned) {
            b.vx += fx;
            b.vy += fy;
          }
        }
      }
      //: Soft walls instead of a pull to the centre: inside the frame a node is
      //: free, and what is not connected by an edge is not dragged anywhere.
      let speed = 0;
      for (const [, body] of items) {
        if (!body.pinned) {
          if (body.x < WALL_MARGIN) body.vx += (WALL_MARGIN - body.x) * WALL_PUSH;
          if (body.x > W - WALL_MARGIN) {
            body.vx -= (body.x - (W - WALL_MARGIN)) * WALL_PUSH;
          }
          if (body.y < WALL_MARGIN) body.vy += (WALL_MARGIN - body.y) * WALL_PUSH;
          if (body.y > H - WALL_MARGIN) {
            body.vy -= (body.y - (H - WALL_MARGIN)) * WALL_PUSH;
          }
          body.vx *= DAMPING;
          body.vy *= DAMPING;
          //: Speed ceiling: a jerk is damped into a step rather than smeared into a jump.
          body.vx = Math.max(-MAX_SPEED, Math.min(MAX_SPEED, body.vx));
          body.vy = Math.max(-MAX_SPEED, Math.min(MAX_SPEED, body.vy));
          body.x += body.vx;
          body.y += body.vy;
        }
        speed = Math.max(speed, Math.abs(body.vx), Math.abs(body.vy));
      }
      setFrame((f) => f + 1);
      //: Settled -- we sleep until the next touch: no frames burned for nothing.
      running = speed > SLEEP_SPEED || Boolean(dragging.current?.key);
      if (running) raf = requestAnimationFrame(step);
    };
    //: Waking is idempotent: while the loop runs, a second is not started.
    kick.current = () => {
      if (!running && alive) {
        running = true;
        raf = requestAnimationFrame(step);
      }
    };
    kick.current();
    return () => {
      alive = false;
      cancelAnimationFrame(raf);
    };
  }, [shownEdges, visible]);

  /** Wake the loop -- without kicking the bodies: random pushes twitched the map.
   *  A frame is forced at once: the dragged node follows the mouse even while
   *  the loop has not woken yet. */
  const wake = () => {
    kick.current();
    setFrame((f) => f + 1);
  };

  // --- mouse: dragging, pan, zoom -------------------------------------------

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

  const grabNode = (key: string) => (e: React.PointerEvent) => {
    e.stopPropagation();
    capture(e);
    const body = bodies.current.get(key);
    if (body) body.pinned = true;
    dragging.current = {
      key,
      moved: false,
      startX: e.clientX,
      startY: e.clientY,
      panX0: 0,
      panY0: 0,
    };
    wake();
  };

  //: Pointer capture is a convenience (drag does not break at the edge), not a
  //: condition: a pointer without capture (touch emulation, tests) must not break dragging.
  const capture = (e: React.PointerEvent) => {
    try {
      (e.currentTarget as Element).setPointerCapture?.(e.pointerId);
    } catch {
      /* указателя с таким id нет — перенос работает и без захвата */
    }
  };

  const grabField = (e: React.PointerEvent) => {
    capture(e);
    dragging.current = {
      key: null,
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

    if (drag.key) {
      const body = bodies.current.get(drag.key);
      if (body) {
        const p = toWorld(e);
        body.x = p.x;
        body.y = p.y;
        body.vx = 0;
        body.vy = 0;
      }
      wake();
    } else if (drag.moved) {
      const m = lens();
      const k = m ? 1 / m.k : 1;
      setCamera((cam) => ({ ...cam, x: drag.panX0 - dx * k, y: drag.panY0 - dy * k }));
    }
  };

  const releasePointer = (node?: MapNode) => () => {
    const drag = dragging.current;
    dragging.current = null;
    if (!drag) return;
    if (drag.key) {
      const body = bodies.current.get(drag.key);
      if (body) body.pinned = false;
      wake();
      //: A short press without movement is a click, not a move.
      if (!drag.moved && node) click(node);
    }
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

  const myRepr = repr(here, currentLayer);

  const groups = useMemo(() => {
    const out = new Set<string>();
    for (const node of map?.nodes ?? []) if (node.parent) out.add(node.parent);
    return out;
  }, [map]);

  /**
   * The walker moves by frames, not renders.
   *
   * Previously a timer recomputed its position every half second -- on a
   * six-second transit that is a dozen jumps instead of movement. Now the dot
   * moves right in `requestAnimationFrame`, bypassing React: React re-renders
   * the map when the map changed, not sixty times a second for one dot.
   *
   * The leg's endpoints are taken from the simulation bodies on every frame:
   * the nodes under the walker may still be spreading by springs, and the dot
   * must stick to the edge.
   */
  const walkerRef = useRef<SVGCircleElement | null>(null);
  useEffect(() => {
    if (!ongoing) return;
    let raf = 0;
    const step = () => {
      const circle = walkerRef.current;
      const from = bodies.current.get(repr(ongoing.from_key, currentLayer) ?? "");
      const to = bodies.current.get(repr(ongoing.to_key, currentLayer) ?? "");
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

  const at = (key: string) => bodies.current.get(key);

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
    if (currentLayer === "space") setLayer("planet");
    else if (currentLayer === "planet") {
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
            onClick={() => setLayer(option.id)}
          >
            {option.label}
          </button>
        ))}
        <Hint>
          Узлы можно таскать мышью, фон — панорама, колесо — зум. Слои: космос,
          планета, город — один и тот же граф с разной высоты.
        </Hint>
      </nav>

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
          onPointerUp={releasePointer()}
          onPointerLeave={releasePointer()}
          onWheel={zoom}
        >
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
                <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 6} className="edge-label">
                  {spell(edge.seconds)} · {SURFACE[edge.surface as keyof typeof SURFACE]}
                </text>
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
            const near =
              !ongoing &&
              !look.survey &&
              !mine &&
              (groups.has(node.key) ? Boolean(walkTargets[node.key]) : true);
            const group = groups.has(node.key);
            const chosen = node.key === picked;
            return (
              <g
                key={node.key}
                className={`node ${mine ? "me" : ""} ${near || group ? "near" : ""}${
                  chosen ? " picked" : ""
                }`}
                onPointerDown={grabNode(node.key)}
                onPointerMove={movePointer}
                onPointerUp={releasePointer(node)}
                onContextMenu={(e) => {
                  e.preventDefault();
                  setPicked(node.key);
                  setMenu({ key: node.key, x: e.clientX, y: e.clientY });
                }}
              >
                <circle cx={p.x} cy={p.y} r={mine ? 14 : group ? 12 : 10} />
                {group && <circle cx={p.x} cy={p.y} r={mine ? 18 : 16} className="halo" />}
                {chosen && (
                  <circle cx={p.x} cy={p.y} r={mine ? 20 : 18} className="ring" />
                )}
                <text x={p.x} y={p.y - 20} className="node-label">
                  {node.name}
                </text>
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
          session={session}
          step={walkTargets[menu.key]}
          group={groups.has(menu.key)}
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
        session={session}
        picked={picked}
        byKey={byKey}
        groups={groups}
        walkTargets={walkTargets}
        onExpand={expand}
        onEnter={onEnter}
        layer={currentLayer}
      />
      </div>
    </section>
  );
}


/**
 * The column beside the map: everything about the node you picked.
 *
 * It replaces three strips that used to live under the map and took 178px of
 * the 605px the scene had -- the map, which is the game's whole navigation
 * surface, was left with barely half the window. Worse, the strips spoke about
 * everything at once: every road from here, every exit, exploration. The column
 * speaks about one node, which is what a person looking at a map wants.
 *
 * Where you stand, the column offers entering and exploring. Anywhere else --
 * the road there, what it costs the body, and what the surface between here and
 * there is worth laying.
 */
function Inspector({
  look,
  session,
  picked,
  byKey,
  groups,
  walkTargets,
  onExpand,
  onEnter,
  layer,
}: {
  look: Look;
  session: Session;
  picked: string | null;
  byKey: Record<string, MapNode>;
  groups: Set<string>;
  walkTargets: Record<string, { key: string; seconds: number }>;
  onExpand: (node: MapNode) => void;
  onEnter: () => void;
  layer: LayerId;
}) {
  const acting = useActions();
  const { busy, act } = acting;
  const here = look.node?.key ?? "";
  const ongoing = look.travel ?? null;

  //: On the road the column reports the road: nothing else can be done from it.
  if (ongoing) {
    return (
      <aside className="inspect">
        <h3>В пути</h3>
        <p className="sign">{ongoing.final ?? ongoing.to}</p>
        <p className="note">
          {ongoing.final ? `сейчас — отрезок до «${ongoing.to}»` : "прямой переход"}
          {(ongoing.legs_left ?? 0) > 1 && ` · впереди ещё ${ongoing.legs_left! - 1} узл.`}
        </p>
        <Deadline until={ongoing.arrives_at} since={ongoing.started_at} label="переход" />
        <div className="row">
          <button
            className="quiet"
            onClick={() => act(() => session.send("travel.cancel"))}
            disabled={busy}
          >
            Повернуть назад
          </button>
        </div>
        <Refusal of={acting} />
        <Rule>
          Пока идёшь, тебя нет нигде: добыча, крафт, погрузка и покупка закрыты,
          а счёт и ордера работают. Повернуть назад можно в любой момент —
          вернёшься туда, откуда вышел, а потраченное не вернётся.
        </Rule>
      </aside>
    );
  }

  const node = picked ? byKey[picked] : null;
  const mine = !node || node.key === here || walkTargets[node.key]?.key === here;

  //: Standing here: the way in, and the way out into the unknown.
  if (!node || mine) {
    return (
      <aside className="inspect">
        <h3>Вы здесь</h3>
        <p className="sign">{look.node?.name}</p>
        {!look.survey && (
          <div className="row">
            <button onClick={onEnter} disabled={busy}>
              Войти
            </button>
          </div>
        )}
        <Search look={look} session={session} busy={busy} act={act} layer={layer} />
        <Refusal of={acting} />
      </aside>
    );
  }

  const step = walkTargets[node.key];
  const exit = (look.exits ?? []).find((path) => path.key === step?.key);
  const group = groups.has(node.key);
  const reachable = !look.survey && (group ? Boolean(step) : true);

  return (
    <aside className="inspect">
      <h3>{node.name}</h3>
      <p className="note">
        {LAYER_NAME[node.layer] ?? node.layer}
        {group ? " · есть что раскрыть" : ""}
      </p>

      {exit ? (
        <table>
          <tbody>
            <tr>
              <td>дорога</td>
              <td className="num">{spell(exit.seconds)}</td>
            </tr>
            <tr>
              <td>стоит тела</td>
              <td className="num">{price(exit.stamina)}</td>
            </tr>
          </tbody>
        </table>
      ) : (
        <p className="note">
          Соседним не является: маршрут построится сам, по проходимым рёбрам.
        </p>
      )}

      <div className="row">
        {reachable && (
          <button
            onClick={() =>
              act(() =>
                session.send("travel.go", { node: step?.key ?? node.key }),
              )
            }
            disabled={busy}
          >
            Идти
          </button>
        )}
        {group && (
          <button className="quiet" onClick={() => onExpand(node)} disabled={busy}>
            Раскрыть
          </button>
        )}
      </div>
      {look.survey && (
        <p className="reason">Разведчик в поле: тело недоступно, как во сне.</p>
      )}

      <Roads look={look} session={session} busy={busy} act={act} only={node.name} />
      <Refusal of={acting} />
      <Rule>
        Идти можно в любой узел: маршрут строится сам по времени с учётом
        покрытия, каждый отрезок — отдельное задание, и приход сам выводит в
        следующий. По прямой не ходят: нет ребра — нет пути.
      </Rule>
    </aside>
  );
}

/** The layer in words: the player reads a place, not an enum. */
const LAYER_NAME: Record<string, string> = {
  space: "в космосе",
  planet: "на планете",
  city: "в городе",
  location: "внутри места",
};

/** The right-click menu on a node: go there, or open it up.
 *
 * Fixed to the pointer rather than to the node, because the node moves: the
 * layout is a live simulation, and a menu pinned to a body would crawl away
 * from under the hand.
 */
function NodeMenu({
  at,
  node,
  look,
  session,
  step,
  group,
  onExpand,
  onDone,
}: {
  at: { x: number; y: number };
  node: MapNode | undefined;
  look: Look;
  session: Session;
  step?: { key: string; seconds: number };
  group: boolean;
  onExpand: () => void;
  onDone: () => void;
}) {
  const acting = useActions();
  const { busy, act } = acting;
  if (!node) return null;

  const here = node.key === (look.node?.key ?? "");
  const may = !look.travel && !look.survey && !here && (group ? Boolean(step) : true);

  return (
    <div
      className="node-menu"
      role="menu"
      style={{ left: at.x, top: at.y }}
      //: The window-wide listener shuts the menu; a click inside it must not.
      onPointerDown={(e) => e.stopPropagation()}
    >
      <p className="menu-ask">{node.name}</p>
      {may && (
        <button
          role="menuitem"
          onClick={() =>
            void act(async () => {
              await session.send("travel.go", { node: step?.key ?? node.key });
              onDone();
            })
          }
          disabled={busy}
        >
          Идти{step ? ` · ${spell(step.seconds)}` : ""}
        </button>
      )}
      {group && (
        <button role="menuitem" className="quiet" onClick={onExpand} disabled={busy}>
          Раскрыть
        </button>
      )}
      {here && <p className="note">Вы здесь.</p>}
      {look.travel && <p className="note">Пока идёшь, никуда не выйти.</p>}
      <Refusal of={acting} />
    </div>
  );
}

/** Roads from this node: what is laid, what sagged and what it costs (D-158).
 *
 * The surface rises by a tier for `road.surface_per_edge` of surface and
 * `road.build_hours` of time: offroad -> road -> paved highway. Without
 * maintenance a road overgrows back, so the condition is always shown -- an
 * overgrown one cuts the convoy off from a node it drove to yesterday.
 */
function Roads({
  look,
  session,
  busy,
  act,
  only,
}: {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  /** Show the road to this neighbour alone: the column speaks about one node. */
  only?: string;
}) {
  const [roads, setRoads] = useState<RoadWork[]>([]);

  useEffect(() => {
    void session
      .send("road.here")
      .then((answer) => setRoads((answer.roads as RoadWork[]) ?? []))
      .catch(() => setRoads([]));
    //: Пересчитывается при переходе и после каждого действия: уложенная
    //: ступень меняет и покрытие, и остаток полотна в руках.
  }, [session, look.node?.key, look.inventory]);

  const shown = only ? roads.filter((path) => path.to === only) : roads;
  if (shown.length === 0) return null;
  const work_ = (edge: string, mend: boolean) =>
    act(() => session.send("road.lay", { edge, mend }));

  return (
    <div className="row roads">
      {shown.map((path) => (
        <span key={path.edge} className="note">
          {path.to}: {SURFACE_LABEL[path.surface]}
          {path.surface !== "trail" && ` ${path.condition.toFixed(0)}%`}
          {path.working ? (
            " · идёт работа"
          ) : (
            <>
              {/* Цена работы стоит на кнопке, а не в подсказке при наведении:
                  выключенная кнопка без объяснения читается как поломка, а с
                  телефона подсказку не увидеть вовсе. */}
              {path.next && path.needs !== null && (
                <button
                  className="quiet"
                  onClick={() => work_(path.edge, false)}
                  disabled={busy || path.at_hand < path.needs}
                  title={`нужно ${path.needs.toFixed(0)} полотна, в руках ${path.at_hand.toFixed(0)}`}
                >
                  {path.surface === "trail" ? "Проложить" : "Мостить"} за{" "}
                  {path.needs.toFixed(0)}
                </button>
              )}
              {path.mend_needs !== null && (
                <button
                  className="quiet"
                  onClick={() => work_(path.edge, true)}
                  disabled={busy || path.at_hand < path.mend_needs}
                  title={`подсыпка: ${path.mend_needs.toFixed(0)} полотна`}
                >
                  Подсыпать за {path.mend_needs.toFixed(0)}
                </button>
              )}
              {path.at_hand < Math.min(path.needs ?? Infinity, path.mend_needs ?? Infinity) && (
                <> · полотна в руках {path.at_hand.toFixed(0)}</>
              )}
            </>
          )}
        </span>
      ))}
      <Hint>
        Покрытие поднимается на ступень за полотно и время: бездорожье → дорога
        → мощёный тракт. Без содержания дорога зарастает обратно, а по
        бездорожью обоз не идёт вовсе.
      </Hint>
    </div>
  );
}

/** Surface in words: the player reads a road, not an enum. */
const SURFACE_LABEL: Record<RoadWork["surface"], string> = {
  trail: "бездорожье",
  road: "дорога",
  paved: "тракт",
};

/** Exploration from the map: the goal depends on the layer the player looks at (D-152).
 *
 * The scout **leaves in person**: while the run goes, the body is in the field
 * and unavailable, as in sleep. Returning early is allowed -- the find then does not happen.
 *
 * The run's price is a property of the place (D-156): in untrodden surroundings
 * it is minutes and an almost certain find, in trodden ones hours and a roll.
 * The forecast is shown before leaving and updates on node change. */
function Search({
  look,
  session,
  busy,
  act,
  layer,
}: {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
  layer: LayerId;
}) {
  const [speciesList, setSpeciesList] = useState<string[]>([]);
  const [species, setSpecies] = useState("");
  const [forecast, setForecast] = useState<Outlook | null>(null);
  //: Отдельный прогноз для леса: он сужает шанс на лесистость мира (D-191).
  const [woods, setWoods] = useState<Outlook | null>(null);
  const run = look.survey ?? null;

  useEffect(() => {
    void session
      //: Прогноз просится под выбранную породу: редкая ищется хуже частой
      //: (D-151), и «шанс 90%» рядом с заказом золота был бы обманом.
      .send("explore.goals", species ? { goal: "vein", resource: species } : {})
      .then((answer) => {
        setSpeciesList((answer.resources as string[]) ?? []);
        setForecast((answer.outlook as Outlook | null) ?? null);
      })
      .catch(() => {
        setSpeciesList([]);
        setForecast(null);
      });
    //: Лес сужает шанс на лесистость мира (D-191), и это должно быть видно
    //: до выхода — как и с редкой породой.
    void session
      .send("explore.goals", { goal: "forest" })
      .then((answer) => setWoods((answer.outlook as Outlook | null) ?? null))
      .catch(() => setWoods(null));
    //: Заход меняет счёт находок узла, поэтому прогноз пересчитывается и по
    //: возвращении разведчика, а не только при переходе.
  }, [session, look.node?.key, run?.returns_at, species]);

  if (!run && layer !== "city" && layer !== "planet") return null;

  const seek = (goal: string, resource?: string) =>
    act(() => session.send("explore.survey", { goal, resource }));

  return (
    <div className="row search">
      {run ? (
        <>
          <span className="note">
            вы в разведке · вернётесь <Deadline until={run.returns_at} label="разведка" />
          </span>
          <button
            onClick={() => act(() => session.send("explore.cancel"))}
            disabled={busy}
          >
            Вернуться сейчас
          </button>
          <Hint>
            Повернуть назад можно в любой момент: находки не будет, потраченные
            силы не вернутся.
          </Hint>
        </>
      ) : layer === "city" ? (
        <>
          <button onClick={() => seek("lot")} disabled={busy}>
            Уйти искать участок
          </button>
          <Hint>
            Найденный участок встанет городской землёй: её выкупают у города
. Разведчик уходит сам, до возвращения недоступен, как во
            сне, и остаётся на находке.
          </Hint>
        </>
      ) : (
        <>
          <button onClick={() => seek("site")} disabled={busy}>
            Уйти искать узел для города
          </button>
          <button onClick={() => seek("vein", species || undefined)} disabled={busy}>
            Уйти искать жилу
          </button>
          <select value={species} onChange={(e) => setSpecies(e.target.value)}>
            <option value="">любую породу</option>
            {speciesList.map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
          {/* Лес ищут так же, как жилу: он свойство места, и рубка читает то
              же свойство (D-177, D-191). */}
          <button
            onClick={() => seek("forest")}
            disabled={busy}
            title={
              woods
                ? `шанс ${woods.chance >= 1 ? Math.round(woods.chance) : woods.chance.toFixed(1)}%: лес ищется дольше прочего`
                : "рубить древесину можно там, где лес"
            }
          >
            Уйти искать лес
          </button>
          <Hint>
            Разведчик уходит сам и до возвращения недоступен, как во сне.
            Кончатся силы — доспит в поле и продолжит. Нашёл — там и остаётся
; чем дальше от города находка, тем длиннее к ней дорога
. Лес попадается и сам собой, но заказанный ищется дольше.
          </Hint>
        </>
      )}
      {!run && forecast && <Forecast forecast={forecast} />}
    </div>
  );
}

/** What a run from here will cost (D-156). */
function Forecast({ forecast }: { forecast: Outlook }) {
  const { min, max } = forecast.minutes;
  const term = min === max ? long(min) : spread(min, max);
  //: The chance may be a fraction of a percent -- rounding to an integer would
  //: show zero where searching is still possible.
  const chance = forecast.chance >= 1
    ? Math.round(forecast.chance)
    : forecast.chance.toFixed(1);
  return (
    <span className="note">
      заход отсюда: {term} · шанс {chance}% · до {price(forecast.stamina)}{" "}
      выносливости
      {forecast.resource && (forecast.aim ?? 1) < 1 &&
        ` · ${forecast.resource.toLowerCase()} редка: шанс уже в ${(1 / (forecast.aim ?? 1)).toFixed(0)} раз`}
      {forecast.explored > 0 &&
        ` · окрестность исхожена: находок отсюда ${forecast.explored}`}
    </span>
  );
}

const MINUTES_PER_HOUR = 60;

function long(minutes: number): string {
  return `${account(minutes)} ${unit(minutes)}`;
}

function spread(from: number, until: number): string {
  return unit(from) === unit(until)
    ? `${account(from)}–${account(until)} ${unit(until)}`
    : `${long(from)} – ${long(until)}`;
}

function unit(minutes: number): string {
  return minutes < MINUTES_PER_HOUR ? "мин" : "ч";
}

function account(minutes: number): string {
  if (minutes < MINUTES_PER_HOUR) return String(Math.round(minutes));
  const hours = minutes / MINUTES_PER_HOUR;
  return hours % 1 === 0 ? String(hours) : hours.toFixed(1);
}

/** The road's price to the body. A step across town costs a fraction of a unit -- and "0.0" would lie here. */
function price(stamina: number): string {
  if (stamina <= 0) return "0";
  return stamina < 0.1 ? "<0.1" : stamina.toFixed(1);
}
