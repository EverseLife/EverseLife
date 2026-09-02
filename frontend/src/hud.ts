// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Asking the sidebar to open a tab from outside it (D-238).
 *
 * The header's instrument strip carries quick buttons -- the carried weight
 * opens the inventory tab, the balance opens a transfer. The sidebar owns its
 * tab state and the header must not: same one-asking-many-places pattern as
 * `people.ts`, so the header stays ignorant of who answers.
 */

const SIDEBAR_TAB = "everselife:sidebar-tab";

//: On a narrow screen the sidebar is mounted only in the "я" zone, and the
//: asking switches the zone -- so at dispatch time there is nobody listening
//: yet. The ask is kept until the sidebar mounts and collects it.
let pending: string | null = null;

/** Open the named sidebar tab, wherever the asking came from. */
export function askSidebarTab(tab: string): void {
  pending = tab;
  window.dispatchEvent(new CustomEvent(SIDEBAR_TAB, { detail: tab }));
}

/** The tab asked for before the sidebar existed: read once, then cleared. */
export function pendingSidebarTab(): string | null {
  const tab = pending;
  pending = null;
  return tab;
}

export function onSidebarTab(handle: (tab: string) => void): () => void {
  const onAsk = (event: Event) => handle(String((event as CustomEvent).detail));
  window.addEventListener(SIDEBAR_TAB, onAsk);
  return () => window.removeEventListener(SIDEBAR_TAB, onAsk);
}

//: What folds stays folded (brief, desktop layout): the sidebar to its
//: rail, the chat strip to its one line. A returning player keeps each
//: fold, the way they keep the density: it is a setting, and a setting
//: that forgets itself nags.
export type Pane = "sidebar" | "chat";

const foldKey = (pane: Pane) => `everselife.${pane}.folded`;

/** Whether the pane was left folded last time. */
export function folded(pane: Pane): boolean {
  try {
    return localStorage.getItem(foldKey(pane)) === "1";
  } catch {
    /* a browser without storage forgets, and that is fine */
  }
  return false;
}

export function rememberFolded(pane: Pane, isFolded: boolean): void {
  try {
    if (isFolded) localStorage.setItem(foldKey(pane), "1");
    else localStorage.removeItem(foldKey(pane));
  } catch {
    /* see above */
  }
}
