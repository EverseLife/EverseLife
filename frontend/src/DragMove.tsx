// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Moving a stack by dragging it (D-238, amendment 4).
 *
 * Every pair of surfaces that exchanges goods -- hands and the floor, hands
 * and a chest, a hold, a convoy, a stall -- used to speak only through a
 * number field and a button in every row. The drag is the accelerator laid
 * over that: grab a row, drop it on the other surface, say how much in a
 * small popover ("всё · ½ · число"). A single piece skips the question.
 *
 * The button path stays untouched and equal: keyboards, screen readers and
 * touch keep working exactly as before, and the engine sees the same command
 * either way. The drag never invents an action -- a zone only accepts what
 * its buttons could already do.
 *
 * The payload rides a module variable in `drag.ts` rather than
 * `dataTransfer`: the data is unreadable during `dragover` by design, and the
 * drag never leaves this window anyway. Rows opt in with `grip` from there.
 */

import { useEffect, useRef, useState } from "react";
import { counted, trim } from "./amounts";
import { answered, askless, carriedStack, dropCarried, fits, halved, type DragStack } from "./drag";

type Ask = { stack: DragStack; x: number; y: number };

/**
 * A surface stacks can be dropped on.
 *
 * `zone` names the surface so it refuses its own rows; `onMove` sends the
 * same command the row's button would. The zone highlights while a foreign
 * stack hovers it, asks the amount on drop, and flashes once when the move
 * is sent -- the one-shot end of the gesture, within the motion budget.
 */
export function DropZone({
  zone,
  accepts,
  onMove,
  disabled,
  hint,
  children,
}: {
  zone: string;
  /** Which surfaces this zone takes from: `onMove` assumes the source, so
   *  the zone must not accept a stranger it would misname a command for. */
  accepts: readonly string[];
  onMove: (stack: DragStack, amount: number) => void;
  /** While the panel is busy the zone goes deaf rather than queueing moves. */
  disabled?: boolean;
  /** The standing invitation, shown always: "перетащите сюда, чтобы …". */
  hint?: string;
  children: React.ReactNode;
}) {
  const [over, setOver] = useState(false);
  const [ask, setAsk] = useState<Ask | null>(null);
  const [landed, setLanded] = useState(false);
  //: The leave/enter pair fires on every child border; counting keeps the
  //: highlight steady while the pointer crosses rows inside the zone.
  const depth = useRef(0);
  const flashTimer = useRef<number | null>(null);
  useEffect(
    () => () => {
      if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
    },
    [],
  );

  const takes = (event: React.DragEvent) => {
    //: A file dragged in from the OS is somebody else's gesture entirely --
    //: and the belt to the window-listener braces in `drag.ts`.
    if (event.dataTransfer.types.includes("Files")) return false;
    const stack = carriedStack();
    return !disabled && stack !== null && stack.zone !== zone && fits(accepts, stack.zone);
  };

  const move = (stack: DragStack, amount: number) => {
    setAsk(null);
    //: The popover may outlive the panel going busy; the buttons are greyed
    //: then, and the answer must not slip past them.
    if (disabled) return;
    //: A frame of "off" between two flashes, or the second drop within the
    //: 450ms would not restart the animation.
    if (flashTimer.current !== null) window.clearTimeout(flashTimer.current);
    setLanded(false);
    requestAnimationFrame(() => {
      setLanded(true);
      flashTimer.current = window.setTimeout(() => setLanded(false), 450);
    });
    onMove(stack, amount);
  };

  return (
    <div
      className={`drop-zone${over ? " drop-over" : ""}${landed ? " drop-landed" : ""}`}
      onDragEnter={(event) => {
        if (!takes(event)) return;
        event.preventDefault();
        depth.current += 1;
        setOver(true);
      }}
      onDragOver={(event) => {
        if (!takes(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = "move";
      }}
      onDragLeave={() => {
        if (depth.current > 0) depth.current -= 1;
        if (depth.current === 0) setOver(false);
      }}
      onDrop={(event) => {
        depth.current = 0;
        setOver(false);
        if (!takes(event)) return;
        event.preventDefault();
        const stack = dropCarried()!;
        //: One piece has no "how much": the question would cost a click and
        //: answer itself.
        if (askless(stack)) move(stack, stack.amount);
        else setAsk({ stack, x: event.clientX, y: event.clientY });
      }}
    >
      {children}
      {hint && !disabled && <p className="drop-hint">{hint}</p>}
      {ask && (
        <AmountAsk
          ask={ask}
          onPick={(amount) => move(ask.stack, amount)}
          onClose={() => setAsk(null)}
        />
      )}
    </div>
  );
}

/**
 * The question a drop asks: the whole stack, half of it, or a typed number.
 * Whole is first and primary -- it is what the buttons did by default.
 */
function AmountAsk({
  ask,
  onPick,
  onClose,
}: {
  ask: Ask;
  onPick: (amount: number) => void;
  onClose: () => void;
}) {
  const { stack } = ask;
  const box = useRef<HTMLDivElement | null>(null);
  const field = useRef<HTMLInputElement | null>(null);
  const whole = counted(stack.goods);
  const half = halved(stack);
  const [typed, setTyped] = useState(() => trim(half > 0 ? half : stack.amount));

  useEffect(() => {
    field.current?.focus();
    const onDown = (event: PointerEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) onClose();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("pointerdown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const send = () => {
    const amount = answered(stack, Number(typed));
    if (amount !== null) onPick(amount);
  };

  //: Clamped to the window so a drop at the edge does not hide the question.
  const left = Math.min(ask.x, window.innerWidth - 240);
  const top = Math.min(ask.y + 8, window.innerHeight - 150);

  return (
    <div
      ref={box}
      className="hud-pop drop-pop"
      style={{ left, top }}
      role="dialog"
      aria-label={`Сколько: ${stack.label}`}
    >
      <p className="drop-what">
        {stack.label} · {trim(stack.amount)}
      </p>
      <div className="row">
        <button onClick={() => onPick(stack.amount)}>всё</button>
        {half > 0 && (
          <button className="quiet" onClick={() => onPick(half)}>
            ½
          </button>
        )}
      </div>
      <div className="row">
        <input
          ref={field}
          type="number"
          min={0}
          max={stack.amount}
          step={whole ? 1 : "any"}
          value={typed}
          onChange={(event) => setTyped(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") send();
          }}
          aria-label="Сколько"
        />
        <button className="quiet" onClick={send}>
          ОК
        </button>
      </div>
    </div>
  );
}
