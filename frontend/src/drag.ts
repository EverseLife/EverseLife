// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The carried stack of a drag (D-238, amendment 4).
 *
 * HTML drag-and-drop hides its payload during `dragover` by design, so the
 * stack rides a module variable instead: the drag never leaves this window.
 * The components -- the drop zone and its amount popover -- live in
 * `DragMove.tsx`; this module holds what is not a component, so fast refresh
 * keeps working there.
 */

/** What is being dragged: enough to ask "how much" and to name the move. */
export type DragStack = {
  /** The engine's item id -- what the command names. */
  item: string;
  goods: string;
  /** What the popover calls it: flavor if the row shows one. */
  label: string;
  /** The whole stack: the ceiling of the ask and the "всё" answer. */
  amount: number;
  /** The surface the stack lies on now: a zone refuses its own stacks. */
  zone: string;
  /** For commands keyed by goods rather than item id (the market's): the
   *  stack carries its own tier and catalog key, so no zone has to look the
   *  row up again and maybe miss. */
  tier?: string;
  key?: string;
};

/**
 * Props for an element inside a draggable row that must keep its own gesture
 * -- a number field where a swipe selects text. The nearest draggable
 * becomes this element, and it cancels the drag instead of starting one.
 */
export const noDrag = {
  draggable: true,
  onDragStart: (event: React.DragEvent<HTMLElement>) => {
    event.preventDefault();
    event.stopPropagation();
  },
};

import { counted } from "./amounts";

let carried: DragStack | null = null;

//: The row's own `dragend` never fires if the row unmounted mid-drag (a push
//: reread removed the item because somebody took it). The window hears the
//: end of every drag that ends inside it and empties the hand, so a stale
//: stack cannot haunt the next drop. Non-capture on purpose: the zone's own
//: drop handler must read the stack first.
let guarded = false;
function guard(): void {
  if (guarded) return;
  guarded = true;
  window.addEventListener("dragend", () => {
    carried = null;
  });
  window.addEventListener("drop", () => {
    carried = null;
  });
}

/** The stack in the air, if any. */
export function carriedStack(): DragStack | null {
  return carried;
}

/** Take the stack out of the air: a drop owns it from here. */
export function dropCarried(): DragStack | null {
  const stack = carried;
  carried = null;
  return stack;
}

/**
 * Props for a draggable row. Spread them onto the row element; the row keeps
 * its buttons -- the drag is an extra hand, not a replacement.
 *
 * The `dragging` mark is set imperatively: a mid-drag rerender may drop it,
 * which costs a frame of styling and nothing else.
 */
export function grip(stack: DragStack) {
  return {
    draggable: true,
    onDragStart: (event: React.DragEvent<HTMLElement>) => {
      guard();
      carried = stack;
      event.dataTransfer.effectAllowed = "move";
      try {
        event.dataTransfer.setData("text/plain", stack.label);
      } catch {
        /* some engines refuse setData on synthetic drags; the module carries it */
      }
      event.currentTarget.classList.add("dragging");
    },
    onDragEnd: (event: React.DragEvent<HTMLElement>) => {
      carried = null;
      event.currentTarget.classList.remove("dragging");
    },
  };
}

/* --- zone names and matching, pinned by tests ----------------------------- */

/** The wildcard a hands-zone accepts: any chest, whichever the stack left. */
export const CHEST_ANY = "chest:*";

/** The zone name of one chest. */
export function chestZone(id: string): string {
  return `chest:${id}`;
}

/** The chest id back out of its zone name -- the inverse of `chestZone`. */
export function chestOf(zone: string): string {
  return zone.slice("chest:".length);
}

/**
 * Whether an accept list takes a stack from `from`: an exact zone, or a
 * family -- an entry ending in ":*" takes every zone under its prefix.
 */
export function fits(accepts: readonly string[], from: string): boolean {
  return accepts.some(
    (a) => a === from || (a.endsWith(":*") && from.startsWith(a.slice(0, -1))),
  );
}

/* --- the arithmetic of the drop's answer, pinned by tests ----------------- */

/** A single counted piece has no "how much": the drop skips the question. */
export function askless(stack: Pick<DragStack, "goods" | "amount">): boolean {
  return counted(stack.goods) && stack.amount <= 1;
}

/** Half the stack the way the popover offers it: whole pieces floor to whole. */
export function halved(stack: Pick<DragStack, "goods" | "amount">): number {
  return counted(stack.goods) ? Math.floor(stack.amount / 2) : stack.amount / 2;
}

/**
 * What a typed answer moves: clamped to the stack, floored to whole pieces
 * for the counted. `null` -- the answer is not a move (empty, zero, junk).
 */
export function answered(
  stack: Pick<DragStack, "goods" | "amount">,
  asked: number,
): number | null {
  if (!Number.isFinite(asked) || asked <= 0) return null;
  const held = Math.min(asked, stack.amount);
  const amount = counted(stack.goods) ? Math.floor(held) : held;
  return amount > 0 ? amount : null;
}
