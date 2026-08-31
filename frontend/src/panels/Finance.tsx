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
import { Glyph } from "../Glyph";
import { groundName } from "../grounds";
import { groundGlyph } from "../marks";
import { Bank } from "./Bank";
import { Rule } from "../Rule";
import { t } from "../locale";

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
  /** The other side's own name, when it has one: a person, or a city. */
  with: string | null;
  /**
   * What kind of side it is, when it is not a person -- `genesis`,
   * `bank_reserve`, `works_fund`. The server sends the enum and the word for
   * it comes out of the locale (D-251): it used to send «резерв банка» ready
   * made, and the two members nobody had written a word for arrived as their
   * own code in the middle of the statement.
   */
  side: string | null;
};

/**
 * Who stood on the other side of the posting, as a person reads it.
 *
 * A person is named and there is nothing to translate. An institution is not:
 * the server sends what kind of side it was and the word comes from the
 * locale, so the same row reads «резерв банка» to one player and "the reserve"
 * to another. A city treasury is both at once -- a kind, with a name in it.
 */
function counterparty(entry: Entry): string {
  if (!entry.side) return entry.with ?? "—";
  return t(`ledger-side-${entry.side}`, {
    //: A variant key is an identifier, never a boolean: the flag says whether
    //: there is a name to put in, and the name travels beside it.
    named: entry.with ? "true" : "false",
    name: entry.with ?? "",
  });
}

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
        {t("ui-finance-account")}
        <Rule>{t("ui-finance-account-rule")}</Rule>
      </h3>
      <p className="sign money">{look.money} ₭</p>

      <h3>
        {t("ui-finance-transfer-title")}
        <Rule>{t("ui-finance-transfer-rule")}</Rule>
      </h3>
      {/* Поля во всю ширину и подписаны сверху: имя личности длиннее, чем
          остаток строки после поля суммы, а подсказка внутри поля исчезает
          ровно в тот момент, когда по ней сверяют написанное. */}
      <div className="form">
        <label>
          <span>{t("ui-finance-to")}</span>
          <input
            value={to}
            onChange={(e) => setTo(e.target.value)}
            placeholder={t("ui-finance-to-hint")}
          />
        </label>
        <label>
          <span>{t("ui-finance-amount")}</span>
          <input
            type="number"
            min={0}
            step="0.01"
            value={amount}
            onChange={(e) => setAmount(Number(e.target.value))}
          />
        </label>
        <label>
          <span>{t("ui-finance-memo")}</span>
          <input
            value={memo}
            onChange={(e) => setMemo(e.target.value)}
            placeholder={t("ui-finance-memo-hint")}
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
          {t("ui-finance-transfer")}
        </button>
      </div>

      <h3>{t("ui-finance-statement")}</h3>
      {entries.length === 0 ? (
        <p className="note">{t("ui-finance-none")}</p>
      ) : (
        <div className="facts">
          {entries.map((entry, index) => (
            <div className="fact" key={`${entry.at}-${index}`}>
              <span className="fact-name">
                <span className="goods-mark">
                  <Glyph name={groundGlyph(entry.reason)} />
                </span>
                {groundName(entry.reason)}
              </span>
              <span className="fact-val">
                {entry.incoming ? "+" : "−"}
                {entry.money} ₭
              </span>
              <p className="note">
                {counterparty(entry)}
                {entry.memo?.ground ? ` · ${entry.memo.ground}` : ""}
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
