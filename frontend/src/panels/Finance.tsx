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
import type { Look, Session } from "../api";
import { when } from "../clock";
import { Bank } from "./Bank";

type Props = {
  look: Look;
  session: Session;
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

export function Finance({ look, session, busy, act }: Props) {
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
      <h3>Счёт</h3>
      <p className="sign">{look.money} ₭</p>

      <h3>Перевести</h3>
      <div className="row">
        <input
          value={to}
          onChange={(e) => setTo(e.target.value)}
          placeholder="кому"
          title="имя личности"
        />
        <input
          type="number"
          min={0}
          step="0.01"
          value={amount}
          onChange={(e) => setAmount(Number(e.target.value))}
          title="сколько, ₭"
        />
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
      <div className="row">
        <input
          className="wide-input"
          value={memo}
          onChange={(e) => setMemo(e.target.value)}
          placeholder="за что (видно получателю и суду)"
          maxLength={140}
        />
      </div>
      <p className="note">
        Без комиссии и налога; отменить перевод нельзя (D-190).
      </p>

      <h3>Выписка</h3>
      {entries.length === 0 ? (
        <p className="note">операций пока нет</p>
      ) : (
        <table>
          <tbody>
            {entries.map((entry, index) => (
              <tr key={`${entry.at}-${index}`}>
                <td>
                  {entry.incoming ? "+" : "−"}
                  {entry.money} ₭
                  <span className="note"> · {REASON[entry.reason] ?? entry.reason}</span>
                </td>
                <td className="note">
                  {entry.with ?? "—"}
                  {entry.memo?.["основание"] ? ` · ${entry.memo["основание"]}` : ""}
                </td>
                <td className="note">{when(entry.at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* Кредит — тоже Сеть: берут и гасят откуда угодно (D-167). */}
      <Bank session={session} busy={busy} act={act} />
      <p className="note">
        Счёт переживает смерть тела: деньги в Сети, а не в кармане (D-012).
      </p>
    </div>
  );
}
