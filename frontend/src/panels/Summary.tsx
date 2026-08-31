// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

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
import { useNames } from "../actions";
import { Deadline } from "../Deadline";
import { goodsName, type NamesRu } from "../names";
import { Rule } from "../Rule";
import { when } from "../clock";
import { eventKey, t } from "../locale";

/**
 * A thing that can still be acted on.
 *
 * The line is named rather than written (D-251): the server sends the message
 * and its arguments, the words are found here. It used to arrive as a finished
 * Russian sentence -- and one of the four had a stable key inside it, so the
 * list read «забрать бронь: iron_ore».
 */
type Needs = {
  kind: "case" | "vote" | "debt" | "reservation";
  say: string;
  args?: Record<string, unknown>;
  where?: string;
  since?: string;
  until?: string;
};

type Happened = { at: string; kind: string; payload: Record<string, unknown> };

export type Digest = { at: string; attention: Needs[]; happened: Happened[] };

/** When we last looked. Kept on the client: the server has no business
 *  remembering how attentive somebody is. */
const SEEN = "everselife.seen";

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

/** What the summary is made of: cases, votes, debts, reservations (D-226). */
const SUMMARY_TOUCHES = ["justice", "city", "bank", "orders", "all"];

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

  //: The summary is rebuilt when something happens to the player (D-226),
  //: not on a clock: a case opened, a vote, a debt withheld all arrive as
  //: events. Several in one breath are one reread.
  useEffect(() => {
    if (!ready) return;
    void reread();
    let timer: ReturnType<typeof setTimeout> | null = null;
    const stop = session.on("*", (happening) => {
      if (timer || !SUMMARY_TOUCHES.some((t) => happening.touches.includes(t))) return;
      timer = setTimeout(() => {
        timer = null;
        void reread();
      }, 500);
    });
    return () => {
      stop();
      if (timer) clearTimeout(timer);
    };
  }, [ready, reread, session]);

  return { digest, reread };
}

/** What each kind of demand is called, in the words the player thinks in. */
const CALLED: Record<Needs["kind"], string> = {
  case: "ui-need-case",
  vote: "ui-need-vote",
  debt: "ui-need-debt",
  reservation: "ui-need-reservation",
};

/** Journal kinds in words. The player reads what happened, not an enum. */
/** The one detail worth showing beside the line, if the payload has one. */
function detail(row: Happened, names: NamesRu | null): string | null {
  const p = row.payload ?? {};
  //: The first four keys carry goods ids (D-251) and go through the names;
  //: a node, a law or a person is already a word.
  for (const key of ["output", "goods", "resource", "type_key"]) {
    const value = p[key];
    if (typeof value === "string" && value) return goodsName(names, value);
  }
  for (const key of ["node", "law", "to"]) {
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
  const names = useNames();
  const needs = digest.attention;
  //: Five lines is the vault's ceiling, and it is a check on our own marking
  //: rather than a display trick: the rest is reachable where it lives.
  const shown = needs.slice(0, 5);
  const rest = needs.length - shown.length;

  return (
    <div className="veil" role="dialog" aria-modal="true" aria-label={t("ui-summary-label")}>
      <section className="intro">
        <h2>
          {t("ui-summary-title")}
          <Rule>
            {t("ui-summary-rule")}
          </Rule>
        </h2>

        <h3>{t("ui-summary-attention")}</h3>
        {shown.length === 0 ? (
          <p className="note">{t("ui-summary-attention-none")}</p>
        ) : (
          <div className="needs">
            {shown.map((line, i) => {
              const what = t(line.say, line.args);
              return (
                <div className="need" key={`${line.kind}-${i}`}>
                  <span className={`need-kind ${line.kind}`}>{t(CALLED[line.kind])}</span>
                  <span className="need-what">
                    {what}
                    {line.where && <span className="note"> · {line.where}</span>}
                  </span>
                  {line.until && (
                    <Deadline until={line.until} since={line.since} label={what} size="row" />
                  )}
                </div>
              );
            })}
            {rest > 0 && (
              <p className="note">{t("ui-summary-attention-rest", { count: rest })}</p>
            )}
          </div>
        )}

        <h3>{t("ui-summary-happened")}</h3>
        {digest.happened.length === 0 ? (
          <p className="note">{t("ui-summary-happened-none")}</p>
        ) : (
          <table>
            <tbody>
              {digest.happened.map((row, i) => (
                <tr key={`${row.at}-${i}`}>
                  <td>
                    {t(eventKey(row.kind))}
                    {detail(row, names) && <span className="note"> · {detail(row, names)}</span>}
                  </td>
                  <td className="num">{when(row.at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <h3>{t("ui-summary-talk")}</h3>
        <p className="note">
          {t("ui-summary-talk-rule")}
        </p>

        <div className="row">
          <button onClick={onClose}>{t("ui-summary-close")}</button>
        </div>
      </section>
    </div>
  );
}
