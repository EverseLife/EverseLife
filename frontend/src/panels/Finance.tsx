// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Finance: the account, the statement, transfers and the bank (D-190, D-167).
 *
 * The whole tab is the Network (D-044): money is paid, borrowed and repaid
 * from anywhere, unlike matter. It used to live inside "хозяйство" next to
 * meter bills, and the bank was buried under the energy grid -- yet the two
 * have nothing in common but the word "payment".
 *
 * The statement is here for the same reason a bank shows one: a balance
 * without a history is a number nobody can argue with.
 */

import { useCallback, useEffect, useState } from "react";
import { useSession } from "../actions";
import type { Look } from "../api";
import { when } from "../clock";
import { Bank } from "./Bank";
import { Rule } from "../Rule";

type Props = {
  look: Look;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

/** One line of the statement, as the server sends it. */
type Entry = {
  at: string;
  reason: string;
  amount: number;
  money: string;
  incoming: boolean;
  memo: Record<string, string>;
  with: string | null;
};

/** Grounds in words: the player reads the statement, not the enum. */
const REASON: Record<string, string> = {
  genesis: "эмиссия",
  trade: "сделка",
  tax_trade: "налог с продажи",
  market_fee: "сбор рынка",
  duty: "пошлина",
  salary: "жалованье",
  upkeep: "содержание",
  energy_bill: "энергия",
  court_fee: "пошлина суда",
  fine: "штраф",
  escrow_hold: "задаток",
  escrow_release: "возврат задатка",
  loan: "кредит",
  loan_repayment: "погашение",
  seigniorage: "сеньораж",
  bank_margin: "маржа города",
  transfer: "перевод",
};

export function Finance({ look, busy, act }: Props) {
  const session = useSession();
  const [entries, setEntries] = useState<Entry[]>([]);
  const [to, setTo] = useState("");
  const [amount, setAmount] = useState(10);
  const [memo, setMemo] = useState("");

  const reload = useCallback(async () => {
    try {
      const answer = await session.send("finance.statement");
      setEntries((answer.entries as Entry[]) ?? []);
    } catch {
      setEntries([]);
    }
  }, [session]);

  useEffect(() => {
    void reload();
  }, [reload, look.money]);

  const go = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await reload();
    });

  return (
    <div>
      <h3>
        Счёт
        <Rule>
          Счёт переживает смерть тела: деньги в Сети, а не в кармане.
        </Rule>
      </h3>
      <p className="sign money">{look.money} ₭</p>

      <h3>
        Перевести
        <Rule>
          Перевод идёт без комиссии и налога — и отменить его нельзя. Основание видят
          получатель и суд: это единственное, что останется от сделки, если о ней
          придётся спорить.
        </Rule>
      </h3>
      {/* Поля во всю ширину и подписаны сверху: имя личности длиннее, чем
          остаток строки после поля суммы, а подсказка внутри поля исчезает
          ровно в тот момент, когда по ней сверяют написанное. */}
      <div className="form">
        <label>
          <span>кому</span>
          <input
            value={to}
            onChange={(e) => setTo(e.target.value)}
            placeholder="имя личности"
          />
        </label>
        <label>
          <span>сколько, ₭</span>
          <input
            type="number"
            min={0}
            step="0.01"
            value={amount}
            onChange={(e) => setAmount(Number(e.target.value))}
          />
        </label>
        <label>
          <span>за что</span>
          <input
            value={memo}
            onChange={(e) => setMemo(e.target.value)}
            placeholder="видно получателю и суду"
            maxLength={140}
          />
        </label>
        <button
          onClick={() =>
            go(async () => {
              await session.send("finance.transfer", { to, amount, memo });
              setMemo("");
            })
          }
          disabled={busy || !to.trim() || amount <= 0}
        >
          Перевести
        </button>
      </div>

      <h3>Выписка</h3>
      {entries.length === 0 ? (
        <p className="note">операций пока нет</p>
      ) : (
        <div className="facts">
          {entries.map((entry, index) => (
            <div className="fact" key={`${entry.at}-${index}`}>
              <span className="fact-name">{REASON[entry.reason] ?? entry.reason}</span>
              <span className="fact-val">
                {entry.incoming ? "+" : "−"}
                {entry.money} ₭
              </span>
              <p className="note">
                {entry.with ?? "—"}
                {entry.memo?.["основание"] ? ` · ${entry.memo["основание"]}` : ""}
                {` · ${when(entry.at)}`}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* Кредит — тоже Сеть: берут и гасят откуда угодно (D-167). */}
      <Bank busy={busy} act={act} />
    </div>
  );
}
