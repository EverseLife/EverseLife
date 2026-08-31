// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * An action and its answer, kept where the hand was (01-interaction-model).
 *
 * The client used to hold one `busy` flag and one error line for the whole
 * application. Two things followed, and both read as a broken client:
 *
 * - **one action froze everything.** While "put the stone down" was in flight,
 *   the chat, the map and the order book were all disabled -- 144 controls on
 *   a single boolean;
 * - **the refusal appeared far from the refusal.** One `.trouble` line at the
 *   bottom of `<main>`, outside the scroll of the panel the button was in. The
 *   player saw a grey button, no explanation, and pressed again.
 *
 * The fix is not a registry of action ids: it is ordinary React state, put in
 * the component that owns the button. Whoever calls `useActions` gets their own
 * `busy` and their own `trouble`, so scope follows the component tree by
 * itself -- a section refuses in the section, a panel in the panel.
 *
 * Refreshing the world stays shared: every action ends by rereading `look`, and
 * that must not become one request per panel.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import type { RecipeBook, Session } from "./api";
import { collatorFor, DEFAULT_LOCALE, t, type Compare } from "./locale";
import type { NamesRu } from "./names";

/**
 * The language the world is read in, and the way to change it (D-251 wave III).
 *
 * It rides the same context as the recipe book and the names because it is the
 * same kind of thing: one per signed-in screen, wanted anywhere, and nothing a
 * panel should be handed as a prop through six layers.
 */
export type LocaleState = {
  /** The language of this account, as the greeting reported it. */
  locale: string;
  /** Every language the server has. One entry today; the switcher reads it. */
  locales: string[];
  /** Say it on the wire, then reload the words and the names of that language. */
  setLocale: (next: string) => Promise<void>;
};

//: Before a session there is nobody to have a language: the default one, and a
//: switch that does nothing rather than a hook that throws on the login screen.
const LocaleContext = createContext<LocaleState>({
  locale: DEFAULT_LOCALE,
  locales: [DEFAULT_LOCALE],
  setLocale: async () => {},
});

/** Reread the world after something changed it. Provided once, near the root. */
const Refresh = createContext<() => Promise<void>>(async () => {});
//: The session, the recipe book and the renames bundle never change for a
//: signed-in screen: one of each, reached from any panel, instead of 61
//: `session={session}` and 16 `book={book}` props through every layer
//: (review 2026-08-23).
const SessionContext = createContext<Session | null>(null);
const BookContext = createContext<RecipeBook | null>(null);
const NamesContext = createContext<NamesRu | null>(null);

export function ActionsProvider({
  refresh,
  session,
  book,
  names,
  locale,
  children,
}: {
  refresh: () => Promise<void>;
  session: Session;
  book: RecipeBook | null;
  names: NamesRu | null;
  locale: LocaleState;
  children: ReactNode;
}) {
  return (
    <Refresh.Provider value={refresh}>
      <SessionContext.Provider value={session}>
        <BookContext.Provider value={book}>
          <NamesContext.Provider value={names}>
            <LocaleContext.Provider value={locale}>{children}</LocaleContext.Provider>
          </NamesContext.Provider>
        </BookContext.Provider>
      </SessionContext.Provider>
    </Refresh.Provider>
  );
}

/** The socket session of the signed-in player. */
export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession outside ActionsProvider");
  return session;
}

/** The vault's recipe book, loaded once at login. */
export function useBook(): RecipeBook | null {
  return useContext(BookContext);
}

/** Display names for the wire's ids (D-251), loaded once at login. */
export function useNames(): NamesRu | null {
  return useContext(NamesContext);
}

/**
 * The language the world is read in: its code, the choice, and the switch.
 *
 * A panel that only formats a date or sorts a list wants `locale`; the account
 * panel wants all three. Outside a session it answers with the default
 * language and a switch that does nothing.
 */
export function useLocale(): LocaleState {
  return useContext(LocaleContext);
}

/**
 * The reading order of the current language, as a value React can watch.
 *
 * `locale.compare` is a module-level cell -- right for the pure modules that
 * sort deep inside `arrange` and `recipes`, and invisible to a `useMemo`. This
 * gives back a comparator whose **identity changes with the language**, so a
 * list memoised on it re-sorts when the player switches, instead of waiting
 * for some unrelated dependency to happen to change.
 */
export function useCompare(): Compare {
  const { locale } = useLocale();
  return useMemo(() => collatorFor(locale), [locale]);
}

/**
 * A counter that moves when the server says something about these kinds
 * (`"farm."`, `"ship."`): the dependency a panel's secondary read hangs on,
 * instead of the whole `look` object that changes on every event (D-226).
 */
export function useEdition(...kinds: string[]): number {
  const session = useSession();
  const [edition, setEdition] = useState(0);
  useEffect(() => {
    const stops = kinds.map((kind) => session.on(kind, () => setEdition((n) => n + 1)));
    return () => stops.forEach((stop) => stop());
    // oxlint-disable-next-line react-hooks/exhaustive-deps -- the kinds are literals
  }, [session, kinds.join("|")]);
  return edition;
}

/**
 * The world's reread on its own, without an action: for a panel whose state
 * flips on the clock -- a find showing itself when its term is up -- and
 * cannot wait for the next poll.
 */
export function useRefresh(): () => Promise<void> {
  return useContext(Refresh);
}

export type Actions = {
  /** Something this component started is still running. */
  busy: boolean;
  /** Run it, reread the world, and keep the refusal here if it comes. */
  act: (what: () => Promise<unknown>) => Promise<void>;
  /** The last refusal, in the engine's own words. */
  trouble: string | null;
  /** Put the refusal away: the player has read it. */
  forget: () => void;
};

export function useActions(): Actions {
  const refresh = useContext(Refresh);
  const [busy, setBusy] = useState(false);
  const [trouble, setTrouble] = useState<string | null>(null);

  const act = useCallback(
    async (what: () => Promise<unknown>) => {
      setTrouble(null);
      setBusy(true);
      try {
        await what();
        await refresh();
      } catch (error) {
        setTrouble(error instanceof Error ? error.message : String(error));
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  const forget = useCallback(() => setTrouble(null), []);
  return { busy, act, trouble, forget };
}

/**
 * The refusal itself, shown where the action was started.
 *
 * The engine already writes refusals for a person to read -- "не хватает
 * «Глина»: нужно ещё 1.895" -- so nothing is rephrased here. It is announced to
 * a screen reader as well: whoever pressed the button deserves the answer
 * whether or not they can see the strip appear.
 */
export function Refusal({ of }: { of: Actions }) {
  if (!of.trouble) return null;
  return (
    <p className="reason" role="alert">
      {of.trouble}{" "}
      <button className="link" onClick={of.forget} aria-label={t("ui-refusal-dismiss")}>
        ×
      </button>
    </p>
  );
}
