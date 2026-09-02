# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The vote's vocabulary and floor: the charter keys the engine executes,
every refusal a ballot can make, and the pure readers of the charter --
who may vote, what passes, what a tally says. Asks nobody above itself.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import ledger
from src.engine.errors import Refusal
from src.models.city import City
from src.models.ledger import AccountKind
from src.models.vote import Ballot, Vote, VoteState
from src.units import PERCENT, money

#: Charter questions from which the procedure is assembled.
APPROVAL = "law_approval"

THRESHOLD = "law_threshold"

QUORUM = "quorum"

QUALIFICATION = "vote_qualification"

#: Options the engine executes. The rest are named in the header.
BY_CITIZENS = "citizens"

SIMPLE, TWO_THIRDS, UNANIMOUS = "simple", "two_thirds", "unanimous"

ALL, RESIDENCE, PROPERTY = "all", "residence", "property"


class VoteError(Refusal):
    pass


class NoVoice(VoteError):
    """No vote: citizenship or census is lacking. The census is the charter's business."""


class Closed(VoteError):
    """The poll is closed. A late vote does not change the result."""


def answer(city: City, question: str, default: str) -> str:
    return str((city.charter or {}).get(question) or default)


def param(city: City, question: str) -> float:
    """A charter option's numeric parameter: days of residency, TC of property, %."""
    try:
        return float((city.charter_params or {}).get(question) or 0)
    except (TypeError, ValueError):  # pragma: no cover -- a human edits the parameter
        return 0.0


def by_citizens(city: City) -> bool:
    """Whether laws are approved by a citizens' vote rather than the ruler alone."""
    return answer(city, APPROVAL, "ruler") == BY_CITIZENS


async def may_vote(
    session: AsyncSession, city: City, identity_id: uuid.UUID, *, now: datetime | None = None
) -> bool:
    """Whether this person has a vote in this city (`vote_qualification`).

    Only citizens have a vote (D-160): without that democracy turns into a
    multi-account contest, and the whole political layer loses its value.
    """

    moment = now or datetime.now(UTC)
    entry = await town.citizenship(session, identity_id)
    if entry is None or entry.city_id != city.id:
        return False

    census = answer(city, QUALIFICATION, ALL)
    if census == RESIDENCE:
        term = timedelta(days=param(city, QUALIFICATION))
        return entry.since + term <= moment
    if census == PROPERTY:
        #: `find_account`, not `account_for`: this is a **read** -- the digest
        #: and the Net tab ask it on every look -- and `account_for` creates
        #: the row it does not find. No account means nothing was ever posted,
        #: which is a balance of zero and an honest answer to the census.
        account = await ledger.find_account(session, AccountKind.IDENTITY, identity_id)
        if account is None:
            return money(param(city, QUALIFICATION)) <= 0
        return await ledger.balance(session, account.id) >= money(param(city, QUALIFICATION))
    #: The treasury-contribution census is not enforced: contribution is not
    #: tracked (D-161). Such a city votes with all citizens rather than locking up.
    return True


async def standing(session: AsyncSession, vote: Vote) -> tuple[int, int]:
    """How many for and how many against right now. The poll is open."""
    ballots = (
        (await session.execute(select(Ballot).where(Ballot.vote_id == vote.id))).scalars().all()
    )
    pro = sum(1 for b in ballots if b.yes)
    return pro, len(ballots) - pro


def passes(constants: Constants, vote: Vote, pro: int, contra: int) -> tuple[bool, str]:
    """Whether it passed. Returns the decision and the reason -- the player sees it,
    not only the log.

    The shares behind the charter's words lie in `vote.thresholds`: "two thirds"
    is a number, and a number belongs in the vault (D-065). A simple majority
    is taken **strictly more** than half, other thresholds not less than their
    share: otherwise an even split would pass as a majority.
    """
    submitted = pro + contra
    quorum_needed = float(vote.quorum_share) / PERCENT * vote.electorate
    if submitted < quorum_needed:
        return False, "кворум не собран"
    if submitted == 0:
        return False, "не проголосовал никто"

    shares = constants[R.VOTE_THRESHOLDS]
    share = shares.get(vote.threshold, shares.get(SIMPLE, 0))
    needed = submitted * share
    enough = pro > needed if vote.threshold == SIMPLE else pro >= needed
    titles = {
        SIMPLE: ("большинство за", "большинства нет"),
        TWO_THIRDS: ("две трети собраны", "двух третей нет"),
        UNANIMOUS: ("единогласно", "не единогласно"),
    }
    elapsed, not_passed = titles.get(vote.threshold, titles[SIMPLE])
    return enough, (elapsed if enough else not_passed)


