/**
 * Альфа-клиент OctoVerse.
 *
 * Компоновка — четыре постоянные зоны (D-050):
 *
 * - **верхний баннер** — где ты, что с телом, счёт;
 * - **левый сайдбар** — то, что работает через Сеть: персонаж, инвентарь,
 *   дела, торговля, знания, хозяйство. Доступен всегда, хоть с дороги: счета
 *   за быт — деньги, а не материя (D-149). Управления городом здесь нет:
 *   власть присутственна и живёт в администрации (D-155);
 * - **основное окно** — табы: карта · локация · кружки. Локация и кружки
 *   присутственные: в пути их нет, потому что нет тебя в узле;
 * - **нижняя полоса** — живой чат локации.
 *
 * Организующий принцип тот же, что у мира: сайдбар — удалённое, основное окно
 * — присутственное. Игрок усваивает устройство мира, просто пользуясь
 * интерфейсом. Визуальный язык — всё ещё работа дизайнера (D-049, D-055).
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
import { Printer } from "./panels/Printer";
import { Register } from "./panels/Register";
import { Rig } from "./panels/Rig";
import { Sidebar } from "./panels/Sidebar";
import { Place } from "./panels/Place";
import { Workshop } from "./panels/Workshop";
import { craftableAt } from "./recipes";
import { powSettings, type PowSettings } from "./pow";

/** Терминал — постройка рынка, всё прочее в узле это станки (D-090, D-100). */
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
  //: Справочник вольта нужен сразу нескольким панелям станков: грузим один раз.
  const [книга, setКнига] = useState<any>(null);
  const [pow, setPow] = useState<PowSettings | null>(null);
  //: Экран до входа: логин либо регистрация (D-187). Жетон прошлого входа
  //: пробуется молча: пока он проверяется, экран входа не мигает.
  const [screen, setScreen] = useState<"login" | "register">("login");
  const [resuming, setResuming] = useState(() => Boolean(Session.remembered()));
  const resumed = useRef(false);
  const [кабинет, setКабинет] = useState(false);
  const [вступление, setВступление] = useState(false);
  const [trouble, setTrouble] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<View>("map");

  const refresh = useCallback(async () => {
    //: Пока личность не названа, обновлять нечего: сессии ещё нет. Иначе
    //: первый же шаг входа — чтение дверей — упирался бы в «нет сессии».
    if (!session.current.name) return;
    setLook(await session.current.look());
  }, []);

  /** Любое действие идёт через это: одна ошибка — одна строка внизу экрана. */
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

  /** Справочники вольта: их ждут и панели станков, и прогноз качества. */
  const справочники = useCallback(async () => {
    const { values } = await api.constants();
    setValues(values);
    setPow(powSettings(values));
    setКнига(await api.recipes());
  }, []);

  const enter = (email: string, password: string) =>
    act(async () => {
      await справочники();
      await session.current.open(email, password);
    });

  //: Автовход жетоном (D-187): F5 не спрашивает пароль. Отказ — молча на экран
  //: входа: истёкший жетон — не ошибка пользователя.
  useEffect(() => {
    const token = Session.remembered();
    //: Один подъём на страницу: StrictMode в разработке зовёт эффект дважды,
    //: а два сокета с одним жетоном — это гонка, а не вход.
    if (!token || resumed.current) return;
    resumed.current = true;
    void (async () => {
      try {
        await справочники();
        await session.current.resume(token);
        await refresh();
      } catch {
        /* жетон истёк или отозван — обычный вход */
      } finally {
        setResuming(false);
      }
    })();
  }, [справочники, refresh]);

  /** Регистрация: четыре шага клиента — одна команда сервера (D-187). Печать у
   *  выбранной двери: ноль на счету, подъёмные — дело города (D-153). Следом
   *  слово Предтеч: объяснить, кто он, больше некому (D-182). */
  const join = (заявка: Enrollment) =>
    act(async () => {
      await справочники();
      await session.current.create(заявка);
      setВступление(true);
    });

  /** Выход: жетон отозван, экран входа. */
  const logout = () =>
    act(async () => {
      await session.current.logout();
      setКабинет(false);
      setLook(null);
      setScreen("login");
    });

  const идёт = Boolean(look?.travel);
  const спит = Boolean(look?.body?.sleeping_since);
  //: Разведка — состояние тела (D-152): разведчик ушёл сам, и пока он в поле,
  //: присутственное закрыто, как во сне. Вернуться — кнопкой на карте.
  const в_разведке = Boolean(look?.survey);
  const отлучился = идёт || в_разведке;

  useEffect(() => {
    if (!look) return;
    //: В пути опрашиваем чаще: приход должен быть виден сразу.
    const timer = setInterval(() => void refresh().catch(() => {}), идёт ? 2000 : 5000);
    return () => clearInterval(timer);
  }, [look, идёт, refresh]);

  //: Вышел в дорогу или в разведку — присутственные табы закрываются сами:
  //: тебя в узле нет.
  useEffect(() => {
    if (отлучился) setView("map");
  }, [отлучился]);

  //: Жетон прошлого входа проверяется — экран входа не мигает (D-187).
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

  //: Кабинет аккаунта — на месте голого имени в шапке (D-187).
  const кто = (
    <button
      className="who"
      onClick={() => setКабинет(true)}
      title="аккаунт: персонаж, пароль, выход"
    >
      {look.identity}
      {look.profile?.surname ? ` ${look.profile.surname}` : ""}
    </button>
  );
  const окно_кабинета = кабинет && look.profile && (
    <Account
      key={look.profile.email ?? look.identity}
      profile={look.profile}
      session={session.current}
      busy={busy}
      act={act}
      onClose={() => setКабинет(false)}
      onLogout={logout}
    />
  );

  //: Тела нет — личность в облаке (D-012). Присутственного экрана в этом
  //: положении не существует вовсе: смотреть на локацию некому. Сайдбар при
  //: этом остаётся: счёт, ордера и знания принадлежат личности, а не телу.
  if (look.body === null) {
    return (
      <main>
        <header>
          {кто}
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
        {окно_кабинета}
        {trouble && <p className="trouble">{trouble}</p>}
      </main>
    );
  }

  const станции = look.node?.stations ?? [];
  //: Панель на каждый станок, у которого здесь есть что делать. Ручной крафт
  //: ушёл в сайдбар, во вкладку «крафт»: верёвку вьют где стоят, и станок
  //: этому не нужен. Общей «мастерской» нет: станок задаёт, чем место
  //: является (D-106), и три станка во дворе — это три разных дела.
  const станки_с_делом = станции.filter(
    (имя) => craftableAt(книга, имя, look.knows).length > 0,
  );
  const есть = {
    жила: Boolean(look.veins?.length),
    станки: станки_с_делом.length > 0,
    терминал: станции.includes(TERMINAL),
    библиотека: Boolean(look.node?.library),
    пашня: (look.node?.fertility ?? 0) > 0,
    очаг: станции.includes("Очаг"),
    двор: станции.includes("Монетный станок"),
    питомник: станции.includes("Селекционный питомник"),
    //: Власть присутственна: администрация показывается там, где она стоит,
    //: а не в сайдбаре (D-155).
    ратуша: Boolean(look.city?.hall),
  };
  //: Энергия из локаций ушла в сайдбар, во вкладку «хозяйство» (D-149): пул
  //: общий на город, счёт за быт — деньги, а не материя, и плашка «Энергия»
  //: в каждой локации не сообщала ничего о самой локации.
  const пусто =
    !есть.жила && !есть.станки && !есть.терминал && !есть.библиотека && !есть.пашня &&
    !есть.ратуша;

  return (
    <main>
      <header>
        {кто}
        <span>
          {идёт
            ? `в пути: ${look.travel!.final ?? look.travel!.to}`
            : в_разведке
              ? `в разведке от: ${look.node?.name}`
              : look.node?.name}
          {спит ? " · спит" : ""}
        </span>
        <nav className="row tabs">
          {VIEWS.map((option) => (
            <button
              key={option.id}
              className={view === option.id ? "" : "quiet"}
              onClick={() => setView(option.id)}
              //: Присутственные табы в пути и в разведке недоступны — тебя
              //: нет в узле (D-107, D-152).
              disabled={option.id !== "map" && отлучился}
            >
              {option.label}
            </button>
          ))}
        </nav>
        {/* Вступление под рукой всегда: прочитанное однажды не должно
            становиться недоступным, а непрочитанное — обязательным (D-182). */}
        <button
          className="quiet"
          onClick={() => setВступление(true)}
          title="кто вы и с чего начать"
        >
          ?
        </button>
        <button className="quiet" onClick={() => void refresh()}>
          обновить
        </button>
      </header>

      <div className="frame">
        <Sidebar look={look} session={session.current} busy={busy} act={act} книга={книга} />

        <div className="main">
          {(view === "map" || отлучился) && (
            <GraphMap
              look={look}
              session={session.current}
              busy={busy}
              act={act}
              onEnter={() => setView("place")}
            />
          )}

          {view === "place" && !отлучился && (
            <>
              <div className="panels">
                {есть.пашня && (
                  <Farm look={look} act={act} session={session.current} busy={busy} />
                )}
                {есть.питомник && (
                  <Nursery look={look} act={act} session={session.current} busy={busy} />
                )}
                {есть.жила && (
                  <Mine look={look} act={act} session={session.current} pow={pow} busy={busy} />
                )}
                {/* Буровая показывает себя сама: панель молчит, если в узле
                    нет ни установки, ни станка в руках (D-115). */}
                <Rig look={look} act={act} session={session.current} busy={busy} />
                {есть.очаг && (
                  <Kitchen look={look} act={act} session={session.current} busy={busy} />
                )}
                {станки_с_делом.map((имя) => (
                  <Workshop
                    key={имя}
                    станок={имя}
                    книга={книга}
                    look={look}
                    act={act}
                    session={session.current}
                    busy={busy}
                  />
                ))}
                {/* Что здесь стоит и чьё это место. Станок из рук ставят
                    отсюда же: место для такой кнопки — участок, а не станок. */}
                <Place look={look} act={act} session={session.current} busy={busy} книга={книга} />
                {есть.библиотека && (
                  <Library look={look} act={act} session={session.current} busy={busy} />
                )}
                {есть.ратуша && (
                  <Admin look={look} act={act} session={session.current} busy={busy} />
                )}
                {есть.двор && (
                  <Mint
                    look={look}
                    act={act}
                    session={session.current}
                    values={values}
                    busy={busy}
                  />
                )}
                {есть.терминал && (
                  <Market
                    look={look}
                    act={act}
                    session={session.current}
                    values={values}
                    busy={busy}
                  />
                )}
                {пусто && (
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

          {view === "circles" && !отлучился && (
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

      {вступление && <Intro onClose={() => setВступление(false)} />}
      {окно_кабинета}

      {trouble && <p className="trouble">{trouble}</p>}
      <footer>
        Альфа в разработке. Визуальный язык — отдельная работа дизайнера (D-049,
        D-055): здесь намеренно один шрифт и одна рамка.
      </footer>
    </main>
  );
}
