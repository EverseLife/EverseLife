// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * The Everse.Life alpha client.
 *
 * The layout is four permanent zones (D-050):
 *
 * - **top banner** -- where you are, what is with the body, the account;
 * - **left sidebar** -- what works over the Net: character, inventory, jobs,
 *   trade, knowledge, holdings. Always available, even from the road:
 *   household bills are money, not matter (D-149). No city governing here:
 *   authority is in-person and lives in the administration (D-155);
 * - **main window** -- tabs: map - location - circles. Location and circles
 *   are in-person: en route they are gone, because you are not in the node;
 * - **bottom strip** -- the location's live chat.
 *
 * The organising principle is the same as the world's: the sidebar is remote,
 * the main window is in-person. The player absorbs the world's structure just
 * by using the interface. The visual language is still the designer's work (D-049, D-055).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "./api";
import * as amounts from "./amounts";
import { Session, type Enrollment, type Look } from "./api";
import { Account } from "./panels/Account";
import { Chat } from "./panels/Chat";
import { Circles } from "./panels/Circles";
import { GraphMap } from "./panels/GraphMap";
import { Intro } from "./panels/Intro";
import { Login } from "./panels/Login";
import { Printer } from "./panels/Printer";
import { Register } from "./panels/Register";
import { Sidebar } from "./panels/Sidebar";
import { Summary, markSeen, useDigest } from "./panels/Summary";
import { Stand } from "./panels/Stand";
import { hands, stamp, worldTime } from "./clock";
import { powSettings, type PowSettings } from "./pow";
import { wearPlanet } from "./theme";
import { useNarrow } from "./narrow";
import { ActionsProvider } from "./actions";


const VIEWS = [
  { id: "map", label: "карта" },
  { id: "place", label: "локация" },
  { id: "circles", label: "кружки" },
] as const;
type View = (typeof VIEWS)[number]["id"];

/** The phone's four sections: the same zones, one at a time (brief section 9). */
const ZONES = [
  { id: "me", label: "я" },
  { id: "here", label: "здесь" },
  { id: "map", label: "карта" },
  { id: "talk", label: "чат" },
] as const;
type Zone = (typeof ZONES)[number]["id"];