async def open_votes(session: AsyncSession, city: City) -> list[Vote]:
    return list(
        (
            await session.execute(
                select(Vote).where(Vote.city_id == city.id, Vote.state == VoteState.OPEN)
            )
        )
        .scalars()
        .all()
    )


# --- election and recall (D-162) ---------------------------------------------

#: Charter questions about change of power.
SELECTION = "ruler_selection"

TERM = "ruler_term"

RECALL_RULE = "ruler_recall"

#: Options the engine executes.
ELECTED = "elected_citizens"

#: The ruler is elected by the council, not the whole city (D-165).
ELECTED_BY_COUNCIL = "elected_council"

RECALL_BY_CITIZENS = "by_citizens"

RECALL_BY_COUNCIL = "by_council"

FIXED_TERM = "fixed"


class NotElective(VoteError):
    """The charter did not hand power to elections: turnover is also a city decision."""


class NotCandidate(VoteError):
    """Citizens nominate themselves, and only while the election runs."""


def elects_ruler(city: City) -> bool:
    """Whether the ruler is elected at all -- by the whole city or by the council (D-165)."""
    return answer(city, SELECTION, "founder") in (ELECTED, ELECTED_BY_COUNCIL)


def recallable(city: City) -> bool:
    return answer(city, RECALL_RULE, "never") in (
        RECALL_BY_CITIZENS,
        RECALL_BY_COUNCIL,
    )


async def tally(session: AsyncSession, vote: Vote) -> dict[str, int]:
    """How many votes each has. The poll is open, the tally is visible to all."""
    ballots = (
        (await session.execute(select(Ballot).where(Ballot.vote_id == vote.id))).scalars().all()
    )
    account: dict[str, int] = {}
    for b in ballots:
        if b.choice_identity_id is None:
            continue
        key = str(b.choice_identity_id)
        account[key] = account.get(key, 0) + 1
    return account


# --- charter amendment by vote (D-163) ---------------------------------------

#: The charter question about how the charter itself is amended, and its options.
AMENDMENT = "charter_amendment"

BY_RULER, NEVER = "ruler", "never"

#: The threshold an amendment is voted at. Keys are `charter_amendment`
#: options, values are thresholds of the same machine: the constitution has its
#: own threshold, not `law_threshold`.
AMENDMENT_THRESHOLD = {"two_thirds": TWO_THIRDS, "unanimous": UNANIMOUS}


class Sealed(VoteError):
    """The charter is sealed: `charter_amendment: never` is executed literally."""


def amends_by_vote(city: City) -> bool:
    """Whether the charter is amended by vote rather than the ruler's stroke of a pen."""
    return answer(city, AMENDMENT, BY_RULER) in AMENDMENT_THRESHOLD


def sealed(city: City) -> bool:
    return answer(city, AMENDMENT, BY_RULER) == NEVER


# --- council (D-164) ---------------------------------------------------------

#: The charter question about the council and its options.
COUNCIL = "council_exists"

NO_COUNCIL, ELECTED_COUNCIL, APPOINTED_COUNCIL = "none", "elected", "appointed"

#: Who proposes a law: the ruler or the council.
LAWMAKER = "lawmaker"

BY_COUNCIL = "council"

#: Voter circles. As strings, because there will be more of them along with the charter.
CITIZENS, COUNCIL_VOTERS = "citizens", "council"


class NoCouncil(VoteError):
    """There is no council in this city: the charter answered "no council"."""
