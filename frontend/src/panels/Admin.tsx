/**
 * Administration: offices, rights, laws, charter, treasury, panel (D-154, D-155).
 *
 * The panel stands **in the location, not the sidebar**: authority is
 * in-person. A city decision is made where the administration stands --
 * otherwise the building becomes decoration, and seizing power a matter of
 * one click rather than geography (D-155).
 *
 * A right here is not chosen from four broad ones. It is assembled: a
 * "minister of economy" is `law:import_duty`, `law:export_duty` and
 * `dashboard`, and the city invents the title, not the engine. The law list
 * comes from the server out of the vault, so a new code-law appears in this list by itself.
 *
 * The economic panel is read **remotely** (D-140) and is therefore shown to
 * everyone who came in: figures are common knowledge, there is nothing to
 * argue with the authority without them. The treasury by line item -- only to
 * those with the `dashboard` right.
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
import { when } from "../clock";
import { Rule } from "../Rule";
import { Refusal, useActions } from "../actions";

type Props = {
  look: Look;
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

export function Admin({ look, session }: Omit<Props, "busy" | "act">) {
  //: This panel's own waiting and its own refusal: one action here
  //: must not grey out the chat, the map and somebody else's orders.
  const acting = useActions();
  const { busy, act } = acting;

  const [city, setCity] = useState<CityView | null>(null);
  const [panel, setPanel] = useState<CityPanel | null>(null);
  const [polls, setPolls] = useState<CityVote[]>([]);
  const [jobs, setJobs] = useState<CourtCase[]>([]);
  const [sanctions, setSanctions] = useState<SanctionKind[]>([]);
  const [penalColonies, setPenalColonies] = useState<{ key: string; name: string }[]>([]);
  const [edit, setEdit] = useState<Record<string, string>>({});
  const [toWhom, setToWhom] = useState("");
  const [post, setPost] = useState("Министр экономики");
  const [rights, setRights] = useState<string[]>(["dashboard"]);
  const [amount, setAmount] = useState(0);
  const [plot, setPlot] = useState("");
  const [kind, setKind] = useState<"власть" | "панель">("власть");

  const reload = useCallback(async () => {
    try {
      const summary = await session.send("city.survey");
      setCity((summary.city as CityView) ?? null);
      const snapshot = await session.send("city.panel");
      setPanel((snapshot.panel as CityPanel) ?? null);
      //: Polls run by their own term and without players: they are shown to
      //: everyone, not only the authority -- that is what polls are for (D-161).
      const convenings = await session.send("city.votes");
      setPolls((convenings.votes as CityVote[]) ?? []);
      const court = await session.send("city.cases");
      setJobs((court.cases as CourtCase[]) ?? []);
      setSanctions((court.sanctions as SanctionKind[]) ?? []);
      setPenalColonies((court.prisons as { key: string; name: string }[]) ?? []);
    } catch {
      setCity(null);
      setPanel(null);
    }
  }, [session]);

  useEffect(() => {
    void reload();
  }, [reload, look.node?.key]);

  const go = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await reload();
    });

  if (!city) {
    return (
      <section>
        <Refusal of={acting} />
        <h2>Администрация</h2>
        <p className="note">Здесь нет города: за стенами законов нет.</p>
      </section>
    );
  }

  const can = (right: string) =>
    city.powers.includes(right) ||
    (right.startsWith(api.LAW_SCOPE) && city.powers.includes("laws"));
  const decides = city.at_hall;
  const residents = city.citizens.filter((name) => name !== look.identity);
  const vacant = city.lots.filter((lot) => lot.free);

  return (
    <section>
      <h2>Администрация · {city.name}</h2>
      <nav className="row tabs">
        {(["власть", "панель"] as const).map((name) => (
          <button
            key={name}
            className={kind === name ? "" : "quiet"}
            onClick={() => setKind(name)}
          >
            {name}
          </button>
        ))}
      </nav>

      {kind === "панель" ? (
        <Panel panel={panel} />
      ) : (
        <>
        <Court jobs={jobs} sanctions={sanctions} penalColonies={penalColonies} can={can("justice")} session={session} go={go} busy={busy} />
        <Votes
          polls={polls}
          city={city}
          session={session}
          go={go}
          busy={busy}
        />
        <>
          <p className="sign">казна {api.tk(city.treasury)} ₭</p>
          <p className="note">
            {city.powers.length === 0
              ? "Вы здесь житель: законы видны, правят их должностные лица."
              : `Ваши права: ${city.powers.map(rightName(city)).join(", ")}.`}
            {!decides && city.powers.length > 0 && (
              <b> Решения принимаются в администрации — придите в неё.</b>
            )}
          </p>

          <Word
            city={city}
            can={can("citizens") && decides}
            session={session}
            go={go}
            busy={busy}
          />

          <h3>Должности</h3>
          {city.offices.length === 0 ? (
            <p className="note">должностей нет</p>
          ) : (
            city.offices.map((office) => (
              <div className="row" key={office.id}>
                <span>
                  <b>{office.title}</b> · {office.who}
                  <span className="note">
                    {" "}
                    · {office.powers.map(rightName(city)).join(", ")}
                  </span>
                </span>
                {can("offices") && decides && office.who !== look.identity && (
                  <button
                    className="quiet"
                    onClick={() => go(() => session.send("city.revoke", { office: office.id }))}
                    disabled={busy}
                  >
                    Снять
                  </button>
                )}
              </div>
            ))
          )}

          {can("offices") && decides && residents.length > 0 && (
            <>
              <h3>Создать должность</h3>
              <div className="row">
                <select value={toWhom} onChange={(e) => setToWhom(e.target.value)}>
                  <option value="">кого назначить</option>
                  {residents.map((name) => (
                    <option key={name}>{name}</option>
                  ))}
                </select>
                <input
                  value={post}
                  onChange={(e) => setPost(e.target.value)}
                  title="название придумывает город, движок смотрит в права"
                />
                <button
                  onClick={() =>
                    go(() =>
                      session.send("city.appoint", {
                        whom: toWhom,
                        title: post,
                        powers: rights,
                      }),
                    )
                  }
                  disabled={busy || !toWhom || rights.length === 0}
                >
                  Назначить
                </button>
              </div>
              <Scopes
                city={city}
                selected={rights}
                setSelected={setRights}
                can={can}
              />
            </>
          )}

          <h3>Код-законы</h3>
          <table>
            <tbody>
              {Object.entries(city.laws).map(([key, law]) => {
                const editing = can(api.LAW_SCOPE + key) && decides;
                return (
                  <tr key={key}>
                    <td title={law.note ?? ""}>
                      {law.name}
                      {law.unit && <span className="note"> · {law.unit}</span>}
                    </td>
                    <td className="num">
                      {editing ? (
                        <input
                          value={edit[key] ?? law.value ?? ""}
                          onChange={(e) =>
                            setEdit((before) => ({ ...before, [key]: e.target.value }))
                          }
                          size={10}
                        />
                      ) : (
                        <b>{law.value ?? "—"}</b>
                      )}
                    </td>
                    <td className="note">{law.own ? "решение города" : "умолчание"}</td>
                    <td>
                      {editing && (edit[key] ?? "") !== "" && (
                        <button
                          onClick={() =>
                            go(() =>
                              session.send("city.law", { law: key, value: edit[key] }),
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
            трогает налог. Отдать можно только то, что есть у себя.
          </p>

          {(["import_duty", "export_duty"] as const)
            .filter((key) => can(api.LAW_SCOPE + key) && decides)
            .map((key) => (
              <Customs
                key={key}
                law={key}
                name={city.laws[key]?.name ?? key}
                value={city.laws[key]?.value ?? null}
                goods={Object.keys(panel?.goods ?? {})}
                busy={busy}
                apply={(value) =>
                  go(() => session.send("city.law", { law: key, value: value }))
                }
              />
            ))}

          {can("land") && decides && vacant.length > 0 && residents.length > 0 && (
            <>
              <h3>Свободные участки</h3>
              <div className="row">
                <select value={plot} onChange={(e) => setPlot(e.target.value)}>
                  <option value="">какой участок</option>
                  {vacant.map((lot) => (
                    <option key={lot.key} value={lot.key}>
                      {lot.name} · {lot.area.toFixed(0)} м²
                    </option>
                  ))}
                </select>
                <select value={toWhom} onChange={(e) => setToWhom(e.target.value)}>
                  <option value="">кому</option>
                  {residents.map((name) => (
                    <option key={name}>{name}</option>
                  ))}
                </select>
                <button
                  onClick={() =>
                    go(() => session.send("city.allot", { node: plot, whom: toWhom }))
                  }
                  disabled={busy || !plot || !toWhom}
                >
                  Выделить
                </button>
              </div>
            </>
          )}

          {can("treasury") && decides && residents.length > 0 && (
            <>
              <h3>Казна</h3>
              <div className="row">
                <select value={toWhom} onChange={(e) => setToWhom(e.target.value)}>
                  <option value="">кому</option>
                  {residents.map((name) => (
                    <option key={name}>{name}</option>
                  ))}
                </select>
                <input
                  type="number"
                  min={0}
                  value={amount}
                  onChange={(e) => setAmount(Number(e.target.value))}
                />
                <button
                  onClick={() =>
                    go(() =>
                      session.send("city.spend", {
                        whom: toWhom,
                        amount: api.minor(amount),
                        memo: "выплата",
                      }),
                    )
                  }
                  disabled={busy || !toWhom || amount <= 0}
                >
                  Заплатить ₭
                </button>
              </div>
            </>
          )}

          <h3>
            Устав
            <Rule>
              Устав решает, кто утверждает закон: «правитель единолично» меняет его
              сразу, «голосованием граждан» — созывает голосование. Выборы правителя и
              совет приедут своей механикой.
            </Rule>
          </h3>
          <table>
            <tbody>
              {(city.charter_questions ?? []).map((question) => {
                const answer = city.charter[question.id];
                const option = question.options.find((o) => o.id === answer);
                return (
                  <tr key={question.id}>
                    <td className="note">
                      {question.section} · {question.question}
                    </td>
                    <td>
                      {can("charter") && decides ? (
                        <select
                          value={answer ?? ""}
                          onChange={(e) =>
                            go(() =>
                              session.send("city.charter", {
                                question: question.id,
                                option: e.target.value,
                              }),
                            )
                          }
                          disabled={busy}
                        >
                          {question.options.map((o) => (
                            <option key={o.id} value={o.id}>
                              {o.label}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <b>{option?.label ?? answer ?? "—"}</b>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
        </>
      )}
    </section>
  );
}

/** The city's word to newcomers -- what stands on the door card (D-183).
 *
 * It is edited by whoever admits citizens: the announcement is recruitment.
 * The engine does not enforce what is written -- the promise here binds people, not code. */
