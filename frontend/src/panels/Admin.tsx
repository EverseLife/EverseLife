/**
 * Администрация: должности, права, законы, устав, казна, панель (D-154, D-155).
 *
 * Панель стоит **в локации, а не в сайдбаре**: власть присутственна. Решение
 * города принимается там, где стоит администрация, — иначе здание становится
 * декорацией, а захват власти вопросом одного нажатия, а не географии (D-155).
 *
 * Право здесь не выбирается из четырёх крупных. Оно собирается: «министр
 * экономики» — это `law:import_duty`, `law:export_duty` и `dashboard`, и
 * название ему придумывает город, а не движок. Список законов приходит с
 * сервера из вольта, поэтому новый код-закон появится в этом списке сам.
 *
 * Экономическая панель читается **удалённо** (D-140) и потому показывается
 * всем, кто зашёл: цифры — общее знание, спорить с властью без них нечем.
 * Казна по статьям — только тем, у кого есть право `dashboard`.
 */

import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import type {
  CityPanel,
  CityView,
  CityVote,
  CourtCase,
  Look,
  SanctionKind,
  Session,
} from "../api";

type Props = {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

export function Admin({ look, session, busy, act }: Props) {
  const [город, setГород] = useState<CityView | null>(null);
  const [панель, setПанель] = useState<CityPanel | null>(null);
  const [голосования, setГолосования] = useState<CityVote[]>([]);
  const [дела, setДела] = useState<CourtCase[]>([]);
  const [санкции, setСанкции] = useState<SanctionKind[]>([]);
  const [каторги, setКаторги] = useState<{ key: string; name: string }[]>([]);
  const [правка, setПравка] = useState<Record<string, string>>({});
  const [кому, setКому] = useState("");
  const [должность, setДолжность] = useState("Министр экономики");
  const [права, setПрава] = useState<string[]>(["dashboard"]);
  const [сумма, setСумма] = useState(0);
  const [участок, setУчасток] = useState("");
  const [вид, setВид] = useState<"власть" | "панель">("власть");

  const reload = useCallback(async () => {
    try {
      const сводка = await session.send("city.survey");
      setГород((сводка.city as CityView) ?? null);
      const срез = await session.send("city.panel");
      setПанель((срез.panel as CityPanel) ?? null);
      //: Голосования идут своим сроком и без игроков: их показывают всем, а
      //: не только власти — на то они и голосования (D-161).
      const созывы = await session.send("city.votes");
      setГолосования((созывы.votes as CityVote[]) ?? []);
      const суд = await session.send("city.cases");
      setДела((суд.cases as CourtCase[]) ?? []);
      setСанкции((суд.sanctions as SanctionKind[]) ?? []);
      setКаторги((суд.prisons as { key: string; name: string }[]) ?? []);
    } catch {
      setГород(null);
      setПанель(null);
    }
  }, [session]);

  useEffect(() => {
    void reload();
  }, [reload, look.node?.key]);

  const го = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await reload();
    });

  if (!город) {
    return (
      <section>
        <h2>Администрация</h2>
        <p className="note">Здесь нет города: за стенами законов нет.</p>
      </section>
    );
  }

  const может = (право: string) =>
    город.powers.includes(право) ||
    (право.startsWith(api.LAW_SCOPE) && город.powers.includes("laws"));
  const решает = город.at_hall;
  const жители = город.citizens.filter((имя) => имя !== look.identity);
  const свободные = город.lots.filter((лот) => лот.free);

  return (
    <section>
      <h2>Администрация · {город.name}</h2>
      <nav className="row tabs">
        {(["власть", "панель"] as const).map((имя) => (
          <button
            key={имя}
            className={вид === имя ? "" : "quiet"}
            onClick={() => setВид(имя)}
          >
            {имя}
          </button>
        ))}
      </nav>

      {вид === "панель" ? (
        <Panel панель={панель} />
      ) : (
        <>
        <Court дела={дела} санкции={санкции} каторги={каторги} может={может("justice")} session={session} go={го} busy={busy} />
        <Votes
          голосования={голосования}
          город={город}
          session={session}
          go={го}
          busy={busy}
        />
        <>
          <p className="sign">казна {api.tk(город.treasury)} ₭</p>
          <p className="note">
            {город.powers.length === 0
              ? "Вы здесь житель: законы видны, правят их должностные лица."
              : `Ваши права: ${город.powers.map(имяПрава(город)).join(", ")}.`}
            {!решает && город.powers.length > 0 && (
              <b> Решения принимаются в администрации — придите в неё.</b>
            )}
          </p>

          <Word
            город={город}
            может={может("citizens") && решает}
            session={session}
            go={го}
            busy={busy}
          />

          <h3>Должности</h3>
          {город.offices.length === 0 ? (
            <p className="note">должностей нет</p>
          ) : (
            город.offices.map((пост) => (
              <div className="row" key={пост.id}>
                <span>
                  <b>{пост.title}</b> · {пост.who}
                  <span className="note">
                    {" "}
                    · {пост.powers.map(имяПрава(город)).join(", ")}
                  </span>
                </span>
                {может("offices") && решает && пост.who !== look.identity && (
                  <button
                    className="quiet"
                    onClick={() => го(() => session.send("city.revoke", { office: пост.id }))}
                    disabled={busy}
                  >
                    Снять
                  </button>
                )}
              </div>
            ))
          )}

          {может("offices") && решает && жители.length > 0 && (
            <>
              <h3>Создать должность</h3>
              <div className="row">
                <select value={кому} onChange={(e) => setКому(e.target.value)}>
                  <option value="">кого назначить</option>
                  {жители.map((имя) => (
                    <option key={имя}>{имя}</option>
                  ))}
                </select>
                <input
                  value={должность}
                  onChange={(e) => setДолжность(e.target.value)}
                  title="название придумывает город, движок смотрит в права"
                />
                <button
                  onClick={() =>
                    го(() =>
                      session.send("city.appoint", {
                        whom: кому,
                        title: должность,
                        powers: права,
                      }),
                    )
                  }
                  disabled={busy || !кому || права.length === 0}
                >
                  Назначить
                </button>
              </div>
              <Scopes
                город={город}
                выбрано={права}
                setВыбрано={setПрава}
                может={может}
              />
            </>
          )}

          <h3>Код-законы</h3>
          <table>
            <tbody>
              {Object.entries(город.laws).map(([ключ, закон]) => {
                const правлю = может(api.LAW_SCOPE + ключ) && решает;
                return (
                  <tr key={ключ}>
                    <td title={закон.note ?? ""}>
                      {закон.name}
                      {закон.unit && <span className="note"> · {закон.unit}</span>}
                    </td>
                    <td className="num">
                      {правлю ? (
                        <input
                          value={правка[ключ] ?? закон.value ?? ""}
                          onChange={(e) =>
                            setПравка((было) => ({ ...было, [ключ]: e.target.value }))
                          }
                          size={10}
                        />
                      ) : (
                        <b>{закон.value ?? "—"}</b>
                      )}
                    </td>
                    <td className="note">{закон.own ? "решение города" : "умолчание"}</td>
                    <td>
                      {правлю && (правка[ключ] ?? "") !== "" && (
                        <button
                          onClick={() =>
                            го(() =>
                              session.send("city.law", { law: ключ, value: правка[ключ] }),
                            )
                          }
                          disabled={busy}
                        >
                          Принять
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="note">
            Право на закон точечное: «министр экономики» правит пошлины и не
            трогает налог. Отдать можно только то, что есть у себя (D-155).
          </p>

          {(["import_duty", "export_duty"] as const)
            .filter((ключ) => может(api.LAW_SCOPE + ключ) && решает)
            .map((ключ) => (
              <Customs
                key={ключ}
                закон={ключ}
                имя={город.laws[ключ]?.name ?? ключ}
                значение={город.laws[ключ]?.value ?? null}
                товары={Object.keys(панель?.goods ?? {})}
                busy={busy}
                применить={(значение) =>
                  го(() => session.send("city.law", { law: ключ, value: значение }))
                }
              />
            ))}

          {может("land") && решает && свободные.length > 0 && жители.length > 0 && (
            <>
              <h3>Свободные участки</h3>
              <div className="row">
                <select value={участок} onChange={(e) => setУчасток(e.target.value)}>
                  <option value="">какой участок</option>
                  {свободные.map((лот) => (
                    <option key={лот.key} value={лот.key}>
                      {лот.name} · {лот.area.toFixed(0)} м²
                    </option>
                  ))}
                </select>
                <select value={кому} onChange={(e) => setКому(e.target.value)}>
                  <option value="">кому</option>
                  {жители.map((имя) => (
                    <option key={имя}>{имя}</option>
                  ))}
                </select>
                <button
                  onClick={() =>
                    го(() => session.send("city.allot", { node: участок, whom: кому }))
                  }
                  disabled={busy || !участок || !кому}
                >
                  Выделить
                </button>
              </div>
            </>
          )}

          {может("treasury") && решает && жители.length > 0 && (
            <>
              <h3>Казна</h3>
              <div className="row">
                <select value={кому} onChange={(e) => setКому(e.target.value)}>
                  <option value="">кому</option>
                  {жители.map((имя) => (
                    <option key={имя}>{имя}</option>
                  ))}
                </select>
                <input
                  type="number"
                  min={0}
                  value={сумма}
                  onChange={(e) => setСумма(Number(e.target.value))}
                />
                <button
                  onClick={() =>
                    го(() =>
                      session.send("city.spend", {
                        whom: кому,
                        amount: api.minor(сумма),
                        memo: "выплата",
                      }),
                    )
                  }
                  disabled={busy || !кому || сумма <= 0}
                >
                  Заплатить ₭
                </button>
              </div>
            </>
          )}

          <h3>Устав</h3>
          <table>
            <tbody>
              {(город.charter_questions ?? []).map((вопрос) => {
                const ответ = город.charter[вопрос.id];
                const вариант = вопрос.options.find((о) => о.id === ответ);
                return (
                  <tr key={вопрос.id}>
                    <td className="note">
                      {вопрос.section} · {вопрос.question}
                    </td>
                    <td>
                      {может("charter") && решает ? (
                        <select
                          value={ответ ?? ""}
                          onChange={(e) =>
                            го(() =>
                              session.send("city.charter", {
                                question: вопрос.id,
                                option: e.target.value,
                              }),
                            )
                          }
                          disabled={busy}
                        >
                          {вопрос.options.map((о) => (
                            <option key={о.id} value={о.id}>
                              {о.label}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <b>{вариант?.label ?? ответ ?? "—"}</b>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="note">
            Устав решает, кто утверждает закон: «правитель единолично» меняет
            его сразу, «голосованием граждан» — созывает голосование (D-161).
            Выборы правителя и совет приедут своей механикой.
          </p>
        </>
        </>
      )}
    </section>
  );
}

/** Слово города новичку — то, что стоит на карточке двери (D-183).
 *
 * Правит его тот, кто принимает в граждане: объявление это вербовка. Движок
 * написанное не исполняет — обещание здесь обязывает людей, а не код. */
function Word({
  город,
  может,
  session,
  go,
  busy,
}: {
  город: CityView;
  может: boolean;
  session: Session;
  go: (what: () => Promise<unknown>) => void;
  busy: boolean;
}) {
  const [текст, setТекст] = useState<string | null>(null);
  const набрано = текст ?? город.about;

  return (
    <div>
      <h3>Слово городу</h3>
      {город.about ? (
        <p className="say">«{город.about}»</p>
      ) : (
        <p className="note">город молчит: новичок видит одни числа</p>
      )}
      {может && (
        <>
          <div className="row">
            <textarea
              className="word"
              value={набрано}
              maxLength={api.CITY_ABOUT_LIMIT}
              placeholder="чем город зовёт новичка"
              onChange={(e) => setТекст(e.target.value)}
            />
          </div>
          <div className="row">
            <button
              onClick={() =>
                go(async () => {
                  await session.send("city.about", { text: набрано });
                  setТекст(null);
                })
              }
              disabled={busy || набрано === город.about}
            >
              Объявить
            </button>
            <span className="note">
              {набрано.length} из {api.CITY_ABOUT_LIMIT} знаков · видно всем,
              кто выбирает, где напечататься
            </span>
          </div>
        </>
      )}
    </div>
  );
}

/** Пошлина: товар, ставка и беспошлинная норма (D-123).
 *
 * Ставка без нормы бьёт по всем одинаково, и первым страдает житель с мешком
 * репы. Поэтому строка тут всегда из трёх частей, а не из одной. */
function Customs({
  закон,
  имя,
  значение,
  товары,
  busy,
  применить,
}: {
  закон: string;
  имя: string;
  значение: string | null;
  товары: string[];
  busy: boolean;
  применить: (значение: unknown) => void;
}) {
  const разобрано = разобрать(значение);
  const [товар, setТовар] = useState("");
  const [ставка, setСтавка] = useState(10);
  const [норма, setНорма] = useState(30);

  const добавить = () =>
    применить({ ...разобрано, [товар]: { rate: ставка, free: норма } });
  const убрать = (какой: string) => {
    const без = { ...разобрано };
    delete без[какой];
    применить(без);
  };

  return (
    <div>
      <h3>{имя}</h3>
      {Object.keys(разобрано).length === 0 ? (
        <p className="note">граница открыта: ставок нет</p>
      ) : (
        <table>
          <tbody>
            {Object.entries(разобрано).map(([какой, условие]) => (
              <tr key={какой}>
                <td>{какой}</td>
                <td className="num">{условие.rate}%</td>
                <td className="note">беспошлинно {условие.free} кг в сутки</td>
                <td>
                  <button className="quiet" onClick={() => убрать(какой)} disabled={busy}>
                    Снять
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="row">
        <input
          list={`товары-${закон}`}
          placeholder="товар"
          value={товар}
          onChange={(e) => setТовар(e.target.value)}
        />
        <datalist id={`товары-${закон}`}>
          {товары.map((имя) => (
            <option key={имя} value={имя} />
          ))}
        </datalist>
        <input
          type="number"
          value={ставка}
          onChange={(e) => setСтавка(Number(e.target.value))}
          title="ставка, % от справочной цены"
        />
        <input
          type="number"
          value={норма}
          onChange={(e) => setНорма(Number(e.target.value))}
          title="беспошлинная норма, кг в сутки на человека"
        />
        <button onClick={добавить} disabled={busy || !товар.trim() || ставка <= 0}>
          Ввести
        </button>
      </div>
    </div>
  );
}

/** Значение закона-карты приходит строкой JSON: разбираем, не падая. */
function разобрать(значение: string | null): Record<string, { rate: number; free: number }> {
  if (!значение) return {};
  try {
    const разобрано = JSON.parse(значение);
    if (разобрано && typeof разобрано === "object" && !Array.isArray(разобрано)) {
      return разобрано as Record<string, { rate: number; free: number }>;
    }
  } catch {
    //: Старое значение числом — это ставка на всё, и её показывает таблица
    //: законов выше. Здесь редактируются прицельные строки.
  }
  return {};
}

/** Набор прав для новой должности: крупные плюс по одному на каждый закон. */
function Scopes({
  город,
  выбрано,
  setВыбрано,
  может,
}: {
  город: CityView;
  выбрано: string[];
  setВыбрано: (кто: (было: string[]) => string[]) => void;
  может: (право: string) => boolean;
}) {
  const переключить = (право: string) =>
    setВыбрано((было) =>
      было.includes(право) ? было.filter((п) => п !== право) : [...было, право],
    );

  return (
    <div>
      <p className="note">Права должности — отдать можно только своё:</p>
      <div className="row">
        {Object.entries(api.POWERS).map(([ключ, имя]) => (
          <label className="note" key={ключ} title={может(ключ) ? "" : "нет у вас"}>
            <input
              type="checkbox"
              checked={выбрано.includes(ключ)}
              disabled={!может(ключ)}
              onChange={() => переключить(ключ)}
            />{" "}
            {имя}
          </label>
        ))}
      </div>
      <div className="row">
        {Object.entries(город.laws).map(([ключ, закон]) => {
          const право = api.LAW_SCOPE + ключ;
          return (
            <label className="note" key={ключ} title={закон.note ?? ""}>
              <input
                type="checkbox"
                checked={выбрано.includes(право)}
                disabled={!может(право)}
                onChange={() => переключить(право)}
              />{" "}
              {закон.name}
            </label>
          );
        })}
      </div>
    </div>
  );
}

/** Экономическая панель: публичный срез плюс казна тем, у кого есть право. */
export function Panel({ панель }: { панель: CityPanel | null }) {
  if (!панель) return <p className="note">панель недоступна</p>;
  if (панель.blind) {
    return (
      <p className="trouble">
        Город слеп: администрация не стоит либо отключена за неуплату. Данные не
        обновляются, и решения принимаются вслепую (D-140).
      </p>
    );
  }
  //: Раздел может не прийти: сервер бывает старше клиента, и падать всем
  //: экраном из-за одной отсутствующей строки сводки панель не вправе.
  const рынок = панель.market ?? { trades: 0, volume: 0, prices: {} };
  const люди = панель.people ?? { here: 0, printed: 0 };
  const энергия = панель.energy ?? {
    stored: 0,
    tariff: 0,
    spent_work: 0,
    spent_home: 0,
  };
  const работа = панель.production ?? { mined: {}, harvested: 0, crafted: {} };
  const граница = панель.trade ?? {
    imported: {},
    exported: {},
    trips_in: 0,
    trips_out: 0,
    duty_collected: 0,
  };
  const цены = Object.entries(рынок.prices ?? {});
  const товары = Object.entries(панель.goods ?? {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 12);

  return (
    <div>
      <p className="sign">
        за последние {панель.window_hours} ч · сделок {рынок.trades} · оборот{" "}
        {рынок.volume.toFixed(2)} ₭
      </p>

      <h3>Люди</h3>
      <p>
        в городе {люди.here} · напечаталось за период {люди.printed}
      </p>

      <h3>Энергия</h3>
      <p>
        в пуле {энергия.stored.toFixed(0)} · тариф {энергия.tariff} ₭ за 100 · на работу{" "}
        {энергия.spent_work.toFixed(0)} · на быт {энергия.spent_home.toFixed(0)}
      </p>

      <h3>Граница</h3>
      <p>
        ввезено{" "}
        {Object.entries(граница.imported)
          .map(([имя, кг]) => `${имя} ${кг.toFixed(1)} кг`)
          .join(", ") || "—"}{" "}
        · вывезено{" "}
        {Object.entries(граница.exported)
          .map(([имя, кг]) => `${имя} ${кг.toFixed(1)} кг`)
          .join(", ") || "—"}
      </p>
      <p className="note">
        ходок: {граница.trips_in} внутрь, {граница.trips_out} наружу · пошлин собрано{" "}
        {граница.duty_collected.toFixed(2)} ₭
      </p>

      <h3>Производство</h3>
      <p>
        добыто {(работа.mined?.["всего"] ?? 0).toFixed(1)} · убрано{" "}
        {(работа.harvested ?? 0).toFixed(1)} · выпущено{" "}
        {Object.entries(работа.crafted ?? {})
          .map(([имя, сколько]) => `${имя} ${сколько.toFixed(0)}`)
          .join(", ") || "—"}
      </p>

      <h3>Цены</h3>
      {цены.length === 0 ? (
        <p className="note">сделок за период не было</p>
      ) : (
        <table>
          <tbody>
            {цены.map(([имя, цена]) => (
              <tr key={имя}>
                <td>{имя}</td>
                <td className="num">{цена.toFixed(2)} ₭</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3>Товар в городе</h3>
      <table>
        <tbody>
          {товары.map(([имя, сколько]) => (
            <tr key={имя}>
              <td>{имя}</td>
              <td className="num">{сколько.toFixed(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {панель.treasury ? (
        <>
          <h3>Казна</h3>
          <p>остаток {панель.treasury.balance.toFixed(2)} ₭</p>
          <p className="note">
            собрано:{" "}
            {Object.entries(панель.treasury.collected)
              .map(([основание, сколько]) => `${основание} ${сколько.toFixed(2)}`)
              .join(", ") || "—"}
          </p>
          <p className="note">
            потрачено:{" "}
            {Object.entries(панель.treasury.spent)
              .map(([основание, сколько]) => `${основание} ${сколько.toFixed(2)}`)
              .join(", ") || "—"}
          </p>
        </>
      ) : (
        <p className="note">
          Казна по статьям — тем, у кого есть право «панель города». Балансы,
          обороты и цены открыты всем: без этого спорить с властью нечем (D-140).
        </p>
      )}

      <p className="note">
        Шаг сводки медленнее рынка нарочно: мгновенные данные дали бы власти
        торговое преимущество перед собственными купцами (D-124). Персонального
        здесь нет ни у кого — ни доходов, ни маршрутов.
      </p>
    </div>
  );
}

/** Право словами. Точечное показывается именем своего закона. */
const имяПрава = (город: CityView) => (право: string) => {
  if (право.startsWith(api.LAW_SCOPE)) {
    const ключ = право.slice(api.LAW_SCOPE.length);
    return город.laws[ключ]?.name ?? ключ;
  }
  return api.POWERS[право] ?? право;
};

/** Идущие голосования: предмет, срок, расклад и свой голос (D-161).
 *
 * Показываются всем, а не только власти: голосование, видимое лишь тому, кто
 * его созвал, — это не процедура, а формальность. Итог применяется сам по
 * сроку, поэтому кнопки «подвести» здесь нет и быть не может.
 */
function Votes({
  голосования,
  город,
  session,
  go,
  busy,
}: {
  голосования: CityVote[];
  город: CityView;
  session: Session;
  go: (what: () => Promise<unknown>) => Promise<void>;
  busy: boolean;
}) {
  //: Созыв показывается только там, где устав его допускает: сменяемость
  //: власти — тоже решение города, а не свойство движка (D-162).
  const выборный =
    город.charter?.ruler_selection === "elected_citizens" ||
    город.charter?.ruler_selection === "elected_council";
  const советный = город.charter?.council_exists === "elected";
  const отзывной =
    город.charter?.ruler_recall === "by_citizens" ||
    город.charter?.ruler_recall === "by_council";
  const идут = (вид: CityVote["kind"]) => голосования.some((г) => г.kind === вид);
  if (голосования.length === 0 && !выборный && !отзывной && !советный) return null;
  const порог: Record<string, string> = {
    simple: "простое большинство",
    two_thirds: "две трети",
    unanimous: "единогласно",
  };

  return (
    <>
      <h3>Голосования</h3>
      {(выборный || отзывной || советный) && (
        <div className="row">
          {выборный && !идут("election") && (
            <button
              onClick={() => go(() => session.send("city.election"))}
              disabled={busy}
            >
              Созвать выборы
            </button>
          )}
          {город.charter?.council_exists === "elected" && !идут("council") && (
            <button
              className="quiet"
              onClick={() => go(() => session.send("city.council_election"))}
              disabled={busy}
            >
              Выборы в совет
            </button>
          )}
          {отзывной && !идут("recall") && (
            <button
              className="quiet"
              onClick={() => go(() => session.send("city.recall"))}
              disabled={busy}
            >
              Отозвать правителя
            </button>
          )}
          <span className="note">
            Итог применяется сам: избранный получает набор прежнего правителя,
            отзыв снимает должность и тут же созывает выборы (D-162).
          </span>
        </div>
      )}
      {голосования.length > 0 && (
      <table>
        <tbody>
          {голосования.map((созыв) => (
            <tr key={созыв.id}>
              <td>
                {созыв.kind === "election" || созыв.kind === "council" ? (
                  <>
                    {созыв.kind === "council" ? "выборы в совет" : "выборы правителя"}
                    <span className="note">
                      {" "}
                      {созыв.candidates.length === 0
                        ? "· кандидатов нет"
                        : `· ${созыв.candidates
                            .map((к) => `${к.name} (${к.votes})`)
                            .join(", ")}`}
                    </span>
                  </>
                ) : созыв.kind === "recall" ? (
                  "отзыв правителя"
                ) : созыв.kind === "charter" ? (
                  <>
                    устав
                    <span className="note">
                      {" "}
                      · порог из `charter_amendment`, а не из закона
                    </span>
                  </>
                ) : (
                  <>
                    {созыв.law}
                    <span className="note"> → {String(созыв.value)}</span>
                  </>
                )}
              </td>
              <td className="note">
                {созыв.voters === "council" && "решает совет · "}
                за {созыв.yes} · против {созыв.no} · из {созыв.electorate} ·{" "}
                {порог[созыв.threshold] ?? созыв.threshold}
                {созыв.quorum > 0 && ` · кворум ${созыв.quorum}%`}
              </td>
              <td className="note">до {new Date(созыв.closes_at).toLocaleString()}</td>
              <td>
                {созыв.kind === "election" || созыв.kind === "council" ? (
                  <>
                    <button
                      className="quiet"
                      onClick={() =>
                        go(() => session.send("city.nominate", { vote: созыв.id }))
                      }
                      disabled={busy}
                      title="выдвинуться в правители"
                    >
                      Выдвинуться
                    </button>
                    {созыв.candidates.map((кандидат) => (
                      <button
                        key={кандидат.id}
                        className={созыв.choice === кандидат.id ? "" : "quiet"}
                        onClick={() =>
                          go(() =>
                            session.send("city.choose", {
                              vote: созыв.id,
                              candidate: кандидат.id,
                            }),
                          )
                        }
                        disabled={busy || !созыв.may_vote}
                      >
                        За {кандидат.name}
                      </button>
                    ))}
                  </>
                ) : созыв.may_vote ? (
                  <>
                    <button
                      className={созыв.mine === true ? "" : "quiet"}
                      onClick={() =>
                        go(() => session.send("city.vote", { vote: созыв.id, yes: true }))
                      }
                      disabled={busy}
                    >
                      За
                    </button>
                    <button
                      className={созыв.mine === false ? "" : "quiet"}
                      onClick={() =>
                        go(() => session.send("city.vote", { vote: созыв.id, yes: false }))
                      }
                      disabled={busy}
                    >
                      Против
                    </button>
                  </>
                ) : (
                  <span className="note">голоса нет</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      )}
      <p className="note">
        Голос подаётся по Сети — присутствие нужно, чтобы править, а не чтобы
        участвовать. Итог применится сам, когда выйдет срок.
      </p>
    </>
  );
}

/** Суд города: дела и приговоры (D-095, D-117, D-166).
 *
 * Карточка дела показывает истца, ответчика и суть словами: разбирать её —
 * работа судьи, а не движка. Санкции перечислены из вольта, и неисполнимые
 * помечены честно — приговор без исполнения хуже, чем отказ от приговора.
 */
function Court({
  дела,
  санкции,
  каторги,
  может,
  session,
  go,
  busy,
}: {
  дела: CourtCase[];
  санкции: SanctionKind[];
  каторги: { key: string; name: string }[];
  может: boolean;
  session: Session;
  go: (what: () => Promise<unknown>) => Promise<void>;
  busy: boolean;
}) {
  const [кому, setКому] = useState("");
  const [суть, setСуть] = useState("");
  const [санкция, setСанкция] = useState("fine");
  const [сколько, setСколько] = useState(10);
  const [каторга, setКаторга] = useState("");
  const открытые = дела.filter((дело) => дело.state === "open");
  if (дела.length === 0 && !может) return null;

  return (
    <>
      <h3>Суд</h3>
      {дела.length > 0 && (
        <table>
          <tbody>
            {дела.slice(0, 8).map((дело) => (
              <tr key={дело.id}>
                <td>
                  {дело.plaintiff} → {дело.defendant}
                  <span className="note"> · {дело.claim}</span>
                </td>
                <td className="note">
                  {дело.state === "open"
                    ? "ждёт суда"
                    : дело.state === "judged"
                      ? `приговор: ${дело.verdict}`
                      : `отказано: ${дело.verdict}`}
                </td>
                <td>
                  {может && дело.state === "open" && (
                    <>
                      <select
                        value={санкция}
                        onChange={(e) => setСанкция(e.target.value)}
                      >
                        {санкции.map((вид) => (
                          <option key={вид.id} value={вид.id} disabled={!вид.enforced}>
                            {вид.name}
                            {вид.enforced ? "" : " (не исполняется)"}
                          </option>
                        ))}
                      </select>
                      <input
                        type="number"
                        min={0}
                        value={сколько}
                        onChange={(e) => setСколько(Number(e.target.value))}
                        title="сумма штрафа либо срок заключения в сутках"
                      />
                      {/* Куда сажать — решает суд (D-176): каторга одна —
                          очевидно, несколько — судья называет которую. */}
                      {санкция === "prison" && каторги.length > 1 && (
                        <select
                          value={каторга}
                          onChange={(e) => setКаторга(e.target.value)}
                          title="в какую каторгу отправить"
                        >
                          <option value="">— каторга —</option>
                          {каторги.map((узел) => (
                            <option key={узел.key} value={узел.key}>
                              {узел.name}
                            </option>
                          ))}
                        </select>
                      )}
                      <button
                        onClick={() =>
                          go(() =>
                            session.send("city.judge", {
                              case: дело.id,
                              sanction: санкция,
                              amount: сколько,
                              days: сколько,
                              ...(санкция === "prison" && каторга
                                ? { prison: каторга }
                                : {}),
                            }),
                          )
                        }
                        disabled={busy || (санкция === "prison" && каторги.length > 1 && !каторга)}
                      >
                        Приговор
                      </button>
                      <button
                        className="quiet"
                        onClick={() =>
                          go(() => session.send("city.judge", { case: дело.id }))
                        }
                        disabled={busy}
                      >
                        Отказать
                      </button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="row">
        <input
          value={кому}
          onChange={(e) => setКому(e.target.value)}
          placeholder="на кого"
        />
        <input
          value={суть}
          onChange={(e) => setСуть(e.target.value)}
          placeholder="суть жалобы"
        />
        <button
          onClick={() =>
            go(() => session.send("city.sue", { who: кому, claim: суть }))
          }
          disabled={busy || !кому.trim() || !суть.trim()}
        >
          Подать жалобу
        </button>
        <span className="note">
          Жалоба стоит пошлины в казну города (D-117, D-166).
        </span>
      </div>
      {открытые.length > 0 && !может && (
        <p className="note">
          Дел в очереди: {открытые.length}. Судит тот, кому город дал право суда.
        </p>
      )}
    </>
  );
}
