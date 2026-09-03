// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Bank: rate, own loans, credit and repayment (D-030, D-087, D-167).
 *
 * The rate is shown together with an explanation of where it came from: the
 * algorithm must be not only deterministic but readable -- otherwise there is
 * nothing to argue monetary policy with. Reserve and circulating supply are
 * public for the same reason.
 *
 * Lives in the "финансы" tab next to the account and the statement (D-190):
 * borrowing, repaying and paying are one kind of thing, and none of them is
 * about the energy meter it used to sit beside.
 *
 * Reading it publicly is a separate job from computing it publicly. Five
 * figures each trailing its own composed sentence made a wall of grey type in
 * which the figures themselves were the hardest thing to find -- so the panel
 * is four blocks told in order (the world's money, your debts, what you may
 * take, who sets the rate), every figure alone in its row, and every
 * explanation broken back into the clauses the engine joined it from.
 */

import { useCallback, useEffect, useState } from "react";
import { useNames, useSession } from "../actions";
import * as api from "../api";
import { buildingKindName, goodsName } from "../names";
import { span, when } from "../clock";
import { Rule } from "../Rule";
import { t } from "../locale";
import { NumberField } from "../NumberField";

type Props = {
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

/** The grounds for a figure, clause by clause (D-030).
 *
 * Every number the bank shows arrives with its reason, and the reason is
 * several facts rather than one: «база 900 ₭», «оборот 1 200 ₭ за 7 суток»,
 * «возвращено ранее 0 ₭». Laid beside the number as a paragraph it read as
 * prose about a number rather than the arithmetic of it; one fact per line,
 * it reads as the sum it is.
 *
 * They arrive as a list and are drawn as one (D-251 wave IV). This used to
 * split a rendered sentence back apart on the semicolon the server put in,
 * which only ever worked by luck: the clauses carry the decimal commas and
 * punctuation of their own language, and which mark separates a list is the
 * language's business rather than Python's.
 *
 * The vault's reference the engine signs some of its clauses with is dropped
 * on the way: a decision code is how we argue about the rule, not how a player
 * reads it -- and standing alone at the end of a short clause it is the loudest
 * thing in the list.
 */
function Why({ said }: { said?: string[] | null }) {
  //: A list or nothing: the field is read off the socket, and a server that
  //: still sends the old sentence must leave the panel standing.
  const clauses = (Array.isArray(said) ? said : [])
    .map((clause) => String(clause).replace(/\s*\(D-\d+\)/g, "").trim())
    .filter(Boolean);
  if (clauses.length === 0) return null;
  return (
    <ul className="why">
      {clauses.map((clause, index) => (
        <li key={index}>{clause}</li>
      ))}
    </ul>
  );
}

export function Bank({ busy, act }: Props) {
  const session = useSession();
  const [bank, setBank] = useState<any>(null);
  const [qty, setQty] = useState(50);

  const refresh = useCallback(async () => {
    try {
      //: Ставку просим под ту сумму, которую человек собрался брать: линия
      //: города конечна, и после неё цена другая (D-193).
      setBank(await session.send("bank.view", { amount: qty }));
    } catch {
      setBank(null);
    }
  }, [session, qty]);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (!bank) return null;
  const go = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await refresh();
    });

  const loans = (bank.loans ?? []) as any[];

  return (
    <>
      {/* Блок читается сверху вниз как рассказ: как стоят деньги в мире, что
          вы должны, что можете взять и кто назначает ставку. Раньше это было
          пять абзацев вперемешку, и число в каждом терялось в своём же
          объяснении. */}
      <h3>
        {t("ui-bank-title")}
        <Rule>{t("ui-bank-rule")}</Rule>
      </h3>
      <div className="facts">
        <div className="fact">
          <span className="fact-name">{t("ui-bank-rate")}</span>
          <span className="fact-val lead">{Number(bank.rate).toFixed(2)}%</span>
          <Why said={bank.why} />
        </div>
        <div className="fact">
          <span className="fact-name">{t("ui-bank-circulating")}</span>
          <span className="fact-val">{api.tk(bank.circulating)} ₭</span>
        </div>
        <div className="fact">
          <span className="fact-name">{t("ui-bank-reserve")}</span>
          <span className="fact-val">{api.tk(bank.reserve)} ₭</span>
        </div>
        {/* Фонд работ (D-248): куда возвращается процентный доход и откуда
            платится госзаказ. Публичен, как резерв. */}
        <div className="fact">
          <span className="fact-name">{t("ui-bank-fund")}</span>
          <span className="fact-val">{api.tk(bank.fund)} ₭</span>
        </div>
      </div>

      <Board />

      {loans.length > 0 && (
        <>
          <p className="sub">{t("ui-bank-debts")}</p>
          <div className="facts">
            {loans.map((loan) => (
              <div className="fact" key={loan.id}>
                <span className="fact-name">{t("ui-bank-outstanding")}</span>
                <span className="fact-val lead">{api.tk(loan.outstanding)} ₭</span>
                <p className="note">
                  {t("ui-bank-loan", {
                    principal: api.tk(loan.principal),
                    rate: Number(loan.rate).toFixed(1),
                    taken: when(loan.taken_at),
                  })}
                </p>
                <button
                  className="quiet"
                  onClick={() => go(() => session.send("bank.repay", { loan: loan.id }))}
                  disabled={busy}
                >
                  {t("ui-bank-repay")}
                </button>
              </div>
            ))}
          </div>
        </>
      )}

      <p className="sub">{t("ui-bank-borrow-title")}</p>
      <div className="facts">
        <div className="fact">
          <span className="fact-name">{t("ui-bank-limit")}</span>
          <span className="fact-val lead">{api.tk(bank.limit)} ₭</span>
          <Why said={bank.limit_why} />
        </div>
        {/* Своя ставка называется до кнопки, а не после (D-193): она зависит
            от запрошенной суммы, поэтому стоит вплотную к полю. Пусто — ставки
            нет вовсе: занимают только у города своего гражданства (D-281), и
            «0,00 %» рядом с «кредита нет» читалось бы как даровые деньги. */}
        {bank.your_rate !== undefined && (
          <div className="fact">
            <span className="fact-name">{t("ui-bank-your-rate")}</span>
            <span className="fact-val lead">
              {bank.your_rate === null ? "—" : `${Number(bank.your_rate).toFixed(2)}%`}
            </span>
            <Why said={bank.your_rate_why} />
          </div>
        )}
      </div>
      <div className="form">
        <label>
          <span>{t("ui-bank-amount")}</span>
          <NumberField
            min={1}
            value={qty}
            onChange={(typed) => setQty(typed ?? 0)}
          />
        </label>
        <button
          onClick={() => go(() => session.send("bank.borrow", { amount: qty }))}
          disabled={busy || qty <= 0}
        >
          {t("ui-bank-borrow")}
        </button>
      </div>

      <Council busy={busy} act={act} />
    </>
  );
}