function Word({
  city,
  can,
  session,
  go,
  busy,
}: {
  city: CityView;
  can: boolean;
  session: Session;
  go: (what: () => Promise<unknown>) => void;
  busy: boolean;
}) {
  const [text, setText] = useState<string | null>(null);
  const tally = text ?? city.about;

  return (
    <div>
      <h3>Слово городу</h3>
      {city.about ? (
        <p className="say">«{city.about}»</p>
      ) : (
        <p className="note">город молчит: новичок видит одни числа</p>
      )}
      {can && (
        <>
          <div className="row">
            <textarea
              className="word"
              value={tally}
              maxLength={api.CITY_ABOUT_LIMIT}
              placeholder="чем город зовёт новичка"
              onChange={(e) => setText(e.target.value)}
            />
          </div>
          <div className="row">
            <button
              onClick={() =>
                go(async () => {
                  await session.send("city.about", { text: tally });
                  setText(null);
                })
              }
              disabled={busy || tally === city.about}
            >
              Объявить
            </button>
            <span className="note">
              {tally.length} из {api.CITY_ABOUT_LIMIT} знаков · видно всем,
              кто выбирает, где напечататься
            </span>
          </div>
        </>
      )}
    </div>
  );
}

/** Duty: goods, rate and duty-free norm (D-123).
 *
 * A rate without a norm hits everyone alike, and the first to suffer is the
 * resident with a sack of turnips. So the row here is always three parts, not one. */
