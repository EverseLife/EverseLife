// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The manners of a popover: the one convention every floating layer of the
 * client keeps (the quick transfer, the circle chip, the header's overflow).
 *
 * Opened, it owns the focus: the first control inside takes it. A press
 * anywhere outside closes it, and so does Escape -- which hands the focus back
 * to the button that opened it, so a keyboard is never left pointing at a
 * layer that is gone. Three copies of this effect used to live in three
 * components, and a fourth was about to be written.
 */

import { useEffect, type RefObject } from "react";

type Popover = {
  open: boolean;
  close: () => void;
  /** The element that holds both the toggle and the layer: presses inside it
   *  are the popover's own business. */
  anchor: RefObject<HTMLElement | null>;
  /** The button that opened the layer; Escape returns the focus to it. */
  toggle: RefObject<HTMLElement | null>;
  /** The layer itself: the first control inside takes the focus. */
  pop: RefObject<HTMLElement | null>;
};

export function usePopover({ open, close, anchor, toggle, pop }: Popover): void {
  useEffect(() => {
    if (!open) return;
    pop.current?.querySelector<HTMLElement>("input, button, a[href]")?.focus();
    const onDown = (event: PointerEvent) => {
      if (anchor.current && !anchor.current.contains(event.target as Node)) close();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        close();
        toggle.current?.focus();
        return;
      }
      //: A layer that calls itself a menu answers the arrows too: the items
      //: in turn, wrapping at either end -- what the role promises a screen
      //: reader, the keys have to keep.
      if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
      const layer = pop.current;
      if (!layer || layer.getAttribute("role") !== "menu") return;
      const items = [...layer.querySelectorAll<HTMLElement>('[role="menuitem"]')];
      if (!items.length) return;
      event.preventDefault();
      const at = items.indexOf(document.activeElement as HTMLElement);
      const step = event.key === "ArrowDown" ? 1 : -1;
      items[(at + step + items.length) % items.length]?.focus();
    };
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, close, anchor, toggle, pop]);
}
