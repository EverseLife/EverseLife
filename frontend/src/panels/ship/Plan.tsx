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
 * Rooms laid before the rule stand wherever the seating put them, which is not
 * a cell. They are drawn where they are and snap on the first drag; «Выровнять»
 * does the whole hull at once for whoever would rather not drag ten of them.
 */

import { useMemo, useRef, useState } from "react";
import type { InSight, MapNode } from "../../api";

/** The drawing's frame, in cells: enough room to lay out a big hull. */
const SPAN = 9;
/** How much of a cell a room's square takes: enough gap to read the corridors. */
const FILL = 0.62;

type Cell = { x: number; y: number };

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
  const ROOM = CELL * FILL;

  /** The plan's own coordinates: cells around the connector, centred in the frame. */
  const screenOf = (cell: Cell, origin: Cell) => ({
    x: SIDE / 2 + (cell.x - origin.x) * CELL,
    y: SIDE / 2 + (cell.y - origin.y) * CELL,
  });
  const rooms = sight.nodes;
  //: What the drag is doing right now, kept out of the sent state: the plan
  //: shows the room under the hand where the hand is, and the server hears
  //: about it once, on release.
  const [held, setHeld] = useState<{ key: string; cell: Cell } | null>(null);
  const frame = useRef<SVGSVGElement | null>(null);

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
  //: one the gangway hangs on, so the plan is drawn around it and does not jump
  //: about when a far compartment is added.
  const origin = cells.get(rooms[0]?.key ?? "") ?? { x: 0, y: 0 };
  const at = (key: string): Cell | null => {
    if (held?.key === key) return held.cell;
    return cells.get(key) ?? null;
  };

  /** Whose cell this is, if anybody's. The plan refuses a drop onto a room. */
  const taken = (cell: Cell, except: string): string | null => {
    for (const room of rooms) {
      if (room.key === except) continue;
      const spot = at(room.key);
      if (spot && spot.x === cell.x && spot.y === cell.y) return room.key;
    }
    return null;
  };

  /** Where a pointer is, in cells. Read off the SVG's own box, so zoom is free. */
  const cellAt = (event: React.PointerEvent): Cell | null => {
    const box = frame.current?.getBoundingClientRect();
    if (!box) return null;
    const x = ((event.clientX - box.left) / box.width) * SIDE;
    const y = ((event.clientY - box.top) / box.height) * SIDE;
    return {
      x: origin.x + Math.round((x - SIDE / 2) / CELL),
      y: origin.y + Math.round((y - SIDE / 2) / CELL),
    };
  };

  const grab = (key: string) => (event: React.PointerEvent) => {
    if (!mine || busy) return;
    event.preventDefault();
    (event.target as Element).setPointerCapture?.(event.pointerId);
    setHeld({ key, cell: cells.get(key) ?? { x: 0, y: 0 } });
  };

  const drag = (event: React.PointerEvent) => {
    if (!held) return;
    const cell = cellAt(event);
    if (!cell || Math.abs(cell.x) > REACH || Math.abs(cell.y) > REACH) return;
    if (cell.x === held.cell.x && cell.y === held.cell.y) return;
    setHeld({ ...held, cell });
  };

  const drop = () => {
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

  return (
    <>
      <svg
        ref={frame}
        className="hull-plan"
        viewBox={`0 0 ${SIDE} ${SIDE}`}
        role="img"
        aria-label="план корабля"
        onPointerMove={drag}
        onPointerUp={drop}
        onPointerCancel={drop}
      >
        {/* The grid, so a straight line looks straight. Nothing but help. */}
        {Array.from({ length: SPAN + 1 }, (_, i) => (
          <g className="hull-grid" key={`g${i}`}>
            <line x1={i * CELL} y1={0} x2={i * CELL} y2={SIDE} />
            <line x1={0} y1={i * CELL} x2={SIDE} y2={i * CELL} />
          </g>
        ))}

        {/* The corridors: one second apiece and unchanged by any arrangement. */}
        {sight.edges.map((edge) => {
          const one = at(edge.a);
          const other = at(edge.b);
          if (!one || !other) return null;
          const from = screenOf(one, origin);
          const to = screenOf(other, origin);
          return (
            <line
              className="hull-link"
              key={`${edge.a}|${edge.b}`}
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
          const spot = screenOf(cell, origin);
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
        те, что возникли при закладке, и каждый из них — одна секунда.
        {askew && " Часть отсеков стоит не по клеткам: их поставили до сетки."}
      </p>
      {mine && askew && (
        <button className="quiet" onClick={straighten} disabled={busy}>
          Выровнять по сетке
        </button>
      )}
    </>
  );
}
