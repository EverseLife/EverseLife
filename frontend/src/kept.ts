// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * View settings that outlive a reload.
 *
 * How the interface is arranged is a **setting**, not a state of the world:
 * which scene tab is open, which tab of the rail, which groups of the
 * inventory are unfolded, whether the map's camera comes along. A setting that
 * forgets itself nags -- the sidebar's fold and the display density have been
 * remembered for exactly that reason, and every other flag of the arrangement
 * is remembered here for the same one.
 *
 * **No decision records this rule yet.** The two settings above were written
 * one at a time, and neither the redesign (D-238, `50-interface/09`) nor the
 * brief says anything about what outlives a session -- so this docstring is
 * the whole of the contract until a D-number is taken for it. Do not cite one
 * that does not say it.
 *
 * What is **not** kept, and the line is what the rule is worth:
 *
 * - anything half-typed -- a search box, a form, an amount;
 * - anything the server answers per session -- a book, a look;
 * - any navigation carrying an argument the world can take away -- a thread,
 *   a vessel, a plot, **the map's layer** (a pointer relative to the body, not
 *   a height: `GraphMap` explains why storing it broke the map);
 * - any argument of a command with consequences in the world -- what the talk
 *   sends as the kind of a line and as its loudness decides who hears it and
 *   how far it leaks (D-043, D-050), and a choice made a week ago must not
 *   speak for a sentence typed today.
 *
 * Keys are `everselife.<what>.<flag>`, the spelling `density`, `session` and
 * the folds already use. They are not per-account -- a browser holds one
 * session's token at a time -- so whatever names a piece of the **world** is
 * dropped at logout by `forgetKept`, and only the arrangement stays.
 *
 * A browser without storage -- private mode, storage switched off -- reads
 * nothing and writes nothing, and the interface opens on its defaults. That is
 * the only failure mode, and it is silent on purpose: the try/catch lives here
 * so that no call site of `kept`/`keep` has to remember it. (The token, the
 * density and the summary's mark still keep their own: each is read before
 * React exists or from an external store, and neither is a view setting.)
 */

import { useCallback, useEffect, useRef, useState } from "react";

/** The raw text under the key, or `null` when there is none to be had. */
export function readKept(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    /* a browser without storage forgets, and that is fine */
    return null;
  }
}

/** Write the text under the key; `null` empties the box rather than filling it. */
export function writeKept(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* see above */
  }
}

/**
 * How one setting travels to the box and back.
 *
 * `read` answers `null` for anything it does not accept -- an empty box, a
 * word that is no longer one of the choices, a shape from an older build --
 * and the call site opens on its default instead. Storage outlives the code
 * that wrote it, so every read validates, and none of it may throw.
 *
 * `write` answers `null` for a value not worth a key: a flag left at its
 * default, an empty set. Then the box is emptied rather than filled, and a
 * browser is not left carrying a key per default.
 */
export type Wire<T> = {
  read: (raw: string) => T | null;
  write: (value: T) => string | null;
};

/** A yes/no flag whose default is no: only a yes leaves a key behind. */
export const FLAG: Wire<boolean> = {
  read: (raw) => (raw === "1" ? true : null),
  write: (value) => (value ? "1" : null),
};

/**
 * A yes/no flag whose default is **yes** -- the map's camera tether, say.
 *
 * A wire of its own rather than `FLAG` under a different default, because the
 * two have to agree: `FLAG` writes nothing for a no, so a `FLAG` defaulting to
 * yes would read a deliberate no back out as a yes and quietly refuse ever to
 * be turned off.
 */
export const UNFLAG: Wire<boolean> = {
  read: (raw) => (raw === "0" ? false : null),
  write: (value) => (value ? null : "0"),
};

/** One of a closed list of words -- a tab, a map layer, a sorting axis. */
export function oneOf<T extends string | null>(allowed: readonly Exclude<T, null>[]): Wire<T> {
  return {
    read: (raw) => ((allowed as readonly string[]).includes(raw) ? (raw as T) : null),
    write: (value) => (value == null ? null : String(value)),
  };
}

/** A whole number, or `null` for "no choice made". */
export const WHOLE: Wire<number | null> = {
  //: Spelled out in digits, not handed to `Number`, which reads "0x40" as 64,
  //: "1e3" as 1000 and an empty box as 0. Nothing here writes those, but the
  //: contract of this module is that a read validates whatever it finds.
  read: (raw) => {
    if (!/^-?\d+$/.test(raw)) return null;
    const value = Number(raw);
    return Number.isSafeInteger(value) ? value : null;
  },
  write: (value) => (value == null ? null : String(value)),
};

