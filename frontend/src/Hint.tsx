/**
 * A hint on demand: a "?" icon, and the explanation in a small window of its own.
 *
 * Explanations of a mechanic are needed once -- when a person sees the window
 * for the first time. Afterwards they turn into a background through which
 * one has to hunt for buttons: under the map five paragraphs had piled up
 * for four actions. So the explanation hides behind an icon, and what
 * changes stays in view -- numbers and buttons.
 *
 * The explanation opens as a separate window over the page, not as a block
 * beside the icon. Expanding in place it pushed the row apart: the panel
 * scrolls, the wide block gave it a scrollbar, and the buttons the hand was
 * aiming at moved aside at the very moment of the tap. A window in a portal
 * lies outside every panel and outside every scroll, and moves nothing.
 *
 * The window itself is `HintWindow` and is shared: a rule of the world
 * (`Rule`) opens the same one, so both kinds of explanation appear in the same
 * place on screen and close by the same gestures.
 *
 * Works from the keyboard too: the icon focuses, `Enter` opens the window,
 * `Escape` closes it, and the focus comes back to the icon. The content lies
 * in markup, not in `title` -- the native tooltip appears after a second and
 * is not readable from a phone at all.
 */


import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

export function Hint({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const mark = useRef<HTMLButtonElement>(null);

  //: Focus returns to the icon: closing the window must not drop the person
  //: to the top of the document.
  const shut = () => {
    setOpen(false);
    mark.current?.focus();
  };

  return (
    <>
      <button
        ref={mark}
        type="button"
        className="hint bare"
        aria-label="подсказка"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        ?
      </button>
      {open && (
        <HintWindow label="Подсказка" onClose={shut}>
          {children}
        </HintWindow>
      )}
    </>
  );
}

/** The window an explanation opens in: a small card over the dimmed page.
 *
 * It hangs in a portal on `body` -- above every panel, outside every scroll,
 * and clipped by none of them: the map panel cuts its content to itself
 * (`overflow: hidden`), and an explanation drawn inside it went under the cut. */
export function HintWindow({
  label,
  onClose,
  children,
}: {
  label: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const shutter = useRef<HTMLButtonElement>(null);
  //: The window lives while it is open, so the panel behind it may re-render
  //: as often as it likes: the listener is hung once, and the way out of it
  //: is read through a ref -- rehanging it on every render would snatch the
  //: focus back to the button under the person's hands.
  const latest = useRef(onClose);
  useEffect(() => {
    latest.current = onClose;
  }, [onClose]);

  //: The window closes by Escape wherever the focus is -- an explanation must
  //: never hold the page hostage.
  useEffect(() => {
    shutter.current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") latest.current();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);

  return createPortal(
    <div
      className="veil"
      role="dialog"
      aria-modal="true"
      aria-label={label}
      //: A click past the card closes it; a click inside -- not, or selecting
      //: a word of the text would shut the window.
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section className="hint-card">
        <div className="hint-text">{children}</div>
        <button type="button" className="quiet hint-shut" ref={shutter} onClick={onClose}>
          Понятно
        </button>
      </section>
    </div>,
    document.body,
  );
}
