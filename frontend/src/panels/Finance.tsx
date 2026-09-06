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
 * without a history is a number nobody can argue with. It is a panel of its
 * own (`Statement`): pages and rows that open are a screen's worth by
 * themselves, and the transfer form has nothing to do with them.
 */

import { useState } from "react";
import { useSession } from "../actions";
import type { Look } from "../api";
import { Bank } from "./Bank";
import { Statement } from "./Statement";
import { Rule } from "../Rule";
import { t } from "../locale";
import { NumberField } from "../NumberField";

type Props = {
  look: Look;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

export function Finance({ look, busy, act }: Props) {
  const session = useSession();
  const [to, setTo] = useState("");
  const [amount, setAmount] = useState(10);
  const [memo, setMemo] = useState("");

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
      {/* The fields run the full width and are labelled above: a person's name
          is longer than what is left of the line after the amount field, and a
          hint inside a field disappears at exactly the moment it is being
          checked against what was typed. */}
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
          <NumberField
            min={0}
            step="0.01"
            value={amount}
            onChange={(typed) => setAmount(typed ?? 0)}
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
            act(async () => {
              await session.send("finance.transfer", { to, amount, memo });
              setMemo("");
            })
          }
          disabled={busy || !to.trim() || amount <= 0}
        >
          {t("ui-finance-transfer")}
        </button>
      </div>

      {/* The statement rereads itself when the balance moves: a transfer
          changes `look.money`, and `act` refreshes the look after it. */}
      <Statement look={look} />

      {/* A loan is the Net too: taken and repaid from anywhere (D-167). */}
      <Bank busy={busy} act={act} />
    </div>
  );
}
