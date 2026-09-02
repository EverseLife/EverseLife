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
 *
 * What is left in this file is the window itself: one survey of the city, and
 * the decisions taken directly against it -- offices, laws, lots, payments,
 * charter. Everything with a life of its own lives in `panels/admin/`: the
 * standing of a visitor (`Citizenship`), the two procedures that answer to the
 * charter rather than to a right (`Votes`, `Court`), the editors of the values
 * a plain input cannot hold (`Word`, `Customs`, `Scopes`), and the figures
 * anybody may read from anywhere (`Panel`).
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
import { t } from "../locale";
import { Rule } from "../Rule";
import { Refusal, useActions, useBook, useEdition, useNames, useSession } from "../actions";
import { lawName, lawNote, lawOption, lawUnit, type Names } from "../names";
import { when } from "../clock";
import { CityWorks } from "./CityWorks";
import { Citizenship } from "./admin/Citizenship";
import { Court } from "./admin/Court";
import { Customs } from "./admin/Customs";
import { Panel } from "./admin/Panel";
import { Scopes } from "./admin/Scopes";
import { Votes } from "./admin/Votes";
import { Word } from "./admin/Word";

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
  const book = useBook();
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
  //: Which laws are a choice, and of what. Catalog constants live in
  //: `/public` rather than in the city's answer (D-225), and they change only
  //: when the vault does -- so one read on mount is the whole of it.
  const [choices, setChoices] = useState<Record<string, string[]>>({});
  useEffect(() => {
    let alive = true;
    void api
      .laws()
      .then((book) => {
        if (!alive) return;
        setChoices(
          Object.fromEntries(
            book.code_laws
              .filter((one) => one.options?.length)
              .map((one) => [one.id, (one.options ?? []).map((option) => option.id)]),
          ),
        );
      })
      //: A law book that did not arrive costs the picker, not the window: the
      //: field falls back to what it always was, a line of text.
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);
  const [toWhom, setToWhom] = useState("");
  const [post, setPost] = useState(t("ui-admin-post-default"));
  const [rights, setRights] = useState<string[]>(["dashboard"]);
  const [amount, setAmount] = useState(0);
  const [print, setPrint] = useState(0);
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

  //: Another hand's signature or a print elsewhere in the hall moves the
  //: counter this window shows (D-226): reread when the server says so.
  const names = useNames();
  const edition = useEdition("emission.", "city.");
  useEffect(() => {
    void reload();
  }, [reload, look.node?.key, edition]);

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
  //: Named before the JSX: inside a closure TypeScript forgets the narrowing,
  //: and a `?.` there could send an empty proposal to the server.
  const proposalId = city.emission?.proposal?.id ?? "";
  //: The share is the vault's number (D-225): the rule says it, not a copy.
  const share = Number(book?.constants?.["emission.signature_share"] ?? 0);
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
                  rights: city.powers.map(rightName(names)).join(", "),
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
                    · {office.powers.map(rightName(names)).join(", ")}
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
                    <td title={lawNote(names, key) ?? ""}>
                      {lawName(names, key)}
                      {lawUnit(names, key) && (
                        <span className="note"> · {lawUnit(names, key)}</span>
                      )}
                    </td>
                    <td className="num">
                      {!editing ? (
                        //: A choice is shown as the word for it, a number as
                        //: itself: the stored value of a choice is a key now,
                        //: and «citizens» is not something to read off a table.
                        //: `lawOption` gives the word where the table knows
                        //: one and the value itself where it does not, so a
                        //: choice and a number need no telling apart here.
                        <b>{law.value ? lawOption(names, key, law.value) : "—"}</b>
                      ) : choices[key] ? (
                        <select
                          value={edit[key] ?? law.value ?? ""}
                          onChange={(e) =>
                            setEdit((before) => ({ ...before, [key]: e.target.value }))
                          }
                        >
                          {choices[key].map((option) => (
                            <option key={option} value={option}>
                              {lawOption(names, key, option)}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <input
                          value={edit[key] ?? law.value ?? ""}
                          onChange={(e) =>
                            setEdit((before) => ({ ...before, [key]: e.target.value }))
                          }
                          size={10}
                        />
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
                name={lawName(names, key)}
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

          {/* Emission by signatures (D-270): the capital prints into its own
              treasury when the vault's share of the right's holders signed.
              The live proposal is shown to whoever came in; the hands to
              those who hold the right, in the hall. */}
          {city.capital && city.emission && (can("emission") || city.emission.proposal) && (
            <>
              <h3>
                {t("ui-emission-title")}
                <Rule>{t("ui-emission-rule", { share: share.toFixed(0) })}</Rule>
              </h3>
              <p className="note">
                {t("ui-emission-holders", {
                  holders: city.emission.holders,
                  needed: city.emission.needed,
                })}
              </p>
              {city.emission.proposal ? (
                <div className="row">
                  <span>
                    {t("ui-emission-proposal", {
                      money: api.tk(city.emission.proposal.money),
                      who: city.emission.proposal.who,
                      signed: city.emission.proposal.signed,
                      needed: city.emission.needed,
                      until: when(city.emission.proposal.expires_at),
                    })}
                  </span>
                  {city.emission.proposal.mine ? (
                    <span className="note">{t("ui-emission-signed")}</span>
                  ) : (
                    can("emission") &&
                    decides && (
                      <button
                        onClick={() =>
                          go(() => session.send("city.emission_sign", { proposal: proposalId }))
                        }
                        disabled={busy}
                      >
                        {t("ui-emission-sign")}
                      </button>
                    )
                  )}
                </div>
              ) : (
                can("emission") &&
                decides && (
                  <div className="row">
                    <label>
                      <span>{t("ui-emission-amount")}</span>
                      <input
                        type="number"
                        min={1}
                        value={print}
                        onChange={(e) => setPrint(Number(e.target.value))}
                      />
                    </label>
                    <button
                      onClick={() =>
                        go(() => session.send("city.emission_propose", { amount: print }))
                      }
                      disabled={busy || print <= 0}
                    >
                      {t("ui-emission-print")}
                    </button>
                  </div>
                )
              )}
            </>
          )}

          {/* Госзаказ и кредит казне (D-248): решения властью «казна» у себя в
              администрации, как и любая трата. */}
          {can("treasury") && decides && (
            <CityWorks busy={busy} act={act} capital={Boolean(city.capital)} />
          )}

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

/** A right in words. A narrow one is shown by its law's name. */
const rightName = (names: Names | null) => (right: string) => {
  if (right.startsWith(api.LAW_SCOPE)) {
    return lawName(names, right.slice(api.LAW_SCOPE.length));
  }
  //: A right the map does not know shows itself: the scope key is honest.
  const word = api.POWERS[right];
  return word ? t(word) : right;
};
