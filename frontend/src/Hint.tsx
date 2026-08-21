// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * An explanation on demand: a "?" icon, and the text in a floating layer beside it.
 *
 * Explanations of a mechanic are needed once -- when a person sees the window
 * for the first time. Afterwards they turn into a background through which one
 * has to hunt for buttons: under the map five paragraphs had piled up for four
 * actions. So the explanation hides behind an icon, and what changes stays in
 * view -- numbers and buttons.
 *
 * **The icon stands by the title of what it explains**, never in a row of its
 * own. A column of identical "?" at the foot of a panel is worse than no
 * explanation at all: the finance tab had four of them one under another, and
 * nothing said which belonged to the transfer and which to the bank.
 *
 * The text appears **next to the icon** and moves nothing: the layer is
 * `position: fixed`, so it is outside the flow of every panel and clipped by
 * none of them -- the map panel cuts its content to itself (`overflow:
 * hidden`), and an explanation drawn inside it went under the cut. Where it
 * stands is decided after it is drawn: below the icon, or above it when the
 * bottom of the screen is nearer than the layer is tall.
 *
 * It is a note, not a window: no dimming behind it, no way out to press, and
 * **any click at all makes it go** -- including a click on the text itself.
 * Escape closes it too, and so do scrolling and resizing, which would leave it
 * hanging where the icon no longer is.
 */


import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";

/** How far the layer stands from the icon, and how close it may come to the screen's edge. */
const GAP = 6;
const EDGE = 8;

export function Hint({
  children,
  label = "подсказка",
}: {
  children: ReactNode;
  /** What the icon is called for whoever does not see it. */
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState<{ top: number; left: number } | null>(null);
  const mark = useRef<HTMLButtonElement>(null);
  const layer = useRef<HTMLSpanElement>(null);
  const id = useId();

  //: The height of the layer depends on how long the explanation is, and
  //: whether it fits under the icon depends on that height -- so it is
  //: measured drawn, and moved before the frame is painted.
  useLayoutEffect(() => {
    if (!open) {
      setAt(null);
      return;
    }
    const icon = mark.current?.getBoundingClientRect();
    const card = layer.current?.getBoundingClientRect();
    if (!icon || !card) return;
    let top = icon.bottom + GAP;
    if (top + card.height > window.innerHeight - EDGE) {
      const above = icon.top - GAP - card.height;
      top = above >= EDGE ? above : Math.max(EDGE, window.innerHeight - EDGE - card.height);
    }
    const left = Math.max(EDGE, Math.min(icon.left, window.innerWidth - EDGE - card.width));
    setAt({ top, left });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    //: Any click closes it, the one on the text included: there is nothing to
    //: do inside a note. The icon is the exception -- its own click toggles,
    //: and closing here would only reopen it a moment later.
    const away = (e: PointerEvent) => {
      if (mark.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    //: Scrolling and resizing take the icon out from under the layer, which is
    //: pinned to the screen. Following it would cost a frame on every scroll
    //: of every panel; an explanation that has been read costs nothing.
    const gone = () => setOpen(false);
    window.addEventListener("pointerdown", away, true);
    window.addEventListener("keydown", key);
    window.addEventListener("scroll", gone, true);
    window.addEventListener("resize", gone);
    return () => {
      window.removeEventListener("pointerdown", away, true);
      window.removeEventListener("keydown", key);
      window.removeEventListener("scroll", gone, true);
      window.removeEventListener("resize", gone);
    };
  }, [open]);

  return (
    <>
      <button
        ref={mark}
        type="button"
        className="hint bare"
        aria-label={label}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((was) => !was)}
      >
        ?
      </button>
      {open && (
        <span
          ref={layer}
          id={id}
          role="note"
          className="hint-pop"
          //: Until it has been measured it stands at the corner and invisible:
          //: the layout effect moves it before this frame reaches the screen.
          style={at ? { top: at.top, left: at.left } : { top: 0, left: 0, visibility: "hidden" }}
        >
          {children}
        </span>
      )}
    </>
  );
}
