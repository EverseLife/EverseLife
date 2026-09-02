// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Nurlan Urazkulov

/**
 * How a poll stands, in words (D-161, D-164).
 *
 * The two windows that draw a ballot -- the administration's table and the Net
 * tab -- say the count and the bar the same way, so the sentences are here
 * rather than in either of them. Pure and free of React on purpose: the
 * components live in `panels/Poll.tsx`, and these two are the half a test can
 * hold on its own.
 */

import type { CityVote } from "./api";
import { t } from "./locale";

/**
 * The count so far, against the size of the electorate.
 *
 * An election is not a for-or-against poll: every ballot in it names a
 * candidate, so `no` is always zero there and «против 0» read as "nobody
 * objects" rather than as a word that does not apply. In an election the
 * candidates carry their own counts beside their names.
 */
export function pollTally(poll: CityVote): string {
  return poll.kind === "election" || poll.kind === "council"
    ? t("ui-admin-vote-turnout", { yes: String(poll.yes), of: String(poll.electorate) })
    : t("ui-admin-vote-tally", {
        yes: String(poll.yes),
        no: String(poll.no),
        of: String(poll.electorate),
      });
}

/** The bar a poll passes at, each by the message that names it. */
const THRESHOLD: Record<string, string> = {
  simple: "ui-admin-threshold-simple",
  two_thirds: "ui-admin-threshold-two-thirds",
  unanimous: "ui-admin-threshold-unanimous",
};

/**
 * The bar, and the quorum where the charter set one.
 *
 * A threshold the client does not know shows itself: the charter's options
 * live in the vault and may outgrow this map (D-094), and a key reads worse
 * than a word and better than a blank.
 */
export function pollThreshold(poll: CityVote): string {
  const said = poll.threshold in THRESHOLD ? t(THRESHOLD[poll.threshold]) : poll.threshold;
  return poll.quorum > 0
    ? `${said} ${t("ui-admin-vote-quorum", { quorum: String(poll.quorum) })}`
    : said;
}
