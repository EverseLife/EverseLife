// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * One poll, wherever it is answered (D-161, D-162).
 *
 * A vote is participation, not governing: presence is needed to rule, and a
 * ballot is cast from the road. So the same poll shows in two places -- the
 * administration, where the city's whole political machinery stands, and the
 * Net, where what reaches a person from afar arrives. What the poll is about
 * and how it is answered live here, once, or the two windows would sooner or
 * later say the same thing differently.
 *
 * Nomination is deliberately not here: standing for office is a political act
 * and belongs beside the rest of them, not beside an answer to a question.
 * Neither is the count in words -- that half is pure, and pure code lives
 * where a test can reach it without a browser (`polls.ts`).
 */

import { useSession } from "../actions";
import type { CityVote } from "../api";
import { t } from "../locale";
import { lawName, lawOption, type Names } from "../names";

/** What is being decided, in the reader's words. */
export function PollSubject({ poll, names }: { poll: CityVote; names: Names | null }) {
  if (poll.kind === "election" || poll.kind === "council") {
    return (
      <>
        {poll.kind === "council" ? t("ui-admin-vote-council") : t("ui-admin-vote-ruler")}
        <span className="note">
          {" "}
          {poll.candidates.length === 0
            ? t("ui-admin-vote-no-candidates")
            : `· ${poll.candidates.map((one) => `${one.name} (${one.votes})`).join(", ")}`}
        </span>
      </>
    );
  }
  if (poll.kind === "recall") return <>{t("ui-admin-vote-recall")}</>;
  if (poll.kind === "charter") {
    return (
      <>
        {t("ui-admin-vote-charter")}
        {/* The player is not shown the name of the charter question the
            threshold comes from: that is a key out of the vault, and the
            sentence around it was written for whoever wrote the code. What
            matters here is that the charter sets its own bar for changing
            itself. */}
        <span className="note"> {t("ui-admin-vote-charter-note")}</span>
      </>
    );
  }
  return (
    <>
      {/* Both halves are named, not printed: the subject travels as its
          D-251 id and so does the value of a law that is a choice -- the line
          read «tax_trade» before the first, and «citizens» before the second.
          `lawOption` gives a number back unchanged, so a rate needs no
          telling apart here. */}
      {lawName(names, String(poll.law))}
      <span className="note">
        {" "}
        → {lawOption(names, String(poll.law), String(poll.value))}
      </span>
    </>
  );
}

/** The ballot itself: for or against, or a name. */
export function PollAnswer({
  poll,
  go,
  busy,
}: {
  poll: CityVote;
  /** Run it and reread whatever the window around it shows: the two windows
   *  keep different lists, and only they know what to read again. */
  go: (what: () => Promise<unknown>) => Promise<void>;
  busy: boolean;
}) {
  const session = useSession();
  if (poll.kind === "election" || poll.kind === "council") {
    return (
      <>
        {poll.candidates.map((candidate) => (
          <button
            key={candidate.id}
            className={poll.choice === candidate.id ? "" : "quiet"}
            onClick={() => go(() => session.send("city.choose", { vote: poll.id, candidate: candidate.id }))}
            disabled={busy || !poll.may_vote}
          >
            {t("ui-admin-vote-for", { name: candidate.name })}
          </button>
        ))}
      </>
    );
  }
  if (!poll.may_vote) return <span className="note">{t("ui-admin-vote-none")}</span>;
  return (
    <>
      <button
        className={poll.mine === true ? "" : "quiet"}
        onClick={() => go(() => session.send("city.vote", { vote: poll.id, yes: true }))}
        disabled={busy}
      >
        {t("ui-admin-vote-yes")}
      </button>
      <button
        className={poll.mine === false ? "" : "quiet"}
        onClick={() => go(() => session.send("city.vote", { vote: poll.id, yes: false }))}
        disabled={busy}
      >
        {t("ui-admin-vote-no")}
      </button>
    </>
  );
}
