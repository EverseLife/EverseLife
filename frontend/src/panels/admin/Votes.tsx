// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * Where power changes hands.
 *
 * The decisions in the window proper answer to a right: without `offices` one
 * does not appoint, without `treasury` one does not spend. A poll answers to
 * the charter instead -- who may convene one, who may vote in it and what bar
 * it passes at are the city's own answers (D-162). So this file reads
 * `city.charter` where its neighbours read `city.powers`, which is enough of a
 * difference to keep the two apart.
 */

import type { CityVote, CityView } from "../../api";
import { when } from "../../clock";
import { t } from "../../locale";
import { PollAnswer, PollSubject } from "../Poll";
import { pollTally, pollThreshold } from "../../polls";
import { Rule } from "../../Rule";
import { useNames, useSession } from "../../actions";

/** Ongoing polls: subject, deadline, tally and own vote (D-161).
 *
 * Shown to everyone, not only the authority: a poll visible only to whoever
 * convened it is not a procedure but a formality. The result applies itself
 * on schedule, so there is and cannot be a "tally" button here.
 */
export function Votes({
  polls,
  city,
  go,
  busy,
}: {
  polls: CityVote[];
  city: CityView;
  go: (what: () => Promise<unknown>) => Promise<void>;
  busy: boolean;
}) {
  const session = useSession();
  const names = useNames();
  //: Convening is shown only where the charter allows it: turnover of power
  //: is also a city decision, not an engine property (D-162).
  const elective =
    city.charter?.ruler_selection === "elected_citizens" ||
    city.charter?.ruler_selection === "elected_council";
  const byCouncil = city.charter?.council_exists === "elected";
  const recallable =
    city.charter?.ruler_recall === "by_citizens" ||
    city.charter?.ruler_recall === "by_council";
  const running = (kind: CityVote["kind"]) => polls.some((g) => g.kind === kind);
  if (polls.length === 0 && !elective && !recallable && !byCouncil) return null;

  return (
    <>
      <h3>
        {t("ui-admin-votes")}
        <Rule>{t("ui-admin-votes-rule")}</Rule>
      </h3>
      {(elective || recallable || byCouncil) && (
        <div className="row">
          {elective && !running("election") && (
            <button
              onClick={() => go(() => session.send("city.election"))}
              disabled={busy}
            >
              {t("ui-admin-call-election")}
            </button>
          )}
          {city.charter?.council_exists === "elected" && !running("council") && (
            <button
              className="quiet"
              onClick={() => go(() => session.send("city.council_election"))}
              disabled={busy}
            >
              {t("ui-admin-call-council")}
            </button>
          )}
          {recallable && !running("recall") && (
            <button
              className="quiet"
              onClick={() => go(() => session.send("city.recall"))}
              disabled={busy}
            >
              {t("ui-admin-call-recall")}
            </button>
          )}
          <span className="note">{t("ui-admin-votes-note")}</span>
        </div>
      )}
      {polls.length > 0 && (
      <table>
        <tbody>
          {polls.map((convening) => (
            <tr key={convening.id}>
              <td>
                <PollSubject poll={convening} names={names} />
              </td>
              <td className="note">
                {convening.voters === "council" && `${t("ui-admin-vote-by-council")} · `}
                {pollTally(convening)}
                {" · "}
                {pollThreshold(convening)}
              </td>
              <td className="note">
                {t("ui-admin-vote-closes", { when: when(convening.closes_at) })}
              </td>
              <td>
                {/* Standing twice is refused, so it is not offered twice.
                    Nomination stays in this window and does not travel with
                    the ballot: putting oneself up for office is a political
                    act, not an answer to a question. */}
                {(convening.kind === "election" || convening.kind === "council") &&
                  !convening.candidates.some((one) => one.own) && (
                    <button
                      className="quiet"
                      onClick={() =>
                        go(() => session.send("city.nominate", { vote: convening.id }))
                      }
                      disabled={busy}
                      title={t("ui-admin-nominate-title")}
                    >
                      {t("ui-admin-nominate")}
                    </button>
                  )}
                <PollAnswer poll={convening} go={go} busy={busy} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      )}
    </>
  );
}
