/**
 * The OctoVerse alpha client.
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
import { Session, type Enrollment, type Look } from "./api";
import { Account } from "./panels/Account";
import { Admin } from "./panels/Admin";
import { Chat } from "./panels/Chat";
import { Circles } from "./panels/Circles";
import { Farm } from "./panels/Farm";
import { GraphMap } from "./panels/GraphMap";
import { Intro } from "./panels/Intro";
import { Kitchen } from "./panels/Kitchen";
import { Library } from "./panels/Library";
import { Login } from "./panels/Login";
import { Market } from "./panels/Market";
import { Mine } from "./panels/Mine";
import { Mint } from "./panels/Mint";
import { Nursery } from "./panels/Nursery";
import { Plant } from "./panels/Plant";
import { Printer } from "./panels/Printer";
import { Register } from "./panels/Register";
import { Rig } from "./panels/Rig";
import { Sidebar } from "./panels/Sidebar";
import { Place } from "./panels/Place";
import { Workshop } from "./panels/Workshop";
import { craftableAt } from "./recipes";
import { hands, stamp, worldTime } from "./clock";
import { powSettings, type PowSettings } from "./pow";

/** The terminal is the market building, everything else in the node is machines (D-090, D-100). */
const TERMINAL = "Терминал маркетплейса";

const VIEWS = [
  { id: "map", label: "карта" },
  { id: "place", label: "локация" },
  { id: "circles", label: "кружки" },
] as const;
type View = (typeof VIEWS)[number]["id"];

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
  const [trouble, setTrouble] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<View>("map");

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
    setBook(await api.recipes());
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

  const ongoing = Boolean(look?.travel);
  const asleep = Boolean(look?.body?.sleeping_since);
  //: Exploration is a body state (D-152): the scout left on their own, and
  //: while in the field, in-person is closed, as in sleep. Return -- by a button on the map.
  const exploring = Boolean(look?.survey);
  const away = ongoing || exploring;

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
      <main>
        <header>
          {who}
          <span>в облаке</span>
          <button className="quiet" onClick={() => void refresh()}>
            обновить
          </button>
        </header>
        <div className="frame">
          <Sidebar look={look} session={session.current} busy={busy} act={act} />
          <div className="main">
            <div className="panels">
              <Printer look={look} act={act} session={session.current} busy={busy} />
            </div>
          </div>
        </div>
        {accountWindow}
        {trouble && <p className="trouble">{trouble}</p>}
      </main>
    );
  }

  const stations = look.node?.stations ?? [];
  //: A panel for every machine that has something to do here. Manual craft
  //: went to the sidebar, the "craft" tab: rope is twisted where you stand,
  //: and no machine is needed for that. There is no common "workshop": the
  //: machine sets what a place is (D-106), and three machines in the yard are three different jobs.
  const busyMachines = stations.filter(
    (name) => craftableAt(book, name, look.knows).length > 0,
  );
  const has = {
    vein: Boolean(look.veins?.length),
    machines: busyMachines.length > 0,
    terminal: stations.includes(TERMINAL),
    library: Boolean(look.node?.library),
    farmland: (look.node?.fertility ?? 0) > 0,
    hearth: stations.includes("Очаг"),
    yard: stations.includes("Монетный станок"),
    nursery: stations.includes("Селекционный питомник"),
    //: A fuel station has its own window: it needs hauling, and hauling needs
    //: a hopper (D-189).
    plant: stations.includes("Угольная станция"),
    //: Authority is in-person: the administration is shown where it stands,
    //: not in the sidebar (D-155).
    townhall: Boolean(look.city?.hall),
  };
  //: Energy moved from locations to the sidebar, the "holdings" tab (D-149):
  //: the pool is shared per city, the household bill is money, not matter,
  //: and an "Energy" strip in every location said nothing about the location itself.
  const empty =
    !has.vein && !has.machines && !has.terminal && !has.library && !has.farmland &&
    !has.townhall;

  return (
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
      </header>

      <div className="frame">
        <Sidebar look={look} session={session.current} busy={busy} act={act} book={book} />

        <div className="main">
          {(view === "map" || away) && (
            <GraphMap
              look={look}
              session={session.current}
              busy={busy}
              act={act}
              onEnter={() => setView("place")}
            />
          )}

          {view === "place" && !away && (
            <>
              <div className="panels">
                {has.farmland && (
                  <Farm look={look} act={act} session={session.current} busy={busy} />
                )}
                {has.nursery && (
                  <Nursery look={look} act={act} session={session.current} busy={busy} />
                )}
                {has.vein && (
                  <Mine look={look} act={act} session={session.current} pow={pow} busy={busy} />
                )}
                {/* Буровая показывает себя сама: панель молчит, если в узле
                    нет ни установки, ни станка в руках (D-115). */}
                <Rig look={look} act={act} session={session.current} busy={busy} />
                {has.hearth && (
                  <Kitchen look={look} act={act} session={session.current} busy={busy} />
                )}
                {has.plant && (
                  <Plant look={look} act={act} session={session.current} busy={busy} />
                )}
                {busyMachines.map((name) => (
                  <Workshop
                    key={name}
                    machine={name}
                    book={book}
                    look={look}
                    act={act}
                    session={session.current}
                    busy={busy}
                  />
                ))}
                {/* Что здесь стоит и чьё это место. Станок из рук ставят
                    отсюда же: место для такой кнопки — участок, а не станок. */}
                <Place look={look} act={act} session={session.current} busy={busy} book={book} />
                {has.library && (
                  <Library look={look} act={act} session={session.current} busy={busy} />
                )}
                {has.townhall && (
                  <Admin look={look} act={act} session={session.current} busy={busy} />
                )}
                {has.yard && (
                  <Mint
                    look={look}
                    act={act}
                    session={session.current}
                    values={values}
                    busy={busy}
                  />
                )}
                {has.terminal && (
                  <Market
                    look={look}
                    act={act}
                    session={session.current}
                    values={values}
                    busy={busy}
                  />
                )}
                {empty && (
                  <section>
                    <h2>{look.node?.name}</h2>
                    <p className="note">
                      Здесь ничего не стоит — только дороги.
                    </p>
                  </section>
                )}
              </div>

              <Chat
                session={session.current}
                busy={busy}
                act={act}
                place={look.node?.key ?? ""}
              />
            </>
          )}

          {view === "circles" && !away && (
            <>
              <Circles
                session={session.current}
                busy={busy}
                act={act}
                place={look.node?.key ?? ""}
              />
              <Chat
                session={session.current}
                busy={busy}
                act={act}
                place={look.node?.key ?? ""}
              />
            </>
          )}
        </div>
      </div>

      {intro && <Intro onClose={() => setIntro(false)} />}
      {accountWindow}

      {trouble && <p className="trouble">{trouble}</p>}
      <footer>
        Альфа в разработке. Визуальный язык — отдельная работа дизайнера (D-049,
        D-055): здесь намеренно один шрифт и одна рамка.
      </footer>
    </main>
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
