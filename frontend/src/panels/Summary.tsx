/**
 * The screen of a returning player (04-notifications).
 *
 * Somebody comes back after a day and has ten seconds to understand what
 * happened. Until now there was no such screen at all: the game had court cases
 * with a reaction window and votes with a quorum, and no way to learn about
 * either except by walking eight sidebar tabs. The vault puts it as a law --
 * any event with irreversible consequences must have a notification **and** a
 * window to react, and one without the other is pointless.
 *
 * Three levels, in this order and no other:
 *
 * - **требует внимания** -- where something can still be done, each with the
 *   time left. Never longer than five lines. If it grows past five, importance
 *   is marked wrong somewhere, and the fix belongs there rather than here;
 * - **произошло** -- done, no answer needed;
 * - **разговоры** -- there is no chat history to come back to (D-043): a
 *   conversation in a room is not correspondence, and this screen says so
 *   rather than pretending it has a backlog.
 */

import { useCallback, useEffect, useState } from "react";
import type { Session } from "../api";
import { Deadline } from "../Deadline";
import { Rule } from "../Rule";
import { when } from "../clock";

/** A thing that can still be acted on. */
type Needs = {
  kind: "case" | "vote" | "debt" | "reservation";
  what: string;
  where?: string;
  since?: string;
  until?: string;
};

type Happened = { at: string; kind: string; payload: Record<string, unknown> };

export type Digest = { at: string; attention: Needs[]; happened: Happened[] };

/** When we last looked. Kept on the client: the server has no business
 *  remembering how attentive somebody is. */
const SEEN = "octoverse.seen";

export function lastSeen(): string | null {
  try {
    return localStorage.getItem(SEEN);
  } catch {
    return null;
  }
}

export function markSeen(at: string): void {
  try {
    localStorage.setItem(SEEN, at);
  } catch {
    /* приватный режим — сводка просто будет за сутки */
  }
}

/** Read the summary. Used by the badge in the header and by the screen itself. */
export function useDigest(session: Session, ready: boolean) {
  const [digest, setDigest] = useState<Digest | null>(null);

  const reread = useCallback(async () => {
    try {
      const answer = await session.send("world.summary", {
        since: lastSeen() ?? undefined,
      });
      setDigest(answer as unknown as Digest);
    } catch {
      //: No summary is not an error worth a strip across the screen: the world
      //: is readable without it.
      setDigest(null);
    }
  }, [session]);

  useEffect(() => {
    if (!ready) return;
    void reread();
    //: Slower than the world poll: this is a summary, not a ticker.
    const timer = setInterval(() => void reread(), 60_000);
    return () => clearInterval(timer);
  }, [ready, reread]);

  return { digest, reread };
}

/** What each kind of demand is called, in the words the player thinks in. */
const CALLED: Record<Needs["kind"], string> = {
  case: "суд",
  vote: "голос",
  debt: "долг",
  reservation: "бронь",
};

/** Journal kinds in words. The player reads what happened, not an enum. */
const SAID: Record<string, string> = {
  "craft.finished": "партия готова",
  "travel.arrived": "пришли",
  "farm.harvested": "урожай собран",
  "explore.found": "разведка: находка",
  "explore.empty": "разведка: пусто",
  "body.died": "тело погибло",
  "body.printed": "напечатано тело",
  "mining.collapsed": "обвал в забое",
  "market.trade": "сделка",
  "market.order_expired": "ордер снят по сроку",
  "market.reservation_lapsed": "бронь просрочена",
  "city.law_set": "город изменил закон",
  "city.vote_closed": "голосование закрыто",
  "justice.case_judged": "приговор",
  "justice.sanction_applied": "наложена санкция",
  "bank.debt_withheld": "с долга удержано",
  "utility.cut_off": "узел отключён за неуплату",
  "transport.broke": "повозка разбилась",
  "road.laid": "дорога уложена",
  "deed.sold": "бумага продана",
  "city.grant_paid": "подъёмные выплачены",
};

/** The one detail worth showing beside the line, if the payload has one. */
function detail(row: Happened): string | null {
  const p = row.payload ?? {};
  for (const key of ["output", "goods", "resource", "node", "law", "to", "type_key"]) {
    const value = p[key];
    if (typeof value === "string" && value) return value;
  }
  return null;
}

export function Summary({
  digest,
  onClose,
}: {
  digest: Digest;
  onClose: () => void;
}) {
  const needs = digest.attention;
  //: Five lines is the vault's ceiling, and it is a check on our own marking
  //: rather than a display trick: the rest is reachable where it lives.
  const shown = needs.slice(0, 5);
  const rest = needs.length - shown.length;

  return (
    <div className="veil" role="dialog" aria-modal="true" aria-label="Что произошло">
      <section className="intro">
        <h2>
          Пока вас не было
          <Rule>
            Сводка считается от того момента, когда вы её закрыли в прошлый раз. Всё, у
            чего есть срок, показано с остатком в настоящих часах: пропущенный срок в
            этом мире необратим, поэтому о нём говорят заранее, а не после.
          </Rule>
        </h2>

        <h3>Требует внимания</h3>
        {shown.length === 0 ? (
          <p className="note">Ничего не ждёт: сроки не поджимают.</p>
        ) : (
          <div className="needs">
            {shown.map((line, i) => (
              <div className="need" key={`${line.kind}-${i}`}>
                <span className={`need-kind ${line.kind}`}>{CALLED[line.kind]}</span>
                <span className="need-what">
                  {line.what}
                  {line.where && <span className="note"> · {line.where}</span>}
                </span>
                {line.until && (
                  <Deadline until={line.until} since={line.since} label={line.what} size="row" />
                )}
              </div>
            ))}
            {rest > 0 && (
              <p className="note">
                и ещё {rest}: их видно там, где они живут — в городе, в хозяйстве, в деньгах.
              </p>
            )}
          </div>
        )}

        <h3>Произошло</h3>
        {digest.happened.length === 0 ? (
          <p className="note">С прошлого раза ничего не случилось.</p>
        ) : (
          <table>
            <tbody>
              {digest.happened.map((row, i) => (
                <tr key={`${row.at}-${i}`}>
                  <td>
                    {SAID[row.kind] ?? row.kind}
                    {detail(row) && <span className="note"> · {detail(row)}</span>}
                  </td>
                  <td className="num">{when(row.at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <h3>Разговоры</h3>
        <p className="note">
          Разговор живёт, пока вы в комнате: истории у него нет, и вернуться к
          сказанному нельзя. Это переписка не ведётся — это речь.
        </p>

        <div className="row">
          <button onClick={onClose}>Понятно</button>
        </div>
      </section>
    </div>
  );
}
