// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Display density (00-ux-principles, brief section 5).
 *
 * A dense interface frightens a newcomer; a simplified one enrages a veteran.
 * So there are three modes, and switching is free -- **a setting, not a
 * reward**: nothing here is unlocked by playing.
 *
 * What changes is row heights and padding. What never changes is the type size
 * (a "dense" mode that shrinks the font is unreadable rather than dense), the
 * layout -- the same things stay in the same places in all three -- and where
 * the world's rules live: behind the "?" beside the title they explain, in
 * every mode alike.
 *
 * | Mode     | Who for            | Rows and padding       |
 * |----------|--------------------|------------------------|
 * | `plain`  | the first hours    | roomy                  |
 * | `normal` | most people        | the middle             |
 * | `dense`  | traders, rulers    | tight                  |
 *
 * The value lives on `<html>` as `data-density`, so CSS reads it without React,
 * and in `localStorage`, so a returning player keeps their choice.
 */

import { useSyncExternalStore } from "react";

export const DENSITIES = ["plain", "normal", "dense"] as const;
export type Density = (typeof DENSITIES)[number];

const KEY = "everselife.density";
const DEFAULT: Density = "normal";

const listeners = new Set<() => void>();
let current: Density = read();

function read(): Density {
  try {
    const kept = localStorage.getItem(KEY);
    if (kept && (DENSITIES as readonly string[]).includes(kept)) return kept as Density;
  } catch {
    /* приватный режим — настройка просто не запомнится */
  }
  return DEFAULT;
}

/** Put the choice where CSS can see it. Called before React mounts, too. */
export function applyDensity(): void {
  document.documentElement.dataset.density = current;
}

export function getDensity(): Density {
  return current;
}

export function setDensity(next: Density): void {
  if (next === current) return;
  current = next;
  applyDensity();
  try {
    localStorage.setItem(KEY, next);
  } catch {
    /* не запомнилось — не беда, на этот сеанс настройка всё равно применена */
  }
  for (const notify of [...listeners]) notify();
}

export function subscribeDensity(notify: () => void): () => void {
  listeners.add(notify);
  return () => listeners.delete(notify);
}

/** The mode, for whoever renders differently in it. CSS reads `data-density`. */
export function useDensity(): Density {
  return useSyncExternalStore(subscribeDensity, getDensity, () => DEFAULT);
}

/** Names for the setting. The mode is described by whom it suits, not by a number. */
export const DENSITY_NAMES: Record<Density, { label: string; about: string }> = {
  plain: { label: "простой", about: "просторные строки и отступы" },
  normal: { label: "обычный", about: "середина: так игра выглядит по умолчанию" },
  dense: { label: "плотный", about: "максимум данных на экране" },
};
