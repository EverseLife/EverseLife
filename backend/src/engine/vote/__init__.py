# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov
"""Citizens' vote (D-036, D-130, D-161).

The charter asks who approves a law, at what threshold, with what quorum and
who has a vote at all. Until now the engine ran a single branch -- "the ruler
alone", the default one: a city that chose "by citizens' vote" simply could
not exist.

## How it works

Whoever the charter allowed to propose laws does not change the law but
**opens a poll** for `vote.duration` hours. At the deadline a journal job
counts the result and applies it itself -- without anybody's participation,
even if everyone has left.

| Condition | Charter question |
|---|---|
| who has a vote | `vote_qualification`: all citizens - by residency - by property |
| quorum | `quorum`: not required, or a share of those eligible |
| threshold | `law_threshold`: simple majority - two thirds - unanimity |

**Conditions are captured at opening.** A charter changed mid-poll does not
rewrite the rules of one already running: otherwise a ruler who sees they are
losing would raise the threshold on the fly. For the same reason the record
stores the number of eligible voters at convening -- the quorum is counted from it.

**A vote is cast remotely.** This is the Net, not an in-person action: a
citizen votes from the road and from the mine. Presence is needed to
**govern** (D-155), and a vote is not governing, it is participation.

## What is not here

* **Secret ballot.** The vault names visibility as a charter parameter, but
  there is no question for it in `laws.json`, and creating one in code is
  forbidden (D-065);
* **A treasury-contribution census.** No table tracks personal contribution,
  and it cannot be derived from postings: their ground does not distinguish
  "donated" from "paid tax";
* **Elections, recall and charter amendment.** They will sit on the same
  machine -- same census, quorum and threshold, only the subject differs.
"""

from src.engine.vote._base import (  # noqa: F401
    ALL,
    AMENDMENT,
    AMENDMENT_THRESHOLD,
    APPOINTED_COUNCIL,
    APPROVAL,
    BY_CITIZENS,
    BY_COUNCIL,
    BY_RULER,
    CITIZENS,
    COUNCIL,
    COUNCIL_VOTERS,
    ELECTED,
    ELECTED_BY_COUNCIL,
    ELECTED_COUNCIL,
    FIXED_TERM,
    LAWMAKER,
    NEVER,
    NO_COUNCIL,
    PROPERTY,
    QUALIFICATION,
    QUORUM,
    RECALL_BY_CITIZENS,
    RECALL_BY_COUNCIL,
    RECALL_RULE,
    RESIDENCE,
    SELECTION,
    SIMPLE,
    TERM,
    THRESHOLD,
    TWO_THIRDS,
    UNANIMOUS,
    Closed,
    NoCouncil,
    NotCandidate,
    NotElective,
    NoVoice,
    Sealed,
    VoteError,
    amends_by_vote,
    answer,
    by_citizens,
    elects_ruler,
    may_vote,
    open_votes,
    param,
    passes,
    recallable,
    sealed,
    standing,
    tally,
)
from src.engine.vote.council import (  # noqa: F401
    appoint_to_council,
    council_mode,
    council_of,
    council_seats,
    has_council,
    in_council,
    may_propose,
    seat,
    vacate,
    voters_for,
)
from src.engine.vote.poll import (  # noqa: F401
    cast,
    choose,
    close,
    electorate,
    may_vote_in,
    nominate,
    open_charter,
    open_council_election,
    open_election,
    open_law,
    open_recall,
    view,
)
