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
  useState,
  type ReactNode,
} from "react";

/** Reread the world after something changed it. Provided once, near the root. */
const Refresh = createContext<() => Promise<void>>(async () => {});

export function ActionsProvider({
  refresh,
  children,
}: {
  refresh: () => Promise<void>;
  children: ReactNode;
}) {
  return <Refresh.Provider value={refresh}>{children}</Refresh.Provider>;
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
      <button className="link" onClick={of.forget} aria-label="убрать сообщение">
        ×
      </button>
    </p>
  );
}