export default function App() {
  const session = useRef(new Session());
  const [look, setLook] = useState<Look | null>(null);
  const [values, setValues] = useState<Record<string, any> | null>(null);
  //: The vault catalog is needed by several machine panels at once: we load it once.
  const [book, setBook] = useState<any>(null);
  const [pow, setPow] = useState<PowSettings | null>(null);
  //: The screen before login: login or registration (D-187). The last login's
  //: token is tried silently: while it is checked, the login screen does not flicker.
  const [screen, setScreen] = useState<"login" | "register">("login");
  const [resuming, setResuming] = useState(() => Boolean(Session.remembered()));
  const resumed = useRef(false);
  const [account_, setAccount_] = useState(false);
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

  const refresh = useCallback(async () => {
    //: While the identity is not named there is nothing to refresh: no session
    //: yet. Otherwise the very first login step -- reading doors -- would hit "no session".
    if (!session.current.name) return;
    setLook(await session.current.look());
  }, []);

  /** Every action goes through this: one error -- one line at the bottom of the screen. */
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

  /** The vault catalogs: both machine panels and the quality forecast wait for them. */
  const catalogs = useCallback(async () => {
    const { values } = await api.constants();
    setValues(values);
    setPow(powSettings(values));
    //: The constants ride along with the catalog: a machine panel that needs
    //: one number (how many kinds go into an attempt, D-209) reads it from the
    //: same book it reads recipes from, without a second prop through every layer.
    const book = await api.recipes();
    //: Piece or weight is read off the same book (D-212), and every panel that
    //: draws a quantity asks `amounts`, not a prop of its own.
    amounts.learn(book);
    setBook({ ...book, constants: values });
  }, []);

  const enter = (email: string, password: string) =>
    act(async () => {
      await catalogs();
      await session.current.open(email, password);
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
        await refresh();
      } catch {
        /* жетон истёк или отозван — обычный вход */
      } finally {
        setResuming(false);
      }
    })();
  }, [catalogs, refresh]);

  /** Registration: four client steps -- one server command (D-187). Printing
   *  at the chosen door: zero on the account, the grant is the city's business
   *  (D-153). Then the Forerunner's word: nobody else can explain who he is (D-182). */
  const join = (application: Enrollment) =>
    act(async () => {
      await catalogs();
      await session.current.create(application);
      setIntro(true);
    });

  /** Logout: the token is revoked, the login screen. */
  const logout = () =>
    act(async () => {
      await session.current.logout();
      setAccount_(false);
      setLook(null);
      setScreen("login");
    });

  const { digest, reread } = useDigest(session.current, Boolean(look));
  const waiting = digest?.attention.length ?? 0;

  const ongoing = Boolean(look?.travel);
  const asleep = Boolean(look?.body?.sleeping_since);
  //: Exploration is a body state (D-152): the scout left on their own, and
  //: while in the field, in-person is closed, as in sleep. Return -- by a button on the map.
  const exploring = Boolean(look?.survey);
  const away = ongoing || exploring;

  //: The base tone belongs to the planet you stand on (D-074, D-080): arriving
  //: repaints the screen, and that is the fair price of a week through the
  //: void. Before login nobody stands anywhere -- the theme stays the default.
  useEffect(() => {
    if (!look) return;
    //: "борт" is a node property, like "лес" or "камни": the vault sets it in
    //: data, and the client only reads it.
    wearPlanet(look.clock?.planet ?? null, (look.node?.features ?? []).includes("борт"));
  }, [look]);

  useEffect(() => {
    if (!look) return;
    //: En route we poll more often: the arrival must be seen at once.
    const timer = setInterval(() => void refresh().catch(() => {}), ongoing ? 2000 : 5000);
    return () => clearInterval(timer);
  }, [look, ongoing, refresh]);

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

  //: The account panel -- in place of the bare name in the header (D-187).
  const who = (
    <button
      className="who"
      onClick={() => setAccount_(true)}
      title="аккаунт: персонаж, пароль, выход"
    >
      {look.identity}
      {look.profile?.surname ? ` ${look.profile.surname}` : ""}
    </button>
  );
  const accountWindow = account_ && look.profile && (
    <Account
      key={look.profile.email ?? look.identity}
      profile={look.profile}
      session={session.current}
      busy={busy}
      act={act}
      onClose={() => setAccount_(false)}
      onLogout={logout}
    />
  );

  //: No body -- the identity is in the cloud (D-012). No in-person screen
  //: exists in this state at all: nobody to look at the location. The sidebar
  //: stays: account, orders and knowledge belong to the identity, not the body.
  if (look.body === null) {
    return (
      <ActionsProvider refresh={refresh}>
      <main>
        <header>
          {who}
          <span>в облаке</span>
          <button className="quiet" onClick={() => void refresh()}>
            обновить
          </button>
        </header>
        <div className="frame">
          <Sidebar look={look} session={session.current} book={book} />
          <div className="main">
            {/* No body, no place: the only thing to do here is print one. The
                sidebar stays -- the account, the orders and the knowledge
                belong to the identity, not to the body. */}
            <div className="panels">
              <Printer look={look} session={session.current} />
            </div>
          </div>
        </div>
        {accountWindow}
        {trouble && <p className="trouble">{trouble}</p>}
      </main>
      </ActionsProvider>
    );
  }

  return (
    <ActionsProvider refresh={refresh}>
    <main>
      <header>
        {who}
        <span>
          {ongoing
            ? `в пути: ${look.travel!.final ?? look.travel!.to}`
            : exploring
              ? `в разведке от: ${look.node?.name}`
              : look.node?.name}
          {asleep ? " · спит" : ""}
        </span>
        {/* Local time of the planet: its day is 38 hours and matches nobody's
            wall clock on purpose (D-029). */}
        {look.clock && <WorldClock clock={look.clock} />}
        {/* On a phone the bar at the bottom chooses the zone, and a second set
            of the same choices in the header would only take the row. */}
        {!narrow && (
          <nav className="row tabs">
            {VIEWS.map((option) => (
              <button
                key={option.id}
                className={view === option.id ? "" : "quiet"}
                onClick={() => setView(option.id)}
                //: In-person tabs are unavailable en route and while exploring --
                //: you are not in the node (D-107, D-152).
                disabled={option.id !== "map" && away}
              >
                {option.label}
              </button>
            ))}
          </nav>
        )}
        <button
          className="quiet"
          onClick={() => setDigestShown(true)}
          title="что произошло, пока вас не было"
        >
          сводка
          {waiting > 0 && <span className="tally alarm">{waiting}</span>}
        </button>
        {/* Вступление под рукой всегда: прочитанное однажды не должно
            становиться недоступным, а непрочитанное — обязательным (D-182). */}
        <button
          className="quiet"
          onClick={() => setIntro(true)}
          title="кто вы и с чего начать"
        >
          ?
        </button>
        <button className="quiet" onClick={() => void refresh()}>
          обновить
        </button>
        {/* Исходники этой версии. Требование §13 AGPL: тому, кто играет по
            сети, их надо предложить, а не спрятать в README. Машиночитаемый
            ответ на тот же вопрос -- `/public/source`. */}
        <a
          className="quiet"
          href="https://github.com/EverseLife/EverseLife"
          target="_blank"
          rel="noopener noreferrer"
          title="исходный код этой версии"
        >
          исходники
        </a>
      </header>

      <div className={`frame${narrow ? " one" : ""}`}>
        {(!narrow || where_ === "me") && (
          <Sidebar look={look} session={session.current} book={book} />
        )}

        {(!narrow || where_ !== "me") && (
          <div className="main">
            {((!narrow && (view === "map" || away)) ||
              (narrow && where_ === "map")) && (
              <GraphMap
                look={look}
                session={session.current}
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
                  session={session.current}
                  book={book}
                  values={values}
                  pow={pow}
                />
              )}

            {/* People nearby: the groups and the talk. On a wide screen the
                circles have a tab of their own; on a phone they share the
                section, because both answer "who is here". */}
            {!away &&
              ((!narrow && view === "circles") || (narrow && where_ === "talk")) && (
                <Circles session={session.current} place={look.node?.key ?? ""} />
              )}

            {!away &&
              ((!narrow && view !== "map") || (narrow && where_ === "talk")) && (
                <Chat session={session.current} place={look.node?.key ?? ""} />
              )}

            {narrow && away && where_ !== "map" && (
              <section>
                <h2>{ongoing ? "В пути" : "В разведке"}</h2>
                <p className="note">
                  {ongoing
                    ? "Пока идёшь, тебя нет нигде: присутственное закрыто."
                    : "Разведчик в поле: тело недоступно, как во сне."}
                </p>
              </section>
            )}
          </div>
        )}
      </div>

      {/* The four zones of the phone: the same zones as the desktop's, one at a
          time, and the bar is where a thumb reaches (brief section 9). */}
      {narrow && (
        <nav className="bottom" aria-label="разделы">
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
      {accountWindow}

      {trouble && <p className="trouble">{trouble}</p>}
    </main>
    </ActionsProvider>
  );
}

/** Local clock of the planet in the header (D-029).
 *
 * A Terran day is 38 hours, so the hands drift against the player's own clock
 * -- that drift is the point: the world lives by its own time, not by the
 * time zone of whoever is looking.
 */
function WorldClock({ clock }: { clock: NonNullable<Look["clock"]> }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    //: A world minute is a real minute: half-minute ticking is enough for the
    //: hands never to lag behind by a visible amount.
    const timer = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(timer);
  }, []);
  return (
    <span className="clock" title={`местное время: ${stamp(clock, now)}`}>
      {hands(clock, now)}
      <span className="note"> · сутки {worldTime(clock, now).day}</span>
    </span>
  );
}
