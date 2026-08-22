// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Asking about a person from anywhere (D-222).
 *
 * A name is met all over the client -- in the room's talk, in a circle, in a
 * letter -- and from any of those places one wants the same two things: the
 * person's card, and a word with them. The card is a modal over the world, the
 * word is a thread in the sidebar's "Net" tab; neither lives where the name was
 * clicked. So the click does not open anything itself: it asks, and whoever
 * owns the modal or the tab answers. One asking, many places.
 */

const PROFILE = "everselife:profile";
const THREAD = "everselife:thread";

/** Show somebody's card. */
export function askProfile(name: string): void {
  window.dispatchEvent(new CustomEvent(PROFILE, { detail: name }));
}

/** Open the correspondence with somebody in the "Net" tab. */
export function askThread(name: string): void {
  window.dispatchEvent(new CustomEvent(THREAD, { detail: name }));
}

function listen(kind: string, handle: (name: string) => void): () => void {
  const onAsk = (event: Event) => handle(String((event as CustomEvent).detail));
  window.addEventListener(kind, onAsk);
  return () => window.removeEventListener(kind, onAsk);
}

export const onProfile = (handle: (name: string) => void) => listen(PROFILE, handle);
export const onThread = (handle: (name: string) => void) => listen(THREAD, handle);
