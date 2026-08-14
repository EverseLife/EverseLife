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
import { Session, type Look } from "./api";
import { Admin } from "./panels/Admin";
import { Chat } from "./panels/Chat";
import { Circles } from "./panels/Circles";
import { Farm } from "./panels/Farm";
import { GraphMap } from "./panels/GraphMap";
import { Kitchen } from "./panels/Kitchen";
import { Library } from "./panels/Library";
import { Market } from "./panels/Market";
import { Mine } from "./panels/Mine";
import { Mint } from "./panels/Mint";
import { Nursery } from "./panels/Nursery";
import { Printer } from "./panels/Printer";
import { Rig } from "./panels/Rig";
import { Sidebar } from "./panels/Sidebar";
import { Place } from "./panels/Place";
import { Workshop } from "./panels/Workshop";
import { craftableAt } from "./recipes";
import { powSettings, type PowSettings } from "./pow";

const NAMES = ["Тэрн", "Хём"];
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
  const [name, setName] = useState(NAMES[0]);
  const [новичок, setНовичок] = useState("");
  const [trouble, setTrouble] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<View>("map");

  const refresh = useCallback(async () => {
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

  const enter = () =>
    act(async () => {
      const { values } = await api.constants();
      setValues(values);
      setPow(powSettings(values));
      setКнига(await api.recipes());
      await session.current.open(name);
    });

  /** Новый игрок: печатается у биопринтера с нулём на счету (D-153). */
  const join = () =>
    act(async () => {
      const { values } = await api.constants();
      setValues(values);
      setPow(powSettings(values));
      setКнига(await api.recipes());
      await session.current.create(новичок);
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

  if (!look) {
    return (
      <main className="entry">
        <h1>OctoVerse — альфа</h1>
        <p className="note">
          Стартовый мир: столица Терры с администрацией и свободными участками,
          угольная шахта и пойма за воротами. Кто дальше — решают игроки: карта
          прирастает разведкой, а не патчем.
        </p>
        {/* Имя вводится, а не выбирается из списка: игроки заводятся в игре, и
            выпадающий список из двух стартовых имён перестал бы работать с
            первым же новым персонажем. Подсказка — те самые двое из сида. */}
        <div className="row">
          <input
            list="жители"
            placeholder="имя"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <datalist id="жители">
            {NAMES.map((n) => (
              <option key={n} value={n} />
            ))}
          </datalist>
          <button onClick={enter} disabled={busy || !name.trim()}>
            Войти
          </button>
        </div>
        <div className="row">
          <input
            placeholder="новое имя"
            value={новичок}
            onChange={(e) => setНовичок(e.target.value)}
          />
          <button onClick={join} disabled={busy || !новичок.trim()}>
            Новый персонаж
          </button>
        </div>
        <p className="note">
          Новый персонаж печатается с нулём на счету; подъёмные платит город
          (D-153). Опознание по имени — заглушка разработки (D-027).
        </p>
        {trouble && <p className="trouble">{trouble}</p>}
      </main>
    );
  }

  //: Тела нет — личность в облаке (D-012). Присутственного экрана в этом
  //: положении не существует вовсе: смотреть на локацию некому. Сайдбар при
  //: этом остаётся: счёт, ордера и знания принадлежат личности, а не телу.
  if (look.body === null) {
    return (
      <main>
        <header>
          <span className="who">{look.identity}</span>
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
        <span className="who">{look.identity}</span>
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

      {trouble && <p className="trouble">{trouble}</p>}
      <footer>
        Альфа в разработке. Визуальный язык — отдельная работа дизайнера (D-049,
        D-055): здесь намеренно один шрифт и одна рамка.
      </footer>
    </main>
  );
}
