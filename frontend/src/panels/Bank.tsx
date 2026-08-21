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
import * as api from "../api";
import type { Session } from "../api";
import { when } from "../clock";
import { Rule } from "../Rule";

type Props = {
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

/** The grounds for a figure, as the clauses the engine joined them from (D-030).
 *
 * Every number the bank shows arrives with its reason, and the reason is
 * composed: `"база 900 ₭; оборот 1 200 ₭ за 7 суток; возвращено ранее 0 ₭"`.
 * Laid beside the number as one line it read as prose about a number rather
 * than the arithmetic of it. Split back apart, each clause is one fact.
 *
 * The vault's reference the engine signs some of its clauses with is dropped
 * on the way: a decision code is how we argue about the rule, not how a player
 * reads it -- and standing alone at the end of a short clause it is the loudest
 * thing in the list.
 */
function Why({ text }: { text?: string | null }) {
  const clauses = String(text ?? "")
    .split(";")
    .map((clause) => clause.replace(/\s*\(D-\d+\)/g, "").trim())
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

export function Bank({ session, busy, act }: Props) {
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
        Банк
        <Rule>
          Залога нет: лимит выдаёт труд — оборот и уже погашенные кредиты. Занимает вам
          ваш город со своей маржой, и пока его линия не исчерпана, ставка ниже: дальше
          деньги идут напрямую у столицы, с надбавкой за риск.
        </Rule>
      </h3>
      <div className="facts">
        <div className="fact">
          <span className="fact-name">ключевая ставка</span>
          <span className="fact-val lead">{Number(bank.rate).toFixed(2)}%</span>
          <Why text={bank.why} />
        </div>
        <div className="fact">
          <span className="fact-name">в обороте</span>
          <span className="fact-val">{api.tk(bank.circulating)} ₭</span>
        </div>
        <div className="fact">
          <span className="fact-name">в резерве</span>
          <span className="fact-val">{api.tk(bank.reserve)} ₭</span>
        </div>
      </div>

      {loans.length > 0 && (
        <>
          <p className="sub">Ваши долги</p>
          <div className="facts">
            {loans.map((loan) => (
              <div className="fact" key={loan.id}>
                <span className="fact-name">осталось вернуть</span>
                <span className="fact-val lead">{api.tk(loan.outstanding)} ₭</span>
                <p className="note">
                  из {api.tk(loan.principal)} ₭ под {Number(loan.rate).toFixed(1)}% ·
                  взят {when(loan.taken_at)}
                </p>
                <button
                  className="quiet"
                  onClick={() => go(() => session.send("bank.repay", { loan: loan.id }))}
                  disabled={busy}
                >
                  Погасить
                </button>
              </div>
            ))}
          </div>
        </>
      )}

      <p className="sub">Занять</p>
      <div className="facts">
        <div className="fact">
          <span className="fact-name">ваш лимит</span>
          <span className="fact-val lead">{api.tk(bank.limit)} ₭</span>
          <Why text={bank.limit_why} />
        </div>
        {/* Своя ставка называется до кнопки, а не после (D-193): она зависит
            от запрошенной суммы, поэтому стоит вплотную к полю. */}
        {bank.your_rate !== undefined && (
          <div className="fact">
            <span className="fact-name">вам дадут под</span>
            <span className="fact-val lead">{Number(bank.your_rate).toFixed(2)}%</span>
            <Why text={bank.your_rate_why} />
          </div>
        )}
      </div>
      <div className="form">
        <label>
          <span>сколько занять, ₭</span>
          <input
            type="number"
            min={1}
            value={qty}
            onChange={(e) => setQty(Number(e.target.value))}
          />
        </label>
        <button
          onClick={() => go(() => session.send("bank.borrow", { amount: qty }))}
          disabled={busy || qty <= 0}
        >
          Взять кредит
        </button>
      </div>

      <Council session={session} busy={busy} act={act} />
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
function Council({ session, busy, act }: Props) {
  const [council, setCouncil] = useState<any>(null);
  const [rate, setRate] = useState<number | null>(null);

  useEffect(() => {
    void session.send("bank.council").then(setCouncil).catch(() => setCouncil(null));
  }, [session]);

  if (!council) return null;
  if (!council.council_decides) {
    return (
      <>
        <p className="sub">Совет городов</p>
        <p className="note">
          {council.locked_until
            ? `ставка возвращена алгоритму ещё на ${when(council.locked_until).replace("через ", "")}: инфляция за тревожной чертой`
            : `ставку считает алгоритм: городов с администрацией ${council.cities_with_hall} из ${council.handover_at}, дальше решает Совет городов`}
        </p>
      </>
    );
  }

  const desired = rate ?? Number(council.advised);
  return (
    <>
      <p className="sub">Совет городов</p>
      <div className="form">
        <label>
          <span>
            ставка города, % — коридор ±{council.corridor} вокруг{" "}
            {Number(council.advised).toFixed(2)}%
          </span>
          <input
            type="number"
            step={0.5}
            min={Number(council.advised) - Number(council.corridor)}
            max={Number(council.advised) + Number(council.corridor)}
            value={desired}
            onChange={(e) => setRate(Number(e.target.value))}
          />
        </label>
        <button
          onClick={() => act(() => session.send("bank.council_rate", { rate: desired }))}
          disabled={busy}
        >
          Голос города за ставку
        </button>
      </div>
      <ul className="why">
        <li>алгоритм советует {Number(council.advised).toFixed(2)}%</li>
        <li>коридор ±{council.corridor}: Совет спорит с алгоритмом, а не заменяет его</li>
        <li>голос подаёт держатель права «законы»</li>
      </ul>
    </>
  );
}
