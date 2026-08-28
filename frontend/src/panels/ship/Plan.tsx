// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The hull's floor plan: where the owner puts their compartments (D-240).
 *
 * A ship's interior is on nobody's map but its own (D-201), so the rule that
 * fixes a node's place for ever (D-237) is lifted here and only here: there is
 * no shared north to break and no neighbour to disagree with. What is left is
 * the owner, laying out rooms they alone see -- and a hull one can read at a
 * glance is worth the exception, because everything aboard is found by walking.
 *
 * **The grid is help, not a graph.** Dragging moves the drawing and nothing
 * else: the corridors stay the corridors laid at construction, each of them one
 * second long, and no arrangement can cut a hull in two or strand a room. A
 * cell is exactly the gap two nodes may never be closer than, so a tidy plan is
 * also a legible one, and two rooms can never end up on one point.
 *
 * **The frame moves too.** The field a hull may be laid out on is far wider
 * than what fits on screen, so the plan pans and zooms like any drawing: the
 * hand drags the empty grid and the wheel scales it about the pointer. Without
 * it a compartment put in a far corner became unreachable -- drawn off the
 * frame, with no way to bring it back. The camera is the hand's alone: nothing
 * here re-aims it, so a plan left where it was put stays there.
 *
 * Rooms laid before the rule stand wherever the seating put them, which is not
 * a cell. They are drawn where they are and snap on the first drag; «Выровнять»
 * does the whole hull at once for whoever would rather not drag ten of them.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { InSight, MapNode } from "../../api";
import { lensFor } from "../map/hand";

/** How much of the field the frame shows at rest, in cells. */
const SPAN = 9;
/** How much of a cell a room's square takes: enough gap to read the corridors. */
const FILL = 0.62;
/** How far the wheel may scale the drawing, as a share of the resting frame. */
const ZOOM_IN = 0.35;
const ZOOM_OUT = 2.5;
/** How much one wheel notch scales by. */
const ZOOM_STEP = 1.15;

type Cell = { x: number; y: number };
type Frame = { x: number; y: number; w: number; h: number };

/**
 * Which cell a place falls in, or none.
 *
 * **Exactly** on a cell, never rounded onto one -- the same test the engine
 * makes (`ship.shape._at`). A hull laid before D-240 has its rooms wherever the
 * seating put them, and rounding those into cells here would make the client
 * and the server disagree about where a room stands: the plan would draw it in
 * a cell the server does not believe it is in, and a drag into that very cell
 * would read as "nothing moved".
 */
function cellOf(node: MapNode, cell: number): Cell | null {
  if (!node.place) return null;
  const x = node.place.x / cell;
  const y = node.place.y / cell;
  if (!Number.isInteger(x) || !Number.isInteger(y)) return null;
  return { x, y };
}