/** Anything JSON carries, checked on the way in by `accept`. */
export function json<T>(accept: (value: unknown) => T | null): Wire<T> {
  return {
    read: (raw) => {
      try {
        return accept(JSON.parse(raw));
      } catch {
        //: Not our shape, or not JSON at all: the default, not a crash.
        return null;
      }
    },
    write: (value) => JSON.stringify(value),
  };
}

const NAMES = json<string[]>((value) =>
  Array.isArray(value) && value.every((name) => typeof name === "string") ? value : null,
);

/** A set of names -- which groups are unfolded, which rows are open. */
export const KEYS: Wire<Set<string>> = {
  read: (raw) => {
    const names = NAMES.read(raw);
    return names === null ? null : new Set(names);
  },
  //: An empty set is the default and leaves no key behind.
  write: (value) => (value.size === 0 ? null : JSON.stringify([...value])),
};

/** A name per place -- what was open in each node one has stood in. */
export const NAMED: Wire<Record<string, string>> = {
  read: json<Record<string, string>>((value) =>
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    Object.values(value).every((name) => typeof name === "string")
      ? (value as Record<string, string>)
      : null,
  ).read,
  write: (value) => (Object.keys(value).length === 0 ? null : JSON.stringify(value)),
};

/** The setting left under this key last time, or the default. */
export function kept<T>(key: string, fallback: T, wire: Wire<T>): T {
  const raw = readKept(key);
  if (raw === null) return fallback;
  const value = wire.read(raw);
  return value === null ? fallback : value;
}

/** Put the setting under the key. */
export function keep<T>(key: string, value: T, wire: Wire<T>): void {
  writeKept(key, wire.write(value));
}

/**
 * Drop every setting whose key starts with one of these, at logout.
 *
 * A browser is not per-account and these keys are not either, so anything that
 * names a piece of the **world** -- the nodes one stood in, the goods one
 * held -- would greet the next player of this browser out of the console with
 * the previous one's whereabouts. The arrangement itself is the browser
 * owner's and stays: nobody's tab or fold says anything about a world.
 */
export function forgetKept(...prefixes: string[]): void {
  try {
    //: Read the names out first: removing while walking `localStorage` by
    //: index skips every second key.
    const doomed: string[] = [];
    for (let at = 0; at < localStorage.length; at += 1) {
      const key = localStorage.key(at);
      if (key !== null && prefixes.some((start) => key.startsWith(start))) doomed.push(key);
    }
    for (const key of doomed) localStorage.removeItem(key);
  } catch {
    /* a browser without storage was carrying nothing to forget */
  }
}

/**
 * `useState` for a view setting: the same pair, remembered.
 *
 * The write happens in the setter rather than in an effect over the value: an
 * effect fires on the first render too, and would write back what it had just
 * read -- a setting nobody has touched must not rewrite itself.
 *
 * The key may change with what is being looked at (the inventory keeps a set
 * of open groups per axis). A changed key is a **different** setting, so it is
 * read afresh instead of carrying the old value over.
 */
export function useKept<T>(
  key: string,
  fallback: T,
  wire: Wire<T>,
): [T, (next: T | ((was: T) => T)) => void] {
  const [held, setHeld] = useState<{ key: string; value: T }>(() => ({
    key,
    value: kept(key, fallback, wire),
  }));
  let now = held;
  if (now.key !== key) {
    //: Set during the render that noticed, not in an effect: React restarts
    //: this render with the new value and nothing is ever drawn holding the
    //: previous key's setting.
    now = { key, value: kept(key, fallback, wire) };
    setHeld(now);
  }
  //: The setter is made once and never changes identity, so an effect that
  //: depends on it does not restart on every render. What it needs to know at
  //: the time it is called it reads out of a box: the key can change, and
  //: `oneOf(...)` written at a call site is a new object each render.
  //:
  //: The box is filled **after** the render commits, never during it. React is
  //: free to begin a render and throw it away, and a box filled in the render
  //: body would be left holding the key of work that never reached the screen
  //: -- the next setting would then be written under it. An event handler
  //: cannot run before the commit that drew the control it is on, so by the
  //: time `put` is called the box holds what is being looked at.
  const at = useRef({ key, wire, value: now.value });
  useEffect(() => {
    at.current = { key, wire, value: now.value };
  });
  const put = useCallback((next: T | ((was: T) => T)) => {
    //: Resolved out here rather than inside the updater: a state updater has
    //: to be pure, and React is free to call one twice.
    const said = typeof next === "function" ? (next as (was: T) => T)(at.current.value) : next;
    at.current = { ...at.current, value: said };
    keep(at.current.key, said, at.current.wire);
    setHeld({ key: at.current.key, value: said });
  }, []);
  return [now.value, put];
}
