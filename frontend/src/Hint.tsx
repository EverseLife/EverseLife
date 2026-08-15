/**
 * A hint on demand: a "?" icon expanding on hover.
 *
 * Explanations of a mechanic are needed once -- when a person sees the window
 * for the first time. Afterwards they turn into a background through which
 * one has to hunt for buttons: under the map five paragraphs had piled up
 * for four actions. So the explanation hides behind an icon, and what
 * changes stays in view -- numbers and buttons.
 *
 * Works from the keyboard too: the icon focuses, the hint expands on
 * `:focus-visible` as well as `:hover`. The content lies in markup, not in
 * `title` -- the native tooltip appears after a second and is not readable
 * from a phone at all.
 */


import type { ReactNode } from "react";

export function Hint({ children }: { children: ReactNode }) {
  return (
    <span className="hint" tabIndex={0} role="note">
      ?<span className="hint-body">{children}</span>
    </span>
  );
}
