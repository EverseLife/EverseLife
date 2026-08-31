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
import { Rule } from "../../Rule";
import { useSession } from "../../actions";

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
  //: The bar a poll passes at, each by the message that names it.
  const threshold: Record<string, string> = {
    simple: "ui-admin-threshold-simple",
    two_thirds: "ui-admin-threshold-two-thirds",
    unanimous: "ui-admin-threshold-unanimous",
  };

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
                {convening.kind === "election" || convening.kind === "council" ? (
                  <>
                    {convening.kind === "council"
                      ? t("ui-admin-vote-council")
                      : t("ui-admin-vote-ruler")}
                    <span className="note">
                      {" "}
                      {convening.candidates.length === 0
                        ? t("ui-admin-vote-no-candidates")
                        : `· ${convening.candidates
                            .map((k) => `${k.name} (${k.votes})`)
                            .join(", ")}`}
                    </span>
                  </>
                ) : convening.kind === "recall" ? (
                  t("ui-admin-vote-recall")
                ) : convening.kind === "charter" ? (
                  <>
                    {t("ui-admin-vote-charter")}
                    {/* The player is not shown the name of the charter question
                        the threshold comes from: that is a key out of the vault,
                        and the sentence around it was written for whoever wrote
                        the code. What matters here is that the charter sets its
                        own bar for changing itself. */}
                    <span className="note"> {t("ui-admin-vote-charter-note")}</span>
                  </>
                ) : (
                  <>
                    {convening.law}
                    <span className="note"> → {String(convening.value)}</span>
                  </>
                )}
              </td>
              <td className="note">
                {convening.voters === "council" && `${t("ui-admin-vote-by-council")} · `}
                {/* An election is not a for-or-against poll: every ballot in it
                    names a candidate, so `no` is always zero there and "против
                    0" read as "nobody objects" rather than as a word that does
                    not apply. The candidates carry their own counts above. */}
                {convening.kind === "election" || convening.kind === "council"
                  ? t("ui-admin-vote-turnout", {
                      yes: String(convening.yes),
                      of: String(convening.electorate),
                    })
                  : t("ui-admin-vote-tally", {
                      yes: String(convening.yes),
                      no: String(convening.no),
                      of: String(convening.electorate),
                    })}
                {" · "}
                {convening.threshold in threshold
                  ? t(threshold[convening.threshold])
                  : convening.threshold}
                {convening.quorum > 0 &&
                  ` ${t("ui-admin-vote-quorum", { quorum: String(convening.quorum) })}`}
              </td>
              <td className="note">
                {t("ui-admin-vote-closes", { when: when(convening.closes_at) })}
              </td>
              <td>
                {convening.kind === "election" || convening.kind === "council" ? (
                  <>
                    {/* Standing twice is refused, so it is not offered twice. */}
                    {!convening.candidates.some((one) => one.own) && (
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
                    {convening.candidates.map((candidate) => (
                      <button
                        key={candidate.id}
                        className={convening.choice === candidate.id ? "" : "quiet"}
                        onClick={() =>
                          go(() =>
                            session.send("city.choose", {
                              vote: convening.id,
                              candidate: candidate.id,
                            }),
                          )
                        }
                        disabled={busy || !convening.may_vote}
                      >
                        {t("ui-admin-vote-for", { name: candidate.name })}
                      </button>
                    ))}
                  </>
                ) : convening.may_vote ? (
                  <>
                    <button
                      className={convening.mine === true ? "" : "quiet"}
                      onClick={() =>
                        go(() => session.send("city.vote", { vote: convening.id, yes: true }))
                      }
                      disabled={busy}
                    >
                      {t("ui-admin-vote-yes")}
                    </button>
                    <button
                      className={convening.mine === false ? "" : "quiet"}
                      onClick={() =>
                        go(() => session.send("city.vote", { vote: convening.id, yes: false }))
                      }
                      disabled={busy}
                    >
                      {t("ui-admin-vote-no")}
                    </button>
                  </>
                ) : (
                  <span className="note">{t("ui-admin-vote-none")}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      )}
    </>
  );
}
