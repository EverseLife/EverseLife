// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The everse.life alpha client.
 *
 * The layout is four permanent zones (D-050):
 *
 * - **top banner** -- where you are, what is with the body, the account;
 * - **left sidebar** -- what works over the Net: character, inventory, jobs,
 *   trade, knowledge, holdings. Always available, even from the road:
 *   household bills are money, not matter (D-149). No city governing here:
 *   authority is in-person and lives in the administration (D-155);
 * - **main window** -- tabs: map - location. The location is in-person: en
 *   route it is gone, because you are not in the node;
 * - **bottom strip** -- the location's live chat, circles included (D-238):
 *   they only decide who hears what is said.
 *
 * The organising principle is the same as the world's: the sidebar is remote,
 * the main window is in-person. The player absorbs the world's structure just
 * by using the interface. The visual language is still the designer's work (D-049, D-055).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "./api";
import * as amounts from "./amounts";
import {
  PART_OF_TOUCH,
  Session,
  compose,
  type Enrollment,
  type LiveLook,
  type Look,
  type RecipeBook,
  type Parts,
} from "./api";
import { Alpha } from "./panels/Alpha";
import { Chat } from "./panels/Chat";
import { GraphMap } from "./panels/GraphMap";
import { Intro } from "./panels/Intro";
import { Login } from "./panels/Login";
import { Printer } from "./panels/Printer";
import { Profile } from "./panels/Profile";
import { Register } from "./panels/Register";
import { Sidebar } from "./panels/Sidebar";
import { Summary, markSeen, useDigest } from "./panels/Summary";
import { Stand } from "./panels/Stand";
import { TopBar } from "./panels/TopBar";
import { type View } from "./views";
import { powSettings, type PowSettings } from "./pow";
import { wearPlanet } from "./theme";
import { useNarrow } from "./narrow";
import { ActionsProvider, type LocaleState } from "./actions";
import { DEFAULT_LOCALE, forget, learn, loadWords, t, type Words } from "./locale";
import { namesOf, type Names, type Renames } from "./names";
import { onSidebarTab } from "./hud";
import { onProfile, onThread } from "./people";

/** The phone's four sections: the same zones, one at a time (brief section 9).
 *
 *  `label` is a getter: the list is built once at import, and the language is
 *  learnt at the greeting -- long afterwards. A plain string would freeze the
 *  word of whichever language was spoken then. */
const ZONES = [
  { id: "me", get label() { return t("ui-app-zone-me"); } },
  { id: "here", get label() { return t("ui-app-zone-here"); } },
  { id: "map", get label() { return t("ui-app-zone-map"); } },
  { id: "talk", get label() { return t("ui-app-zone-talk"); } },
] as const;
type Zone = (typeof ZONES)[number]["id"];

/** How long the screen gathers events before rereading, and the reserve poll (D-226). */
const REREAD_DELAY_MS = 150;
const RESERVE_POLL_MS = 30_000;
/** How long an action waits for its own events before rereading on its own. */
const SETTLE_MS = 400;
/** Which `touches` name the live look rather than a cached part. */
const LIVE_TOUCHES = new Set([
  "body",
  "inventory",
  "node",
  "money",
  "doings",
  "mining",
  "farm",
  "ships",
  "market",
  "net",
  "city",
  "justice",
  "bank",
  "holdings",
]);