export function Plan({
  sight,
  here,
  mine,
  grid,
  busy,
  onArrange,
}: {
  /** The rooms and corridors as `look` gave them: this hull, seen from inside. */
  sight: InSight;
  /** The compartment the body is standing in. */
  here: string;
  /** Whether this hull is ours to rearrange. A guest reads the plan, no more. */
  mine: boolean;
  /** The server's own grid: the cell's width and how far out a room may go. */
  grid: { cell: number; reach: number };
  busy: boolean;
  onArrange: (spots: Record<string, [number, number]>) => void;
}) {
  const CELL = grid.cell;
  const REACH = grid.reach;
  const SIDE = CELL * SPAN;

  const rooms = sight.nodes;
  //: What the drag is doing right now, kept out of the sent state: the plan
  //: shows the room under the hand where the hand is, and the server hears
  //: about it once, on release.
  const [held, setHeld] = useState<{ key: string; cell: Cell } | null>(null);
  //: Where the hand took the frame and where the frame stood then. A ref, not
  //: state: the anchor is read by the next move and never drawn, so writing it
  //: would be a render for nothing. The frame itself **is** state and a pan is
  //: a render apiece -- a plan is a dozen rectangles, and the world map's
  //: camera-outside-React (`map/camera`) would be machinery for no gain here.
  const panning = useRef<{ x: number; y: number; frame: Frame } | null>(null);
  const box = useRef<SVGSVGElement | null>(null);

  const cells = useMemo(() => {
    const out = new Map<string, Cell>();
    rooms.forEach((room, index) => {
      //: A hull laid before D-237 has rooms with no place at all. They are
      //: given one in the drawing -- a row along the top -- so they can be
      //: picked up and put somewhere on purpose.
      out.set(room.key, cellOf(room, CELL) ?? { x: index, y: -REACH });
    });
    return out;
  }, [rooms, CELL, REACH]);

  //: The connector is the hull's own origin: it is the node laid first and the
  //: one the gangway hangs on, so the frame opens on it. Only the **opening**
  //: -- after that the frame is the hand's, and a new compartment does not drag
  //: it about.
  const start = cells.get(rooms[0]?.key ?? "") ?? { x: 0, y: 0 };
  const [frame, setFrame] = useState<Frame>(() => ({
    x: start.x * CELL - SIDE / 2,
    y: start.y * CELL - SIDE / 2,
    w: SIDE,
    h: SIDE,
  }));
  //: A hull that arrives after the first render -- the panel opened before
  //: `look` did -- still gets its frame aimed once. Keyed by the connector, so
  //: walking to another ship re-aims and walking about this one does not.
  const aimed = useRef<string | null>(null);
  const connector = rooms[0]?.key ?? "";
  useEffect(() => {
    if (!connector || aimed.current === connector) return;
    aimed.current = connector;
    const spot = cells.get(connector) ?? { x: 0, y: 0 };
    setFrame({ x: spot.x * CELL - SIDE / 2, y: spot.y * CELL - SIDE / 2, w: SIDE, h: SIDE });
    //: `cells` changes with every arrangement; the aim is not one of the
    //: reasons to move the frame, and the ref above is what says so.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connector, CELL, SIDE]);

  //: React attaches `wheel` passively, and `preventDefault` from a passive
  //: listener does nothing -- the page scrolled along with the zoom. The same
  //: native listener the world map uses (`map/hand`) suppresses it.
  useEffect(() => {
    const field = box.current;
    if (!field) return;
    const block = (event: Event) => event.preventDefault();
    field.addEventListener("wheel", block, { passive: false });
    return () => field.removeEventListener("wheel", block);
  }, []);

  const at = (key: string): Cell | null => {
    if (held?.key === key) return held.cell;
    return cells.get(key) ?? null;
  };

  /** Where a cell is drawn. The field's own coordinates: the origin cell is 0,0. */
  const screenOf = (cell: Cell) => ({ x: cell.x * CELL, y: cell.y * CELL });

  /** Whose cell this is, if anybody's. The plan refuses a drop onto a room. */
  const taken = (cell: Cell, except: string): string | null => {
    for (const room of rooms) {
      if (room.key === except) continue;
      const spot = at(room.key);
      if (spot && spot.x === cell.x && spot.y === cell.y) return room.key;
    }
    return null;
  };

  /**
   * A pointer's place in the field's own units, whatever the frame is doing.
   *
   * Through the lens, not through the element's width: an svg keeps its
   * viewBox's proportions, and `.hull-plan` is capped by `max-height`, so on
   * any frame wider than that cap the drawing sits letterboxed with empty room
   * to either side. Measuring across the whole element would then put a
   * compartment down a cell or two from where it was dropped.
   */
  const fieldAt = (event: React.PointerEvent | React.WheelEvent) => {
    const rect = box.current?.getBoundingClientRect();
    if (!rect) return null;
    const lens = lensFor(rect, frame.w, frame.h);
    return {
      x: frame.x + (event.clientX - rect.left - lens.offX) / lens.k,
      y: frame.y + (event.clientY - rect.top - lens.offY) / lens.k,
    };
  };

  /** Which cell a pointer is over. */
  const cellAt = (event: React.PointerEvent): Cell | null => {
    const spot = fieldAt(event);
    if (!spot) return null;
    return { x: Math.round(spot.x / CELL), y: Math.round(spot.y / CELL) };
  };

  const grab = (key: string) => (event: React.PointerEvent) => {
    if (!mine || busy) return;
    //: A room takes the gesture from the field: a drag that started on a
    //: compartment moves the compartment, never the frame.
    event.stopPropagation();
    event.preventDefault();
    (event.currentTarget as Element).setPointerCapture?.(event.pointerId);
    setHeld({ key, cell: cells.get(key) ?? { x: 0, y: 0 } });
  };

  /** The hand on the empty grid takes the frame. */
  const grabField = (event: React.PointerEvent) => {
    if (held) return;
    event.preventDefault();
    (event.currentTarget as Element).setPointerCapture?.(event.pointerId);
    panning.current = { x: event.clientX, y: event.clientY, frame };
  };

  const move = (event: React.PointerEvent) => {
    const pan = panning.current;
    if (pan) {
      const rect = box.current?.getBoundingClientRect();
      if (!rect) return;
      //: The field moves under the hand by exactly what the hand moved: the
      //: pointer keeps the point it grabbed. Pixels into field units by the
      //: lens, for the same reason `fieldAt` uses it.
      const lens = lensFor(rect, pan.frame.w, pan.frame.h);
      setFrame({
        ...pan.frame,
        x: pan.frame.x - (event.clientX - pan.x) / lens.k,
        y: pan.frame.y - (event.clientY - pan.y) / lens.k,
      });
      return;
    }
    if (!held) return;
    const cell = cellAt(event);
    if (!cell || Math.abs(cell.x) > REACH || Math.abs(cell.y) > REACH) return;
    if (cell.x === held.cell.x && cell.y === held.cell.y) return;
    setHeld({ ...held, cell });
  };

  const release = () => {
    panning.current = null;
    if (!held) return;
    const was = cells.get(held.key);
    const moved = !was || was.x !== held.cell.x || was.y !== held.cell.y;
    //: A cell somebody stands in is not a place to drop into: the engine
    //: refuses it, and a refusal collected after the release says one step too
    //: late what the drawing could have said during it.
    if (moved && !taken(held.cell, held.key)) {
      onArrange({ [held.key]: [held.cell.x, held.cell.y] });
    }
    setHeld(null);
  };

  /** The wheel scales the drawing about the pointer, within honest bounds. */
  const zoom = (event: React.WheelEvent) => {
    const spot = fieldAt(event);
    if (!spot) return;
    const step = event.deltaY > 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
    const width = Math.min(SIDE * ZOOM_OUT, Math.max(SIDE * ZOOM_IN, frame.w * step));
    const scale = width / frame.w;
    if (scale === 1) return;
    setFrame({
      //: The point under the pointer stays under it: that is what makes a zoom
      //: feel like a lens rather than a jump.
      x: spot.x - (spot.x - frame.x) * scale,
      y: spot.y - (spot.y - frame.y) * scale,
      w: width,
      h: frame.h * scale,
    });
  };

  const home = () => {
    const spot = cells.get(connector) ?? { x: 0, y: 0 };
    setFrame({ x: spot.x * CELL - SIDE / 2, y: spot.y * CELL - SIDE / 2, w: SIDE, h: SIDE });
  };

  /**
   * The whole hull onto the grid at once, keeping the shape it has.
   *
   * For a ship built before the rule, where every room sits at whatever angle
   * the seating gave it. Each room takes the free cell nearest to where it
   * already is, in a fixed order, so the result is the plan the owner has been
   * looking at -- straightened, not rearranged.
   */
  const straighten = () => {
    const used = new Set<string>();
    const spots: Record<string, [number, number]> = {};
    for (const room of rooms) {
      const want = cells.get(room.key) ?? { x: 0, y: 0 };
      let put = want;
      for (let ring = 0; ring <= REACH && used.has(`${put.x}:${put.y}`); ring++) {
        for (let dx = -ring; dx <= ring && used.has(`${put.x}:${put.y}`); dx++) {
          for (let dy = -ring; dy <= ring; dy++) {
            const spot = { x: want.x + dx, y: want.y + dy };
            if (Math.abs(spot.x) > REACH || Math.abs(spot.y) > REACH) continue;
            if (used.has(`${spot.x}:${spot.y}`)) continue;
            put = spot;
            break;
          }
        }
      }
      used.add(`${put.x}:${put.y}`);
      spots[room.key] = [put.x, put.y];
    }
    onArrange(spots);
  };

  const askew = rooms.some((room) => cellOf(room, CELL) === null);
  const ROOM = CELL * FILL;
  //: The grid is drawn over the **whole** field a room may stand on, not over
  //: the frame: panning to a corner must not run off the paper. Remembered:
  //: a pan is a render apiece, and the grid depends on neither the frame nor
  //: the rooms.
  const edge = REACH * CELL;
  const lines = useMemo(
    () => Array.from({ length: REACH * 2 + 1 }, (_, i) => (i - REACH) * CELL),
    [REACH, CELL],
  );

  return (
    <>
      <svg
        ref={box}
        className="hull-plan"
        viewBox={`${frame.x} ${frame.y} ${frame.w} ${frame.h}`}
        role="img"
        aria-label="план корабля"
        onPointerDown={grabField}
        onPointerMove={move}
        onPointerUp={release}
        onPointerCancel={release}
        onWheel={zoom}
      >
        {/* The grid, so a straight line looks straight. Nothing but help. */}
        {lines.map((at_) => (
          <g className="hull-grid" key={`g${at_}`}>
            <line x1={at_} y1={-edge} x2={at_} y2={edge} />
            <line x1={-edge} y1={at_} x2={edge} y2={at_} />
          </g>
        ))}

        {/* The corridors: one second apiece and unchanged by any arrangement. */}
        {sight.edges.map((edge_) => {
          const one = at(edge_.a);
          const other = at(edge_.b);
          if (!one || !other) return null;
          const from = screenOf(one);
          const to = screenOf(other);
          return (
            <line
              className="hull-link"
              key={`${edge_.a}|${edge_.b}`}
              x1={from.x}
              y1={from.y}
              x2={to.x}
              y2={to.y}
            />
          );
        })}

        {rooms.map((room) => {
          const cell = at(room.key);
          if (!cell) return null;
          const spot = screenOf(cell);
          const classes = [
            "hull-room",
            room.key === here ? "here" : "",
            held?.key === room.key ? "held" : "",
            mine ? "movable" : "",
          ]
            .filter(Boolean)
            .join(" ");
          return (
            <g key={room.key} className={classes} onPointerDown={grab(room.key)}>
              <rect
                x={spot.x - ROOM / 2}
                y={spot.y - ROOM / 2}
                width={ROOM}
                height={ROOM}
                rx={2}
              />
              <text x={spot.x} y={spot.y + 4} textAnchor="middle">
                {room.name}
              </text>
            </g>
          );
        })}
      </svg>
      <p className="note">
        Перетаскивайте отсеки по сетке: меняется только чертёж. Переходы остаются
        те, что возникли при закладке, и каждый из них — одна секунда. Пустое
        поле тянет чертёж, колесо приближает.
        {askew && " Часть отсеков стоит не по клеткам: их поставили до сетки."}
      </p>
      <div className="row">
        <button className="quiet" onClick={home}>
          К основанию
        </button>
        {mine && askew && (
          <button className="quiet" onClick={straighten} disabled={busy}>
            Выровнять по сетке
          </button>
        )}
      </div>
    </>
  );
}