function Customs({
  law,
  name,
  value,
  goods,
  busy,
  apply,
}: {
  law: string;
  name: string;
  value: string | null;
  goods: string[];
  busy: boolean;
  apply: (value: unknown) => void;
}) {
  const parsed = parse(value);
  const [item, setItem] = useState("");
  const [rate, setRate] = useState(10);
  const [norm, setNorm] = useState(30);

  const add = () =>
    apply({ ...parsed, [item]: { rate: rate, free: norm } });
  const remove = (which: string) => {
    const without = { ...parsed };
    delete without[which];
    apply(without);
  };

  return (
    <div>
      <h3>{name}</h3>
      {Object.keys(parsed).length === 0 ? (
        <p className="note">граница открыта: ставок нет</p>
      ) : (
        <table>
          <tbody>
            {Object.entries(parsed).map(([which, condition]) => (
              <tr key={which}>
                <td>{which}</td>
                <td className="num">{condition.rate}%</td>
                <td className="note">беспошлинно {condition.free} кг в сутки</td>
                <td>
                  <button className="quiet" onClick={() => remove(which)} disabled={busy}>
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
          list={`товары-${law}`}
          placeholder="товар"
          value={item}
          onChange={(e) => setItem(e.target.value)}
        />
        <datalist id={`товары-${law}`}>
          {goods.map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>
        <input
          type="number"
          value={rate}
          onChange={(e) => setRate(Number(e.target.value))}
          title="ставка, % от справочной цены"
        />
        <input
          type="number"
          value={norm}
          onChange={(e) => setNorm(Number(e.target.value))}
          title="беспошлинная норма, кг в сутки на человека"
        />
        <button onClick={add} disabled={busy || !item.trim() || rate <= 0}>
          Ввести
        </button>
      </div>
    </div>
  );
}

/** A map-law's value comes as a JSON string: parse without crashing. */
function parse(value: string | null): Record<string, { rate: number; free: number }> {
  if (!value) return {};
  try {
    const parsed = JSON.parse(value);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, { rate: number; free: number }>;
    }
  } catch {
    //: Старое значение числом — это ставка на всё, и её показывает таблица
    //: законов выше. Здесь редактируются прицельные строки.
  }
  return {};
}

/** The set of rights for a new office: broad ones plus one per law. */
function Scopes({
  city,
  selected,
  setSelected,
  can,
}: {
  city: CityView;
  selected: string[];
  setSelected: (who: (before: string[]) => string[]) => void;
  can: (right: string) => boolean;
}) {
  const toggle = (right: string) =>
    setSelected((before) =>
      before.includes(right) ? before.filter((p) => p !== right) : [...before, right],
    );

  return (
    <div>
      <p className="note">Права должности — отдать можно только своё:</p>
      <div className="row">
        {Object.entries(api.POWERS).map(([key, name]) => (
          <label className="note" key={key} title={can(key) ? "" : "нет у вас"}>
            <input
              type="checkbox"
              checked={selected.includes(key)}
              disabled={!can(key)}
              onChange={() => toggle(key)}
            />{" "}
            {name}
          </label>
        ))}
      </div>
      <div className="row">
        {Object.entries(city.laws).map(([key, law]) => {
          const right = api.LAW_SCOPE + key;
          return (
            <label className="note" key={key} title={law.note ?? ""}>
              <input
                type="checkbox"
                checked={selected.includes(right)}
                disabled={!can(right)}
                onChange={() => toggle(right)}
              />{" "}
              {law.name}
            </label>
          );
        })}
      </div>
    </div>
  );
}

/** The economic panel: the public snapshot plus the treasury for those with the right. */
export function Panel({ panel }: { panel: CityPanel | null }) {
  if (!panel) return <p className="note">панель недоступна</p>;
  if (panel.blind) {
    return (
      <p className="trouble">
        Город слеп: администрация не стоит либо отключена за неуплату. Данные не
        обновляются, и решения принимаются вслепую.
      </p>
    );
  }
  //: A section may not arrive: the server may be older than the client, and
  //: the panel may not crash the whole screen over one missing summary line.
  const market = panel.market ?? { trades: 0, volume: 0, prices: {} };
  const people = panel.people ?? { here: 0, printed: 0 };
  const energy = panel.energy ?? {
    stored: 0,
    tariff: 0,
    spent_work: 0,
    spent_home: 0,
  };
  const work = panel.production ?? { mined: {}, harvested: 0, crafted: {} };
  const border = panel.trade ?? {
    imported: {},
    exported: {},
    trips_in: 0,
    trips_out: 0,
    duty_collected: 0,
  };
  const prices = Object.entries(market.prices ?? {});
  const goods = Object.entries(panel.goods ?? {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 12);

  return (
    <div>
      <p className="sign">
        за последние {panel.window_hours} ч · сделок {market.trades} · оборот{" "}
        {market.volume.toFixed(2)} ₭
        <Rule>
          Шаг сводки медленнее рынка нарочно: мгновенные данные дали бы власти
          торговое преимущество перед собственными купцами. Персонального здесь нет ни
          у кого — ни доходов, ни маршрутов.
        </Rule>
      </p>

      <h3>Люди</h3>
      <p>
        в городе {people.here} · напечаталось за период {people.printed}
      </p>

      <h3>Энергия</h3>
      <p>
        в пуле {energy.stored.toFixed(0)} · тариф {energy.tariff} ₭ за 100 · на работу{" "}
        {energy.spent_work.toFixed(0)} · на быт {energy.spent_home.toFixed(0)}
      </p>

      <h3>Граница</h3>
      <p>
        ввезено{" "}
        {Object.entries(border.imported)
          .map(([name, kg]) => `${name} ${kg.toFixed(1)} кг`)
          .join(", ") || "—"}{" "}
        · вывезено{" "}
        {Object.entries(border.exported)
          .map(([name, kg]) => `${name} ${kg.toFixed(1)} кг`)
          .join(", ") || "—"}
      </p>
      <p className="note">
        ходок: {border.trips_in} внутрь, {border.trips_out} наружу · пошлин собрано{" "}
        {border.duty_collected.toFixed(2)} ₭
      </p>

      <h3>Производство</h3>
      <p>
        добыто {(work.mined?.["всего"] ?? 0).toFixed(1)} · убрано{" "}
        {(work.harvested ?? 0).toFixed(1)} · выпущено{" "}
        {Object.entries(work.crafted ?? {})
          .map(([name, qty]) => `${name} ${qty.toFixed(0)}`)
          .join(", ") || "—"}
      </p>

      <h3>Цены</h3>
      {prices.length === 0 ? (
        <p className="note">сделок за период не было</p>
      ) : (
        <table>
          <tbody>
            {prices.map(([name, price]) => (
              <tr key={name}>
                <td>{name}</td>
                <td className="num">{price.toFixed(2)} ₭</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3>Товар в городе</h3>
      <table>
        <tbody>
          {goods.map(([name, qty]) => (
            <tr key={name}>
              <td>{name}</td>
              <td className="num">{qty.toFixed(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {panel.treasury ? (
        <>
          <h3>Казна</h3>
          <p>остаток {panel.treasury.balance.toFixed(2)} ₭</p>
          <p className="note">
            собрано:{" "}
            {Object.entries(panel.treasury.collected)
              .map(([ground, qty]) => `${ground} ${qty.toFixed(2)}`)
              .join(", ") || "—"}
          </p>
          <p className="note">
            потрачено:{" "}
            {Object.entries(panel.treasury.spent)
              .map(([ground, qty]) => `${ground} ${qty.toFixed(2)}`)
              .join(", ") || "—"}
          </p>
        </>
      ) : (
        <p className="note">
          Казна по статьям — тем, у кого есть право «панель города». Балансы,
          обороты и цены открыты всем: без этого спорить с властью нечем.
        </p>
      )}
    </div>
  );
}

/** A right in words. A narrow one is shown by its law's name. */
const rightName = (city: CityView) => (right: string) => {
  if (right.startsWith(api.LAW_SCOPE)) {
    const key = right.slice(api.LAW_SCOPE.length);
    return city.laws[key]?.name ?? key;
  }
  return api.POWERS[right] ?? right;
};

/** Ongoing polls: subject, deadline, tally and own vote (D-161).
 *
 * Shown to everyone, not only the authority: a poll visible only to whoever
 * convened it is not a procedure but a formality. The result applies itself
 * on schedule, so there is and cannot be a "tally" button here.
 */
function Votes({
  polls,
  city,
  session,
  go,
  busy,
}: {
  polls: CityVote[];
  city: CityView;
  session: Session;
  go: (what: () => Promise<unknown>) => Promise<void>;
  busy: boolean;
}) {
  //: Convening is shown only where the charter allows it: turnover of power
  //: is also a city decision, not an engine property (D-162).
  const elective =
    city.charter?.ruler_selection === "elected_citizens" ||
    city.charter?.ruler_selection === "elected_council";
  const byCouncil = city.charter?.council_exists === "elected";
  const recallable =
    city.charter?.ruler_recall === "by_citizens" ||
    city.charter?.ruler_recall === "by_council";
  const running = (kind: CityVote["kind"]) => polls.some((g) => g.kind === kind);
  if (polls.length === 0 && !elective && !recallable && !byCouncil) return null;
  const threshold: Record<string, string> = {
    simple: "простое большинство",
    two_thirds: "две трети",
    unanimous: "единогласно",
  };

  return (
    <>
      <h3>
        Голосования
        <Rule>
          Голос подаётся по Сети — присутствие нужно, чтобы править, а не чтобы
          участвовать. Итог применится сам, когда выйдет срок.
        </Rule>
      </h3>
      {(elective || recallable || byCouncil) && (
        <div className="row">
          {elective && !running("election") && (
            <button
              onClick={() => go(() => session.send("city.election"))}
              disabled={busy}
            >
              Созвать выборы
            </button>
          )}
          {city.charter?.council_exists === "elected" && !running("council") && (
            <button
              className="quiet"
              onClick={() => go(() => session.send("city.council_election"))}
              disabled={busy}
            >
              Выборы в совет
            </button>
          )}
          {recallable && !running("recall") && (
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
            отзыв снимает должность и тут же созывает выборы.
          </span>
        </div>
      )}
      {polls.length > 0 && (
      <table>
        <tbody>
          {polls.map((convening) => (
            <tr key={convening.id}>
              <td>
                {convening.kind === "election" || convening.kind === "council" ? (
                  <>
                    {convening.kind === "council" ? "выборы в совет" : "выборы правителя"}
                    <span className="note">
                      {" "}
                      {convening.candidates.length === 0
                        ? "· кандидатов нет"
                        : `· ${convening.candidates
                            .map((k) => `${k.name} (${k.votes})`)
                            .join(", ")}`}
                    </span>
                  </>
                ) : convening.kind === "recall" ? (
                  "отзыв правителя"
                ) : convening.kind === "charter" ? (
                  <>
                    устав
                    <span className="note">
                      {" "}
                      · порог из `charter_amendment`, а не из закона
                    </span>
                  </>
                ) : (
                  <>
                    {convening.law}
                    <span className="note"> → {String(convening.value)}</span>
                  </>
                )}
              </td>
              <td className="note">
                {convening.voters === "council" && "решает совет · "}
                за {convening.yes} · против {convening.no} · из {convening.electorate} ·{" "}
                {threshold[convening.threshold] ?? convening.threshold}
                {convening.quorum > 0 && ` · кворум ${convening.quorum}%`}
              </td>
              <td className="note">закроется {when(convening.closes_at)}</td>
              <td>
                {convening.kind === "election" || convening.kind === "council" ? (
                  <>
                    <button
                      className="quiet"
                      onClick={() =>
                        go(() => session.send("city.nominate", { vote: convening.id }))
                      }
                      disabled={busy}
                      title="выдвинуться в правители"
                    >
                      Выдвинуться
                    </button>
                    {convening.candidates.map((candidate) => (
                      <button
                        key={candidate.id}
                        className={convening.choice === candidate.id ? "" : "quiet"}
                        onClick={() =>
                          go(() =>
                            session.send("city.choose", {
                              vote: convening.id,
                              candidate: candidate.id,
                            }),
                          )
                        }
                        disabled={busy || !convening.may_vote}
                      >
                        За {candidate.name}
                      </button>
                    ))}
                  </>
                ) : convening.may_vote ? (
                  <>
                    <button
                      className={convening.mine === true ? "" : "quiet"}
                      onClick={() =>
                        go(() => session.send("city.vote", { vote: convening.id, yes: true }))
                      }
                      disabled={busy}
                    >
                      За
                    </button>
                    <button
                      className={convening.mine === false ? "" : "quiet"}
                      onClick={() =>
                        go(() => session.send("city.vote", { vote: convening.id, yes: false }))
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
    </>
  );
}

/** The city court: cases and verdicts (D-095, D-117, D-166).
 *
 * The case card shows the plaintiff, the defendant and the substance in
 * words: examining it is the judge's work, not the engine's. Sanctions are
 * listed from the vault, and unenforceable ones are marked honestly -- a
 * verdict without enforcement is worse than refusing a verdict.
 */

function Court({
  jobs,
  sanctions,
  penalColonies,
  can,
  session,
  go,
  busy,
}: {
  jobs: CourtCase[];
  sanctions: SanctionKind[];
  penalColonies: { key: string; name: string }[];
  can: boolean;
  session: Session;
  go: (what: () => Promise<unknown>) => Promise<void>;
  busy: boolean;
}) {
  const [toWhom, setToWhom] = useState("");
  const [essence, setEssence] = useState("");
  const [sanction, setSanction] = useState("fine");
  const [qty, setQty] = useState(10);
  const [penalColony, setPenalColony] = useState("");
  const open = jobs.filter((job) => job.state === "open");
  if (jobs.length === 0 && !can) return null;

  return (
    <>
      <h3>Суд</h3>
      {jobs.length > 0 && (
        <table>
          <tbody>
            {jobs.slice(0, 8).map((job) => (
              <tr key={job.id}>
                <td>
                  {job.plaintiff} → {job.defendant}
                  <span className="note"> · {job.claim}</span>
                </td>
                <td className="note">
                  {job.state === "open"
                    ? "ждёт суда"
                    : job.state === "judged"
                      ? `приговор: ${job.verdict}`
                      : `отказано: ${job.verdict}`}
                </td>
                <td>
                  {can && job.state === "open" && (
                    <>
                      <select
                        value={sanction}
                        onChange={(e) => setSanction(e.target.value)}
                      >
                        {sanctions.map((kind) => (
                          <option key={kind.id} value={kind.id} disabled={!kind.enforced}>
                            {kind.name}
                            {kind.enforced ? "" : " (не исполняется)"}
                          </option>
                        ))}
                      </select>
                      <input
                        type="number"
                        min={0}
                        value={qty}
                        onChange={(e) => setQty(Number(e.target.value))}
                        title="сумма штрафа либо срок заключения в сутках"
                      />
                      {/* Куда сажать — решает суд (D-176): каторга одна —
                          очевидно, несколько — судья называет которую. */}
                      {sanction === "prison" && penalColonies.length > 1 && (
                        <select
                          value={penalColony}
                          onChange={(e) => setPenalColony(e.target.value)}
                          title="в какую каторгу отправить"
                        >
                          <option value="">— каторга —</option>
                          {penalColonies.map((node) => (
                            <option key={node.key} value={node.key}>
                              {node.name}
                            </option>
                          ))}
                        </select>
                      )}
                      <button
                        onClick={() =>
                          go(() =>
                            session.send("city.judge", {
                              case: job.id,
                              sanction: sanction,
                              amount: qty,
                              days: qty,
                              ...(sanction === "prison" && penalColony
                                ? { prison: penalColony }
                                : {}),
                            }),
                          )
                        }
                        disabled={busy || (sanction === "prison" && penalColonies.length > 1 && !penalColony)}
                      >
                        Приговор
                      </button>
                      <button
                        className="quiet"
                        onClick={() =>
                          go(() => session.send("city.judge", { case: job.id }))
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
          value={toWhom}
          onChange={(e) => setToWhom(e.target.value)}
          placeholder="на кого"
        />
        <input
          value={essence}
          onChange={(e) => setEssence(e.target.value)}
          placeholder="суть жалобы"
        />
        <button
          onClick={() =>
            go(() => session.send("city.sue", { who: toWhom, claim: essence }))
          }
          disabled={busy || !toWhom.trim() || !essence.trim()}
        >
          Подать жалобу
        </button>
        <span className="note">
          Жалоба стоит пошлины в казну города.
        </span>
      </div>
      {open.length > 0 && !can && (
        <p className="note">
          Дел в очереди: {open.length}. Судит тот, кому город дал право суда.
        </p>
      )}
    </>
  );
}