export default function App() {
  const session = useRef(new Session());
  //: The live part and the slow parts are read apart (D-226, step 2): `look`
  //: on every event that touches the body, the place or the pocket, a part
  //: only when an event names it. The panels see them put together.
  const [live, setLive] = useState<LiveLook | null>(null);
  const [parts, setParts] = useState<Parts | null>(null);
  const look = useMemo<Look | null>(
    () => (live && parts ? compose(live, parts) : null),
    [live, parts],
  );
  const [values, setValues] = useState<Record<string, any> | null>(null);
  //: The vault catalog is needed by several machine panels at once: we load it once.
  const [book, setBook] = useState<RecipeBook | null>(null);
  //: Display names for the wire's ids (D-251): loaded with the catalogs.
  const [names, setNames] = useState<Names | null>(null);
  //: Kept in a ref as well: the words of a language are built from the names,
  //: and the login sequence needs them before React has flushed the state.
  const namesRef = useRef<Names | null>(null);
  //: Every language's names, as `/public/renames` sent them. A ref rather
  //: than state: nothing draws from it directly, `show` picks out of it.
  const renamesRef = useRef<Renames | null>(null);
  //: The words of the account's language (D-251 wave III). One object holds
  //: both the code and the list of languages the server has, so the switcher
  //: never guesses; `learn` puts the same words where the pure modules --
  //: sorting, `t` -- read them without a hook.
  const [words, setWords] = useState<Words | null>(null);
  const [pow, setPow] = useState<PowSettings | null>(null);
  //: The screen before login: login or registration (D-187). The last login's
  //: token is tried silently: while it is checked, the login screen does not flicker.
  const [screen, setScreen] = useState<"login" | "register">("login");
  const [resuming, setResuming] = useState(() => Boolean(Session.remembered()));
  const resumed = useRef(false);
  //: Somebody's card, asked for by right-clicking a name anywhere (D-222).
  const [card, setCard] = useState<string | null>(null);
  useEffect(() => onProfile(setCard), []);
  //: "Write" from the card: the Net lives in the sidebar, and on a phone the
  //: sidebar is the "я" section. The header's quick buttons open sidebar tabs
  //: the same way (D-238).
  useEffect(() => onThread(() => setWhere_("me")), []);
  useEffect(() => onSidebarTab(() => setWhere_("me")), []);
  const [intro, setIntro] = useState(false);
  //: The summary is shown once on arrival, not on every refresh: a curtain that
  //: comes back every five seconds is a fault, not a notification.
  const [digestShown, setDigestShown] = useState(false);
  const [trouble, setTrouble] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<View>("map");
  //: On a phone the four zones become four sections, one at a time (03-screens).
  const narrow = useNarrow();
  const [where_, setWhere_] = useState<Zone>("here");

  //: Read through refs so the subscription below is made once per session
  //: and never loses what it gathered while the screen was rerendering.
  const partsRef = useRef<Parts | null>(null);
  partsRef.current = parts;

  const refresh = useCallback(async () => {
    //: While the identity is not named there is nothing to refresh: no session
    //: yet. Otherwise the very first login step -- reading doors -- would hit "no session".
    if (!session.current.name) return;
    //: The first look of a session reads everything: no half screen.
    if (!partsRef.current) {
      const [seen, all] = await Promise.all([session.current.look(), session.current.parts()]);
      setParts(all);
      setLive(seen);
      return;
    }
    setLive(await session.current.look());
  }, []);

  /** Reread the slow parts an event named. */
  const rereadParts = useCallback(async (names: Iterable<keyof Parts>) => {
    const wanted = [...new Set(names)];
    if (!wanted.length || !session.current.name) return;
    const fresh = await Promise.all(wanted.map((name) => session.current.part(name)));
    setParts((known) => {
      if (!known) return known;
      const next = { ...known };
      wanted.forEach((name, i) => {
        (next as Record<keyof Parts, unknown>)[name] = fresh[i];
      });
      return next;
    });
  }, []);

  /** Every action goes through this: one error -- one line at the bottom of the screen. */
  //: An answer is a confirmation, not the state (D-226): what the action
  //: changed arrives as events within a moment, and the screen rereads on
  //: them. `settle` waits for that moment; only an action that said nothing
  //: -- none should, but the reserve costs nothing -- rereads by itself.
  const lastHeard = useRef(0);
  const settle = useCallback(async () => {
    const since = Date.now();
    await new Promise<void>((resolve) => setTimeout(resolve, SETTLE_MS));
    if (lastHeard.current < since) await refresh();
  }, [refresh]);

  const act = useCallback(
    async (what: () => Promise<unknown>) => {
      setTrouble(null);
      setBusy(true);
      try {
        await what();
        await settle();
      } catch (error) {
        setTrouble(error instanceof Error ? error.message : String(error));
      } finally {
        setBusy(false);
      }
    },
    [settle],
  );

  /**
   * Show the names of this language, out of the bundle already fetched.
   *
   * Every language arrives in one read (`/public/renames`), so this is a
   * lookup rather than a request: a switch must not wait on the network for
   * the word «Железная руда» to become "Iron ore".
   */
  const show = useCallback((locale: string) => {
    const table = namesOf(renamesRef.current, locale);
    namesRef.current = table;
    setNames(table);
  }, []);

  /** The vault catalogs: both machine panels and the quality forecast wait for them. */
  const catalogs = useCallback(async () => {
    const { values } = await api.constants();
    setValues(values);
    setPow(powSettings(values));
    //: The constants ride along with the catalog: a machine panel that needs
    //: one number (how many kinds go into an attempt, D-209) reads it from the
    //: same book it reads recipes from, without a second prop through every layer.
    //: The renames bundle travels with it (D-251): the wire speaks ids, the
    //: player reads Russian, and every panel asks `useNames` for the bridge.
    //: A failed bundle must not refuse the login: every helper falls back to
    //: the raw id, and that fallback is unreachable if this read can sink the
    //: whole catalog load (an older server has no `/public/renames` at all).
    const [book, renames] = await Promise.all([
      api.recipes(),
      api.renames().catch(() => null),
    ]);
    //: Piece or weight is read off the same book (D-212), and every panel that
    //: draws a quantity asks `amounts`, not a prop of its own.
    amounts.learn(book);
    setBook({ ...book, constants: values });
    //: The whole bundle is kept, not one language's half of it: a switch then
    //: costs nothing over the wire, and the words for the new language are
    //: already in hand when `speak` builds the messages over them.
    renamesRef.current = renames;
    show(session.current.locale || DEFAULT_LOCALE);
  }, [show]);

  /**
   * Start speaking a language: its words, built over the names, become the
   * ones every `t` and every sort read (D-251 wave III).
   *
   * Only after a greeting: which language this account reads is the account's
   * business, and the server says it in `hello`.
   */
  const speak = useCallback(async (want: string) => {
    const next = await loadWords(want || DEFAULT_LOCALE, namesRef.current);
    learn(next);
    setWords(next);
  }, []);

  /**
   * Change the language of the account (D-249, D-251 wave III).
   *
   * The wire first: the server keeps the choice and starts answering in the
   * new language from the next command, so a refusal it renders and a sentence
   * this client renders never disagree. Then both bundles are read again --
   * the messages and the display names -- because a language is both.
   */
  const setLocale = useCallback(
    async (next: string) => {
      await session.current.send("account.locale", { locale: next });
      //: The server changed both the account and this session; the client's
      //: copy of the session must not go on saying the old one until the next
      //: revive happens to reread it from `hello`.
      session.current.locale = next;
      //: Both halves of a language change: the names of things and the
      //: sentences about them. The names are already here -- every language
      //: came in one bundle (D-251 wave V) -- so only the messages are read.
      show(next);
      await speak(next);
    },
    [show, speak],
  );

  const enter = (email: string, password: string) =>
    act(async () => {
      await catalogs();
      await session.current.open(email, password);
      await speak(session.current.locale);
    });

  //: Auto-login by token (D-187): F5 does not ask for the password. Refusal --
  //: silently to the login screen: an expired token is not the user's error.
  useEffect(() => {
    const token = Session.remembered();
    //: One rise per page: StrictMode in development calls the effect twice,
    //: and two sockets with one token is a race, not a login.
    if (!token || resumed.current) return;
    resumed.current = true;
    void (async () => {
      try {
        await catalogs();
        await session.current.resume(token);
        await speak(session.current.locale);
        await refresh();
      } catch {
        /* жетон истёк или отозван — обычный вход */
      } finally {
        setResuming(false);
      }
    })();
  }, [catalogs, refresh, speak]);

  /** Registration: four client steps -- one server command (D-187). Printing
   *  at the chosen door: zero on the account, the grant is the city's business
   *  (D-153). Then the Forerunner's word: nobody else can explain who he is (D-182). */
  const join = (application: Enrollment) =>
    act(async () => {
      await catalogs();
      await session.current.create(application);
      await speak(session.current.locale);
      setIntro(true);
    });

  /** Logout: the token is revoked, the login screen. */
  const logout = () =>
    act(async () => {
      await session.current.logout();
      setLive(null);
      setParts(null);
      //: The language goes with the account: the login screen must not be left
      //: speaking the last player's, and the next login loads its own words.
      //: The name table goes with it. Today it is one table for every
      //: language and dropping it costs a refetch; from wave V it is not, and
      //: a table kept across a logout would show the next player the previous
      //: one's words for everything the message functions name.
      forget();
      setWords(null);
      setNames(null);
      namesRef.current = null;
      renamesRef.current = null;
      setScreen("login");
    });

  const locale = useMemo<LocaleState>(
    () => ({
      locale: words?.locale ?? DEFAULT_LOCALE,
      locales: words?.locales ?? [DEFAULT_LOCALE],
      setLocale,
    }),
    [words, setLocale],
  );

  //: The page's language is the account's, so the browser hyphenates, spells
  //: and reads it aloud correctly. `index.html` only carries the default: the
  //: attribute belongs to the session from the greeting onwards.
  useEffect(() => {
    document.documentElement.lang = locale.locale;
  }, [locale.locale]);

  const { digest, reread } = useDigest(session.current, Boolean(look));
  const waiting = digest?.attention.length ?? 0;

  const ongoing = Boolean(look?.travel);
  //: Exploration is a body state (D-152): the scout left on their own, and
  //: while in the field, in-person is closed, as in sleep. Return -- by a button on the map.
  const exploring = Boolean(look?.survey);
  const away = ongoing || exploring;

  //: The base tone belongs to the planet you stand on (D-074, D-080): arriving
  //: repaints the screen, and that is the fair price of a week through the
  //: void. Before login nobody stands anywhere -- the theme stays the default.
  useEffect(() => {
    if (!look) return;
    //: "aboard" is a node property id, like "woods" or "stones": the vault
    //: sets it in data, and the client only reads it.
    wearPlanet(look.clock?.planet ?? null, (look.node?.features ?? []).includes("aboard"));
  }, [look]);

  //: The server speaks first (D-226): whatever happens to the player arrives
  //: as an event, and the screen rereads after it. Several events in one
  //: breath -- a craft finished and its goods created -- are one reread.
  //: `touches` say what: the live look only when a live part is named, a
  //: cached part only when it is named. Subscribed once per session.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    const touched = new Set<keyof Parts>();
    let live = false;
    let everything = false;
    const stop = session.current.on("*", (happening) => {
      if (happening.touches.length === 0) return;
      if (happening.event === "session.reread" || happening.touches.includes("all")) {
        everything = true;
      }
      for (const touch of happening.touches) {
        const part = PART_OF_TOUCH[touch];
        if (part) touched.add(part);
        else if (LIVE_TOUCHES.has(touch)) live = true;
      }
      if (timer) return;
      timer = setTimeout(() => {
        timer = null;
        const names = everything ? (Object.keys(PART_OF_TOUCH) as (keyof Parts)[]) : [...touched];
        const reads: Promise<unknown>[] = [];
        if (live || everything) {
          lastHeard.current = Date.now();
          reads.push(refresh());
        }
        if (names.length) reads.push(rereadParts(names));
        touched.clear();
        live = false;
        everything = false;
        void Promise.all(reads).catch(() => {});
      }, REREAD_DELAY_MS);
    });
    return () => {
      stop();
      if (timer) clearTimeout(timer);
    };
  }, [refresh, rereadParts]);

  //: A token refused on the way back up is the end of the session: the
  //: login screen, not an endless rise (D-226).
  useEffect(
    () =>
      session.current.on("session.lost", () => {
        setLive(null);
        setParts(null);
        setScreen("login");
      }),
    [],
  );

  //: The shelf belongs to the place: a new node is a new library, or none.
  const nodeKey = live?.node?.key;
  useEffect(() => {
    //: Through the ref: a reread of the parts must not reread the shelf.
    if (!partsRef.current) return;
    void rereadParts(["shelf"]).catch(() => {});
  }, [nodeKey, rereadParts]);

  //: The reserve: a socket cut without a close frame hears nothing, and a
  //: half-minute poll is what finds that out. Not the way news arrives.
  const signedIn = Boolean(live);
  useEffect(() => {
    if (!signedIn) return;
    const timer = setInterval(() => void refresh().catch(() => {}), RESERVE_POLL_MS);
    return () => clearInterval(timer);
  }, [signedIn, refresh]);

  //: Set out on the road or exploring -- in-person tabs close by themselves:
  //: you are not in the node.
  useEffect(() => {
    if (away) setView("map");
  }, [away]);

  //: The last login's token is being checked -- the login screen does not flicker (D-187).
  if (!look && resuming) {
    return (
      <main className="entry auth">
        <p className="note center">…</p>
      </main>
    );
  }

  if (!look && screen === "register") {
    return (
      <Register
        busy={busy}
        trouble={trouble}
        onSubmit={join}
        onBack={() => {
          setTrouble(null);
          setScreen("login");
        }}
      />
    );
  }

  if (!look) {
    return (
      <Login
        busy={busy}
        trouble={trouble}
        onLogin={enter}
        onRegister={() => {
          setTrouble(null);
          setScreen("register");
        }}
      />
    );
  }

  //: No body -- the identity is in the cloud (D-012). No in-person screen
  //: exists in this state at all: nobody to look at the location. The sidebar
  //: stays: account, orders and knowledge belong to the identity, not the body.
  if (look.body == null) {
    return (
      <ActionsProvider
        refresh={settle}
        session={session.current}
        book={book}
        names={names}
        locale={locale}
      >
      <main>
        <TopBar
          look={look}
          waiting={waiting}
          narrow={narrow}
          onSummary={() => setDigestShown(true)}
          onIntro={() => setIntro(true)}
          onRefresh={() => void refresh()}
        />
        <div className="frame">
          <Sidebar look={look} onLogout={() => void logout()} />
          <div className="main">
            {/* The alpha's widget stands here too (D-229). Twelve hours at the
                Forerunners' printer is the longest term in the world, and the
                one state the widget used to be hidden in was the one that had
                nothing to do but wait it out. */}
            {session.current.admin && <Alpha values={values} embodied={false} />}
            {/* No body, no place: the only thing to do here is print one. The
                sidebar stays -- the account, the orders and the knowledge
                belong to the identity, not to the body. */}
            <div className="panels">
              <Printer look={look} />
            </div>
          </div>
        </div>
        {trouble && <p className="trouble">{trouble}</p>}
      </main>
      </ActionsProvider>
    );
  }

  return (
    <ActionsProvider
      refresh={settle}
      session={session.current}
      book={book}
      names={names}
      locale={locale}
    >
    <main>
      <TopBar
        look={look}
        waiting={waiting}
        narrow={narrow}
        onSummary={() => setDigestShown(true)}
        onIntro={() => setIntro(true)}
        onRefresh={() => void refresh()}
        view={view}
        onView={setView}
      />

      <div className={`frame${narrow ? " one" : ""}`}>
        {(!narrow || where_ === "me") && (
          <Sidebar look={look} onLogout={() => void logout()} />
        )}

        {(!narrow || where_ !== "me") && (
          <div className="main">
            {/* The alpha's debug widget: printing things and finishing terms
                early (D-229). Outside the tabs and before them -- in-person
                tabs close on the road and in the field, and that is exactly
                the wait it is there to skip. The flag arrives once at the
                greeting; an ordinary player never gets it. */}
            {session.current.admin && <Alpha values={values} />}
            {((!narrow && (view === "map" || away)) ||
              (narrow && where_ === "map")) && (
              <GraphMap
                look={look}
                onEnter={() => {
                  setView("place");
                  setWhere_("here");
                }}
              />
            )}

            {!away &&
              ((!narrow && view === "place") || (narrow && where_ === "here")) && (
                <Stand
                  look={look}
                  values={values}
                  pow={pow}
                />
              )}

            {!away &&
              ((!narrow && view !== "map") || (narrow && where_ === "talk")) && (
                <Chat place={look.node?.key ?? ""} />
              )}

            {narrow && away && where_ !== "map" && (
              <section>
                <h2>{t("ui-app-away-title", { ongoing: String(ongoing) })}</h2>
                <p className="note">{t("ui-app-away-note", { ongoing: String(ongoing) })}</p>
              </section>
            )}
          </div>
        )}
      </div>

      {/* The four zones of the phone: the same zones as the desktop's, one at a
          time, and the bar is where a thumb reaches (brief section 9). */}
      {narrow && (
        <nav className="bottom" aria-label={t("ui-app-zones")}>
          {ZONES.map((zone) => (
            <button
              key={zone.id}
              className={where_ === zone.id ? "" : "quiet"}
              aria-pressed={where_ === zone.id}
              onClick={() => setWhere_(zone.id)}
            >
              {zone.label}
              {zone.id === "me" && waiting > 0 && (
                <span className="tally alarm">{waiting}</span>
              )}
            </button>
          ))}
        </nav>
      )}

      {card && <Profile name={card} onClose={() => setCard(null)} />}

      {digestShown && digest && (
        <Summary
          digest={digest}
          onClose={() => {
            //: Closing is the mark: what was read is not offered again, and the
            //: next summary counts from this moment.
            markSeen(digest.at);
            setDigestShown(false);
            void reread();
          }}
        />
      )}
      {intro && <Intro onClose={() => setIntro(false)} />}

      {trouble && <p className="trouble">{trouble}</p>}
    </main>
    </ActionsProvider>
  );
}

