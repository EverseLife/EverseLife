// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

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
} from "../api";
import { when } from "../clock";
import { groundName } from "../grounds";
import { t } from "../locale";
import { goodsName } from "../names";
import { Rule } from "../Rule";
import { Refusal, useActions, useBook, useNames, useSession } from "../actions";
import { CityWorks } from "./CityWorks";

type Props = {
  look: Look;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

/** The two halves of this window, each by the message that names its tab. */
const TABS = [
  { id: "power", label: "ui-admin-tab-power" },
  { id: "panel", label: "ui-admin-tab-panel" },
] as const;

export function Admin({ look }: Omit<Props, "busy" | "act">) {
  const session = useSession();
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
  const [post, setPost] = useState(t("ui-admin-post-default"));
  const [rights, setRights] = useState<string[]>(["dashboard"]);
  const [amount, setAmount] = useState(0);
  const [plot, setPlot] = useState("");
  const [kind, setKind] = useState<(typeof TABS)[number]["id"]>("power");

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
      <>
        <Citizenship look={look} />
        <section>
          <Refusal of={acting} />
          <h2>{t("ui-admin-title")}</h2>
          <p className="note">{t("ui-admin-no-city")}</p>
        </section>
      </>
    );
  }

  const can = (right: string) =>
    city.powers.includes(right) ||
    (right.startsWith(api.LAW_SCOPE) && city.powers.includes("laws"));
  const decides = city.at_hall;
  const residents = city.citizens.filter((name) => name !== look.identity);
  const vacant = city.lots.filter((lot) => lot.free);

  return (
    <>
    {/* One joins and leaves where the city decides (D-155): the standing of
        the visitor comes before the machinery of the power. */}
    <Citizenship look={look} />
    <section>
      <h2>{t("ui-admin-title-city", { city: city.name })}</h2>
      <nav className="row tabs">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={kind === tab.id ? "" : "quiet"}
            aria-current={kind === tab.id || undefined}
            onClick={() => setKind(tab.id)}
          >
            {t(tab.label)}
          </button>
        ))}
      </nav>

      {kind === "panel" ? (
        <Panel panel={panel} />
      ) : (
        <>
        <Court jobs={jobs} sanctions={sanctions} penalColonies={penalColonies} can={can("justice")} go={go} busy={busy} />
        <Votes
          polls={polls}
          city={city}
          go={go}
          busy={busy}
        />
        <>
          <p className="sign">{t("ui-admin-treasury-sign", { treasury: api.tk(city.treasury) })}</p>
          {city.upkeep && city.upkeep.nodes > 0 && (
            <p className="note">
              {t("ui-admin-upkeep", {
                nodes: String(city.upkeep.nodes),
                energy: city.upkeep.energy.toFixed(0),
                hours: String(city.upkeep.hours),
                tariff: String(city.upkeep.tariff),
                worth: api.tk(city.upkeep.worth),
              })}
            </p>
          )}
          <p className="note">
            {city.powers.length === 0
              ? t("ui-admin-resident")
              : t("ui-admin-your-rights", {
                  rights: city.powers.map(rightName(city)).join(", "),
                })}
            {!decides && city.powers.length > 0 && <b> {t("ui-admin-come-in")}</b>}
          </p>

          <Word
            city={city}
            can={can("citizens") && decides}
            go={go}
            busy={busy}
          />

          <h3>{t("ui-admin-offices")}</h3>
          {city.offices.length === 0 ? (
            <p className="note">{t("ui-admin-offices-none")}</p>
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
                    {t("ui-admin-revoke")}
                  </button>
                )}
              </div>
            ))
          )}

          {can("offices") && decides && residents.length > 0 && (
            <>
              <h3>{t("ui-admin-create-office")}</h3>
              <div className="row">
                <select value={toWhom} onChange={(e) => setToWhom(e.target.value)}>
                  <option value="">{t("ui-admin-whom")}</option>
                  {residents.map((name) => (
                    <option key={name}>{name}</option>
                  ))}
                </select>
                <input
                  value={post}
                  onChange={(e) => setPost(e.target.value)}
                  title={t("ui-admin-post-title")}
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
                  {t("ui-admin-appoint")}
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

          <h3>{t("ui-admin-laws")}</h3>
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
                    <td className="note">
                      {law.own ? t("ui-admin-law-own") : t("ui-admin-law-default")}
                    </td>
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
                          {t("ui-admin-law-accept")}
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="note">{t("ui-admin-laws-note")}</p>

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
              <h3>{t("ui-admin-lots")}</h3>
              <div className="row">
                <select value={plot} onChange={(e) => setPlot(e.target.value)}>
                  <option value="">{t("ui-admin-which-lot")}</option>
                  {vacant.map((lot) => (
                    <option key={lot.key} value={lot.key}>
                      {lot.name} · {t("ui-admin-lot-area", { area: lot.area.toFixed(0) })}
                    </option>
                  ))}
                </select>
                <select value={toWhom} onChange={(e) => setToWhom(e.target.value)}>
                  <option value="">{t("ui-admin-to-whom")}</option>
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
                  {t("ui-admin-allot")}
                </button>
              </div>
            </>
          )}

          {can("treasury") && decides && residents.length > 0 && (
            <>
              <h3>{t("ui-admin-treasury")}</h3>
              <div className="row">
                <select value={toWhom} onChange={(e) => setToWhom(e.target.value)}>
                  <option value="">{t("ui-admin-to-whom")}</option>
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
                        //: A wire value, not a line to read: the treasury
                        //: writes this into the ledger row as its reason, and
                        //: the engine matches on it. Translating it would
                        //: change what the server is told, not what is shown.
                        memo: "выплата",
                      }),
                    )
                  }
                  disabled={busy || !toWhom || amount <= 0}
                >
                  {t("ui-admin-pay")}
                </button>
              </div>
            </>
          )}

          {/* Госзаказ и кредит казне (D-248): решения властью «казна» у себя в
              администрации, как и любая трата. */}
          {can("treasury") && decides && <CityWorks busy={busy} act={act} />}

          <h3>
            {t("ui-admin-charter")}
            <Rule>{t("ui-admin-charter-rule")}</Rule>
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
    </>
  );
}

