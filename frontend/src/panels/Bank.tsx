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
 */

import { useCallback, useEffect, useState } from "react";
import * as api from "../api";
import type { Session } from "../api";
import { when } from "../clock";

type Props = {
  session: Session;
  busy: boolean;
  act: (what: () => Promise<unknown>) => Promise<void>;
};

export function Bank({ session, busy, act }: Props) {
  const [bank, setBank] = useState<any>(null);
  const [qty, setQty] = useState(50);

  const refresh = useCallback(async () => {
    try {
      setBank(await session.send("bank.view"));
    } catch {
      setBank(null);
    }
  }, [session]);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (!bank) return null;
  const go = (what: () => Promise<unknown>) =>
    act(async () => {
      await what();
      await refresh();
    });

  return (
    <>
      <h3>Банк</h3>
      <p>
        ключевая ставка <b>{Number(bank.rate).toFixed(2)}%</b>
        {bank.why && <span className="note"> · {bank.why}</span>}
      </p>
      <Council session={session} busy={busy} act={act} />
      <p className="note">
        в обороте {api.tk(bank.circulating)} ₭ · в резерве {api.tk(bank.reserve)} ₭
      </p>
      <p>
        ваш лимит <b>{api.tk(bank.limit)} ₭</b>
        <span className="note"> · {bank.limit_why}</span>
      </p>

      {(bank.loans ?? []).length > 0 && (
        <table>
          <tbody>
            {bank.loans.map((loan: any) => (
              <tr key={loan.id}>
                <td>
                  {api.tk(loan.outstanding)} ₭
                  <span className="note">
                    {" "}
                    из {api.tk(loan.principal)} ₭ под {Number(loan.rate).toFixed(1)}%
                  </span>
                </td>
                <td>
                  <button
                    onClick={() => go(() => session.send("bank.repay", { loan: loan.id }))}
                    disabled={busy}
                  >
                    Погасить
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="row">
        <input
          type="number"
          min={1}
          value={qty}
          onChange={(e) => setQty(Number(e.target.value))}
          title="сколько занять, ₭"
        />
        <button
          onClick={() => go(() => session.send("bank.borrow", { amount: qty }))}
          disabled={busy || qty <= 0}
        >
          Взять кредит
        </button>
        <span className="note">
          Залога нет: лимит выдаёт труд (D-173). Занимает ваш город со своей
          маржой (D-175).
        </span>
      </div>
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
      <p className="note">
        {council.locked_until
          ? `ставка возвращена алгоритму ещё на ${when(council.locked_until).replace("через ", "")}: инфляция за тревожной чертой`
          : `ставку считает алгоритм: городов с администрацией ${council.cities_with_hall} из ${council.handover_at}, дальше решает Совет городов`}
      </p>
    );
  }

  const desired = rate ?? Number(council.advised);
  return (
    <div className="row">
      <input
        type="number"
        step={0.5}
        min={Number(council.advised) - Number(council.corridor)}
        max={Number(council.advised) + Number(council.corridor)}
        value={desired}
        onChange={(e) => setRate(Number(e.target.value))}
        title={`коридор ±${council.corridor} вокруг рекомендации ${Number(council.advised).toFixed(2)}%`}
      />
      <button
        onClick={() => act(() => session.send("bank.council_rate", { rate: desired }))}
        disabled={busy}
      >
        Голос города за ставку
      </button>
      <span className="note">
        алгоритм советует {Number(council.advised).toFixed(2)}% · коридор ±
        {council.corridor} · голос подаёт держатель права «законы» (D-172)
      </span>
    </div>
  );
}