/** The state order board (D-248): what the fund pays for right now.
 *
 * Reference, not a form: the orders are taken by doing the work -- mending
 * the edge, raising the house, pouring the fuel -- and the engine pays
 * whoever it verified first. The board only says what is bought and for how
 * much.
 */
const ORDER_KINDS: Record<string, string> = {
  road_mend: "ui-bank-order-road-mend",
  building_repair: "ui-bank-order-building-repair",
  building_build: "ui-bank-order-building-build",
  fuel_delivery: "ui-bank-order-fuel-delivery",
};

function Board() {
  const session = useSession();
  const names = useNames();
  const [orders, setOrders] = useState<api.WorksOrder[] | null>(null);

  useEffect(() => {
    void session
      .send("works.board", {})
      .then((board) => setOrders((board as api.WorksBoard).orders ?? []))
      .catch(() => setOrders(null));
  }, [session]);

  if (!orders || orders.length === 0) return null;
  return (
    <>
      <p className="sub">{t("ui-bank-works")}</p>
      <table>
        <tbody>
          {orders.map((order) => {
            const about = order.about ?? {};
            const place = order.between
              ? `${order.between[0]} — ${order.between[1]}`
              : (order.node ?? "");
            const detail =
              order.kind === "fuel_delivery"
                ? t("ui-bank-fuel-left", {
                    goods: goodsName(names, about.type_key ?? ""),
                    left: Number(about.left ?? 0).toFixed(0),
                  })
                : order.kind === "building_build"
                  ? t("ui-bank-building", {
                      kind: buildingKindName(names, about.building_kind ?? ""),
                      footprint: String(about.footprint),
                      floors: String(about.floors),
                    })
                  : "";
            return (
              <tr key={order.id}>
                <td className="note">
                  {/* An unknown kind still reads as itself: `t` falls back to
                      the key it was given, which here is the wire word. */}
                  {t(ORDER_KINDS[order.kind] ?? order.kind)} · {place}
                  {detail ? ` · ${detail}` : ""}
                </td>
                <td>{api.tk(order.tariff)} ₭</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}

/** The Council of cities and the rate (D-087, D-172).
 *
 * While there are fewer cities with an administration than the threshold, the
 * algorithm computes the rate, and this window just says how many are left.
 * After the threshold -- the city's vote in the corridor around the
 * recommendation: the Council argues with the algorithm, not replaces it.
 */
function Council({ busy, act }: Props) {
  const session = useSession();
  const [council, setCouncil] = useState<any>(null);
  const [rate, setRate] = useState<number | null>(null);

  useEffect(() => {
    void session.send("bank.council").then(setCouncil).catch(() => setCouncil(null));
  }, [session]);

  if (!council) return null;
  if (!council.council_decides) {
    return (
      <>
        <p className="sub">{t("ui-bank-council")}</p>
        <p className="note">
          {council.locked_until
            ? t("ui-bank-council-locked", {
                //: `when` says «через столько-то»; here the words around it
                //: already say «ещё на», so only the span itself is wanted.
                left: span(council.locked_until),
              })
            : t("ui-bank-council-waiting", {
                cities: String(council.cities_with_hall),
                needed: String(council.handover_at),
              })}
        </p>
      </>
    );
  }

  const desired = rate ?? Number(council.advised);
  return (
    <>
      <p className="sub">{t("ui-bank-council")}</p>
      <div className="form">
        <label>
          <span>
            {t("ui-bank-council-rate", {
              corridor: String(council.corridor),
              advised: Number(council.advised).toFixed(2),
            })}
          </span>
          <NumberField
            step={0.5}
            min={Number(council.advised) - Number(council.corridor)}
            max={Number(council.advised) + Number(council.corridor)}
            value={desired}
            onChange={(typed) => setRate(typed ?? 0)}
          />
        </label>
        <button
          onClick={() => act(() => session.send("bank.council_rate", { rate: desired }))}
          disabled={busy}
        >
          {t("ui-bank-council-vote")}
        </button>
      </div>
      <ul className="why">
        <li>{t("ui-bank-council-advises", { advised: Number(council.advised).toFixed(2) })}</li>
        <li>{t("ui-bank-council-corridor", { corridor: String(council.corridor) })}</li>
        <li>{t("ui-bank-council-voter")}</li>
      </ul>
    </>
  );
}