/** Citizenship: one per person, entry by charter, exit with a delay (D-160).
 *
 * One joins in the administration -- where the city makes every decision
 * (D-155) -- so the section stands in this window, first: for a visitor the
 * question "may I belong here" comes before the machinery of the power. The
 * admission order is always shown: "open", "by application" and "by
 * invitation" behave differently, and the person must understand what to expect.
 */
function Citizenship({ look }: Omit<Props, "busy" | "act">) {
  const session = useSession();
  //: Own waiting and own refusal: joining must not grey out the court and the votes.
  const acting = useActions();
  const { busy, act } = acting;
  const city = look.city ?? null;
  const own = look.citizenship ?? null;
  //: Only in the administration: both joining and leaving are in-person (D-155).
  //: The window itself is opened by the hall on the bench; here only the city is needed.
  if (!city) return null;

  //: The admission order, each by the message that says what to expect.
  const order_: Record<string, string> = {
    open: "ui-admin-admission-open",
    application: "ui-admin-admission-application",
    invite: "ui-admin-admission-invite",
  };
  const admission = t(order_[city.admission] ?? city.admission);
  //: Citizenship taken as a print condition cannot be given up before the term (D-184).
  const linked = Boolean(
    own?.bound_until && new Date(own.bound_until) > new Date(),
  );

  return (
    <section>
      <Refusal of={acting} />
      <h2>{t("ui-admin-citizenship")}</h2>
      {own ? (
        <p>
          {t("ui-admin-citizenship-in")} <b>{own.city}</b>
          {own.leaving_at && (
            <> · {t("ui-admin-citizenship-leaving", { when: when(own.leaving_at) })}</>
          )}
          {/* Обязательство, принятое при печати (D-184): срок виден заранее,
              а не открывается отказом при попытке выйти. */}
          {linked && (
            <> · {t("ui-admin-citizenship-bound", { when: when(own.bound_until) })}</>
          )}
        </p>
      ) : (
        <p className="note">{t("ui-admin-citizenship-none")}</p>
      )}

      <div className="row">
        {city.citizen ? (
          <span className="note">{t("ui-admin-your-city")}</span>
        ) : city.requested ? (
          <span className="note">
            {city.admission === "invite" ? t("ui-admin-invited") : t("ui-admin-applied")}
          </span>
        ) : null}
        {!city.citizen && (
          <button
            onClick={() => act(() => session.send("city.join", {}))}
            disabled={busy || Boolean(own)}
            title={own ? t("ui-admin-join-blocked") : admission}
          >
            {city.requested && city.admission === "invite"
              ? t("ui-admin-accept-invite")
              : t("ui-admin-join")}
          </button>
        )}
        <span className="note">
          {t("ui-admin-admission-line", { city: city.name, order: admission })}
        </span>
      </div>

      {own && !own.leaving_at && (
        <div className="row">
          <button
            onClick={() => act(() => session.send("city.leave", {}))}
            disabled={busy || linked}
            title={linked ? t("ui-admin-leave-bound-title") : t("ui-admin-leave-title")}
          >
            {t("ui-admin-leave")}
          </button>
          <span className="note">
            {linked ? t("ui-admin-leave-bound-note") : t("ui-admin-leave-note")}
          </span>
        </div>
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
  go,
  busy,
}: {
  city: CityView;
  can: boolean;
  go: (what: () => Promise<unknown>) => void;
  busy: boolean;
}) {
  const session = useSession();
  const [text, setText] = useState<string | null>(null);
  const tally = text ?? city.about;

  return (
    <div>
      <h3>{t("ui-admin-word")}</h3>
      {city.about ? (
        <p className="say">«{city.about}»</p>
      ) : (
        <p className="note">{t("ui-admin-word-none")}</p>
      )}
      {can && (
        <>
          <div className="row">
            <textarea
              className="word"
              value={tally}
              maxLength={api.CITY_ABOUT_LIMIT}
              placeholder={t("ui-admin-word-hint")}
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
              {t("ui-admin-word-publish")}
            </button>
            <span className="note">
              {t("ui-admin-word-count", {
                used: String(tally.length),
                limit: String(api.CITY_ABOUT_LIMIT),
              })}
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
  const names = useNames();
  const book = useBook();
  const parsed = parse(value);
  const [item, setItem] = useState("");
  const [rate, setRate] = useState(10);
  const [norm, setNorm] = useState(30);

  //: The player types the Russian word; the law is keyed by the id (D-251).
  //: The synonyms map carries every Russian spelling, and an id passes as is.
  const add = () =>
    apply({
      ...parsed,
      [book?.synonyms?.[item.trim()] ?? item.trim()]: { rate: rate, free: norm },
    });
  const remove = (which: string) => {
    const without = { ...parsed };
    delete without[which];
    apply(without);
  };

  return (
    <div>
      <h3>{name}</h3>
      {Object.keys(parsed).length === 0 ? (
        <p className="note">{t("ui-admin-customs-open")}</p>
      ) : (
        <table>
          <tbody>
            {Object.entries(parsed).map(([which, condition]) => (
              <tr key={which}>
                <td>{goodsName(names, which)}</td>
                <td className="num">{condition.rate}%</td>
                <td className="note">
                  {t("ui-admin-customs-free", { free: String(condition.free) })}
                </td>
                <td>
                  <button className="quiet" onClick={() => remove(which)} disabled={busy}>
                    {t("ui-admin-customs-drop")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="row">
        <input
          list={`goods-${law}`}
          placeholder={t("ui-admin-customs-goods")}
          value={item}
          onChange={(e) => setItem(e.target.value)}
        />
        <datalist id={`goods-${law}`}>
          {goods.map((name) => (
            //: Offered in the player's words; `add` resolves back to the id.
            <option key={name} value={goodsName(names, name)} />
          ))}
        </datalist>
        <input
          type="number"
          value={rate}
          onChange={(e) => setRate(Number(e.target.value))}
          title={t("ui-admin-customs-rate-title")}
        />
        <input
          type="number"
          value={norm}
          onChange={(e) => setNorm(Number(e.target.value))}
          title={t("ui-admin-customs-free-title")}
        />
        <button onClick={add} disabled={busy || !item.trim() || rate <= 0}>
          {t("ui-admin-customs-add")}
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
      <p className="note">{t("ui-admin-scopes-note")}</p>
      <div className="row">
        {/* `POWERS` holds message keys, not words: it is built once at import. */}
        {Object.entries(api.POWERS).map(([key, word]) => (
          <label className="note" key={key} title={can(key) ? "" : t("ui-admin-scopes-lacking")}>
            <input
              type="checkbox"
              checked={selected.includes(key)}
              disabled={!can(key)}
              onChange={() => toggle(key)}
            />{" "}
            {t(word)}
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
  //: Called before the early returns: a hook must run on every render.
  const names = useNames();
  if (!panel) return <p className="note">{t("ui-admin-panel-none")}</p>;
  if (panel.blind) {
    return <p className="trouble">{t("ui-admin-panel-blind")}</p>;
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
        {t("ui-admin-panel-sign", {
          hours: String(panel.window_hours),
          trades: String(market.trades),
          volume: market.volume.toFixed(2),
        })}
        <Rule>{t("ui-admin-panel-rule")}</Rule>
      </p>

      <h3>{t("ui-admin-panel-people")}</h3>
      <p>
        {t("ui-admin-panel-people-line", {
          here: String(people.here),
          printed: String(people.printed),
        })}
      </p>

      <h3>{t("ui-admin-panel-energy")}</h3>
      <p>
        {t("ui-admin-panel-energy-line", {
          stored: energy.stored.toFixed(0),
          tariff: String(energy.tariff),
          work: energy.spent_work.toFixed(0),
          home: energy.spent_home.toFixed(0),
        })}
      </p>

      <h3>{t("ui-admin-panel-border")}</h3>
      <p>
        {t("ui-admin-panel-border-line", {
          imported: weighed(border.imported, names),
          exported: weighed(border.exported, names),
        })}
      </p>
      <p className="note">
        {t("ui-admin-panel-trips", {
          in: String(border.trips_in),
          out: String(border.trips_out),
          duty: border.duty_collected.toFixed(2),
        })}
      </p>

      <h3>{t("ui-admin-panel-production")}</h3>
      <p>
        {t("ui-admin-panel-production-line", {
          mined: (work.mined?.["total"] ?? 0).toFixed(1),
          harvested: (work.harvested ?? 0).toFixed(1),
          crafted:
            Object.entries(work.crafted ?? {})
              .map(([name, qty]) => `${goodsName(names, name)} ${qty.toFixed(0)}`)
              .join(", ") || "—",
        })}
      </p>

      <h3>{t("ui-admin-panel-prices")}</h3>
      {prices.length === 0 ? (
        <p className="note">{t("ui-admin-panel-no-trades")}</p>
      ) : (
        <table>
          <tbody>
            {prices.map(([name, price]) => (
              <tr key={name}>
                <td>{goodsName(names, name)}</td>
                <td className="num">{price.toFixed(2)} ₭</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <h3>{t("ui-admin-panel-goods")}</h3>
      <table>
        <tbody>
          {goods.map(([name, qty]) => (
            <tr key={name}>
              <td>{goodsName(names, name)}</td>
              <td className="num">{qty.toFixed(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {panel.treasury ? (
        <>
          <h3>{t("ui-admin-panel-treasury")}</h3>
          <p>{t("ui-admin-panel-balance", { balance: panel.treasury.balance.toFixed(2) })}</p>
          <p className="note">
            {t("ui-admin-panel-collected", { lines: ledger(panel.treasury.collected) })}
          </p>
          <p className="note">
            {t("ui-admin-panel-spent", { lines: ledger(panel.treasury.spent) })}
          </p>
        </>
      ) : (
        <p className="note">{t("ui-admin-panel-treasury-closed")}</p>
      )}
    </div>
  );
}

/** Goods and their weight, in the player's words: what crossed the border. */
function weighed(rows: Record<string, number>, names: ReturnType<typeof useNames>): string {
  return (
    Object.entries(rows)
      .map(([id, kg]) =>
        t("ui-admin-panel-kg", { goods: goodsName(names, id), kg: kg.toFixed(1) }),
      )
      .join(", ") || "—"
  );
}

/** Treasury lines by their ground: what was collected, what was spent. */
function ledger(rows: Record<string, number>): string {
  return (
    Object.entries(rows)
      .map(([ground, qty]) =>
        t("ui-admin-panel-ledger-line", {
          ground: groundName(ground),
          amount: qty.toFixed(2),
        }),
      )
      .join(", ") || "—"
  );
}

/** A right in words. A narrow one is shown by its law's name. */
const rightName = (city: CityView) => (right: string) => {
  if (right.startsWith(api.LAW_SCOPE)) {
    const key = right.slice(api.LAW_SCOPE.length);
    return city.laws[key]?.name ?? key;
  }
  //: A right the map does not know shows itself: the scope key is honest.
  const word = api.POWERS[right];
  return word ? t(word) : right;
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
  go,
  busy,
}: {
  polls: CityVote[];
  city: CityView;
  go: (what: () => Promise<unknown>) => Promise<void>;
  busy: boolean;
}) {
  const session = useSession();
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
  //: The bar a poll passes at, each by the message that names it.
  const threshold: Record<string, string> = {
    simple: "ui-admin-threshold-simple",
    two_thirds: "ui-admin-threshold-two-thirds",
    unanimous: "ui-admin-threshold-unanimous",
  };

  return (
    <>
      <h3>
        {t("ui-admin-votes")}
        <Rule>{t("ui-admin-votes-rule")}</Rule>
      </h3>
      {(elective || recallable || byCouncil) && (
        <div className="row">
          {elective && !running("election") && (
            <button
              onClick={() => go(() => session.send("city.election"))}
              disabled={busy}
            >
              {t("ui-admin-call-election")}
            </button>
          )}
          {city.charter?.council_exists === "elected" && !running("council") && (
            <button
              className="quiet"
              onClick={() => go(() => session.send("city.council_election"))}
              disabled={busy}
            >
              {t("ui-admin-call-council")}
            </button>
          )}
          {recallable && !running("recall") && (
            <button
              className="quiet"
              onClick={() => go(() => session.send("city.recall"))}
              disabled={busy}
            >
              {t("ui-admin-call-recall")}
            </button>
          )}
          <span className="note">{t("ui-admin-votes-note")}</span>
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
                    {convening.kind === "council"
                      ? t("ui-admin-vote-council")
                      : t("ui-admin-vote-ruler")}
                    <span className="note">
                      {" "}
                      {convening.candidates.length === 0
                        ? t("ui-admin-vote-no-candidates")
                        : `· ${convening.candidates
                            .map((k) => `${k.name} (${k.votes})`)
                            .join(", ")}`}
                    </span>
                  </>
                ) : convening.kind === "recall" ? (
                  t("ui-admin-vote-recall")
                ) : convening.kind === "charter" ? (
                  <>
                    {t("ui-admin-vote-charter")}
                    {/* The player is not shown the name of the charter question
                        the threshold comes from: that is a key out of the vault,
                        and the sentence around it was written for whoever wrote
                        the code. What matters here is that the charter sets its
                        own bar for changing itself. */}
                    <span className="note"> {t("ui-admin-vote-charter-note")}</span>
                  </>
                ) : (
                  <>
                    {convening.law}
                    <span className="note"> → {String(convening.value)}</span>
                  </>
                )}
              </td>
              <td className="note">
                {convening.voters === "council" && `${t("ui-admin-vote-by-council")} · `}
                {/* An election is not a for-or-against poll: every ballot in it
                    names a candidate, so `no` is always zero there and "против
                    0" read as "nobody objects" rather than as a word that does
                    not apply. The candidates carry their own counts above. */}
                {convening.kind === "election" || convening.kind === "council"
                  ? t("ui-admin-vote-turnout", {
                      yes: String(convening.yes),
                      of: String(convening.electorate),
                    })
                  : t("ui-admin-vote-tally", {
                      yes: String(convening.yes),
                      no: String(convening.no),
                      of: String(convening.electorate),
                    })}
                {" · "}
                {convening.threshold in threshold
                  ? t(threshold[convening.threshold])
                  : convening.threshold}
                {convening.quorum > 0 &&
                  ` ${t("ui-admin-vote-quorum", { quorum: String(convening.quorum) })}`}
              </td>
              <td className="note">
                {t("ui-admin-vote-closes", { when: when(convening.closes_at) })}
              </td>
              <td>
                {convening.kind === "election" || convening.kind === "council" ? (
                  <>
                    {/* Standing twice is refused, so it is not offered twice. */}
                    {!convening.candidates.some((one) => one.own) && (
                      <button
                        className="quiet"
                        onClick={() =>
                          go(() => session.send("city.nominate", { vote: convening.id }))
                        }
                        disabled={busy}
                        title={t("ui-admin-nominate-title")}
                      >
                        {t("ui-admin-nominate")}
                      </button>
                    )}
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
                        {t("ui-admin-vote-for", { name: candidate.name })}
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
                      {t("ui-admin-vote-yes")}
                    </button>
                    <button
                      className={convening.mine === false ? "" : "quiet"}
                      onClick={() =>
                        go(() => session.send("city.vote", { vote: convening.id, yes: false }))
                      }
                      disabled={busy}
                    >
                      {t("ui-admin-vote-no")}
                    </button>
                  </>
                ) : (
                  <span className="note">{t("ui-admin-vote-none")}</span>
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

/**
 * A sanction in the player's words. The names come with the list the picker is
 * built from, so a passed sentence is read the same way it was chosen: without
 * this the decided case printed the engine's key -- `приговор: fine` -- in a
 * row where the very next line offered «Штраф».
 */
function named(sanctions: SanctionKind[], kind: string | null | undefined): string {
  return sanctions.find((one) => one.id === kind)?.name ?? kind ?? "—";
}

function Court({
  jobs,
  sanctions,
  penalColonies,
  can,
  go,
  busy,
}: {
  jobs: CourtCase[];
  sanctions: SanctionKind[];
  penalColonies: { key: string; name: string }[];
  can: boolean;
  go: (what: () => Promise<unknown>) => Promise<void>;
  busy: boolean;
}) {
  const session = useSession();
  const [toWhom, setToWhom] = useState("");
  const [essence, setEssence] = useState("");
  const [sanction, setSanction] = useState("fine");
  const [qty, setQty] = useState(10);
  const [penalColony, setPenalColony] = useState("");
  const open = jobs.filter((job) => job.state === "open");
  if (jobs.length === 0 && !can) return null;

  return (
    <>
      <h3>{t("ui-admin-court")}</h3>
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
                    ? t("ui-admin-case-open")
                    : job.state === "judged"
                      ? t("ui-admin-case-judged", { sanction: named(sanctions, job.verdict) })
                      : /* A dismissal carries the judge's own words when there
                           are any, and the engine's own "отказано" when there
                           are none -- and repeating that after the colon read
                           "отказано: отказано". The comparison is against what
                           the engine writes into the row, so it stays a literal:
                           it is a wire value, not a line to read. */
                        job.verdict && job.verdict !== "отказано"
                        ? t("ui-admin-case-dismissed-why", { why: job.verdict })
                        : t("ui-admin-case-dismissed")}
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
                            {kind.enforced ? "" : ` ${t("ui-admin-sanction-unenforced")}`}
                          </option>
                        ))}
                      </select>
                      <input
                        type="number"
                        min={0}
                        value={qty}
                        onChange={(e) => setQty(Number(e.target.value))}
                        title={t("ui-admin-fine-title")}
                      />
                      {/* Куда сажать — решает суд (D-176): каторга одна —
                          очевидно, несколько — судья называет которую. */}
                      {sanction === "prison" && penalColonies.length > 1 && (
                        <select
                          value={penalColony}
                          onChange={(e) => setPenalColony(e.target.value)}
                          title={t("ui-admin-prison-title")}
                        >
                          <option value="">{t("ui-admin-prison-pick")}</option>
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
                        {t("ui-admin-verdict")}
                      </button>
                      <button
                        className="quiet"
                        onClick={() =>
                          go(() => session.send("city.judge", { case: job.id }))
                        }
                        disabled={busy}
                      >
                        {t("ui-admin-dismiss")}
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
          placeholder={t("ui-admin-sue-whom")}
        />
        <input
          value={essence}
          onChange={(e) => setEssence(e.target.value)}
          placeholder={t("ui-admin-sue-claim")}
        />
        <button
          onClick={() =>
            go(() => session.send("city.sue", { who: toWhom, claim: essence }))
          }
          disabled={busy || !toWhom.trim() || !essence.trim()}
        >
          {t("ui-admin-sue")}
        </button>
        <span className="note">{t("ui-admin-sue-note")}</span>
      </div>
      {open.length > 0 && !can && (
        <p className="note">{t("ui-admin-court-queue", { count: String(open.length) })}</p>
      )}
    </>
  );
}
