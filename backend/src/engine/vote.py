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

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current
from src.constants import registry as R
from src.engine import city as town
from src.engine import events, ledger
from src.engine.errors import Refusal
from src.engine.jobs import enqueue, handler
from src.models.city import City, CouncilSeat, Power
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.job import Job, JobKind
from src.models.ledger import AccountKind
from src.models.vote import Ballot, Vote, VoteKind, VoteState
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
        account = await ledger.account_for(session, AccountKind.IDENTITY, identity_id)
        return await ledger.balance(session, account.id) >= money(param(city, QUALIFICATION))
    #: The treasury-contribution census is not enforced: contribution is not
    #: tracked (D-161). Such a city votes with all citizens rather than locking up.
    return True


async def electorate(
    session: AsyncSession,
    city: City,
    *,
    now: datetime | None = None,
    voters: str = "citizens",
) -> list[uuid.UUID]:
    """Who has a vote now. The quorum is counted from their number.

    The circle comes in two kinds: all citizens by census, or council members (D-164).
    """

    if voters == COUNCIL_VOTERS:
        return [place.identity_id for place in await council_of(session, city)]

    have_: list[uuid.UUID] = []
    for entry in await town.citizens_of(session, city):
        if await may_vote(session, city, entry.identity_id, now=now):
            have_.append(entry.identity_id)
    return have_


async def open_law(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity,
    law_id: str,
    value,
    *,
    now: datetime | None = None,
) -> Vote:
    """Convene a poll on a code-law. The result applies itself, on schedule."""
    return await _open(
        session,
        constants,
        city,
        by,
        kind=VoteKind.LAW,
        subject={"law": law_id, "value": value},
        now=now,
    )


async def _open(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity | None,
    *,
    kind: VoteKind,
    subject: dict,
    now: datetime | None = None,
) -> Vote:
    """Convening: conditions are captured here and do not change afterwards (D-161)."""
    moment = now or datetime.now(UTC)
    lap = voters_for(city, kind)
    eligible = await electorate(session, city, now=moment, voters=lap)
    closing = moment + timedelta(hours=constants[R.VOTE_DURATION])

    poll = Vote(
        city_id=city.id,
        kind=kind,
        subject=subject,
        opened_by_identity_id=None if by is None else by.id,
        threshold=answer(city, THRESHOLD, SIMPLE),
        quorum_share=Decimal(
            str(param(city, QUORUM) if answer(city, QUORUM, "none") != "none" else 0)
        ),
        electorate=len(eligible),
        voters=voters_for(city, kind),
        closes_at=closing,
    )
    session.add(poll)
    await session.flush()

    event = await events.record(
        session,
        EventKind.VOTE_OPENED,
        actor_identity_id=None if by is None else by.id,
        node_id=city.node_id,
        city_id=str(city.id),
        vote_id=str(poll.id),
        kind_of_vote=kind.value,
        subject=subject,
        electorate=poll.electorate,
        closes_at=closing.isoformat(),
    )
    await enqueue(
        session,
        JobKind.VOTE_CLOSE,
        closing,
        payload={"vote": str(poll.id)},
        dedup_key=f"vote.close:{poll.id}",
        cause_event_id=event.id,
    )
    return poll


async def cast(
    session: AsyncSession,
    city: City,
    identity: Identity,
    vote: Vote,
    yes: bool,
    *,
    now: datetime | None = None,
) -> Ballot:
    """Vote. Remote: a vote is participation, not governing."""
    moment = now or datetime.now(UTC)
    if vote.state is not VoteState.OPEN or vote.closes_at <= moment:
        raise Closed("голосование закрыто: опоздавший голос итога не меняет")
    if not await may_vote_in(session, city, identity.id, vote, now=moment):
        raise NoVoice(
            "голоса нет: в этом голосовании решают "
            + ("члены совета" if vote.voters == COUNCIL_VOTERS else "граждане")
        )

    ballot = (
        await session.execute(
            select(Ballot).where(Ballot.vote_id == vote.id, Ballot.identity_id == identity.id)
        )
    ).scalar_one_or_none()
    if ballot is None:
        ballot = Ballot(vote_id=vote.id, identity_id=identity.id, yes=yes)
        session.add(ballot)
    else:
        #: Changing one's mind before the deadline is allowed: the poll runs a
        #: day, and there is no point locking a person into their first decision.
        ballot.yes = yes
    await session.flush()
    await events.record(
        session,
        EventKind.VOTE_CAST,
        actor_identity_id=identity.id,
        city_id=str(city.id),
        vote_id=str(vote.id),
        yes=yes,
    )
    return ballot


async def may_vote_in(
    session: AsyncSession,
    city: City,
    identity_id: uuid.UUID,
    vote: Vote,
    *,
    now: datetime | None = None,
) -> bool:
    """Whether one has a vote **in this** poll: the circle was captured at convening (D-164)."""
    if vote.voters == COUNCIL_VOTERS:
        return await in_council(session, city, identity_id)
    return await may_vote(session, city, identity_id, now=now)


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


@handler(JobKind.VOTE_CLOSE)
async def close(session: AsyncSession, job: Job) -> None:
    """The term is up: we count the result and apply it ourselves (D-161)."""

    poll = await session.get(Vote, uuid.UUID(job.payload["vote"]))
    if poll is None or poll.state is not VoteState.OPEN:
        #: A job retry after a failure does not become a second decision.
        return

    city = await town.by_id(session, poll.city_id)
    pro, contra = await standing(session, poll)

    if poll.kind is VoteKind.ELECTION:
        reason = await _finish_election(session, poll, city)
        elapsed = reason.startswith("избран")
    elif poll.kind is VoteKind.COUNCIL:
        reason = await _finish_council(session, poll, city)
        elapsed = reason.startswith("избрано")
    else:
        elapsed, reason = passes(current(), poll, pro, contra)
        if elapsed and city is not None and poll.kind is VoteKind.LAW:
            law = str(poll.subject.get("law"))
            city.laws = {
                **(city.laws or {}),
                law: poll.subject.get("value"),
            }
        if poll.kind is VoteKind.RECALL and city is not None:
            await _finish_recall(session, poll, city, elapsed)
        if elapsed and poll.kind is VoteKind.CHARTER and city is not None:
            await _finish_charter(session, poll, city)

    poll.state = VoteState.PASSED if elapsed else VoteState.FAILED
    poll.closed_at = job.run_at
    await session.flush()

    await events.record(
        session,
        EventKind.VOTE_CLOSED,
        node_id=None if city is None else city.node_id,
        city_id=str(poll.city_id),
        vote_id=str(poll.id),
        passed=elapsed,
        why=reason,
        yes=pro,
        no=contra,
        electorate=poll.electorate,
    )


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


async def view(
    session: AsyncSession, catalog: Catalog, city: City, identity_id: uuid.UUID
) -> list[dict]:
    """Ongoing polls through the client's eyes: subject, deadlines and own vote."""
    result: list[dict] = []
    for poll in await open_votes(session, city):
        pro, contra = await standing(session, poll)
        mine = (
            await session.execute(
                select(Ballot).where(
                    Ballot.vote_id == poll.id,
                    Ballot.identity_id == identity_id,
                )
            )
        ).scalar_one_or_none()
        #: For an election the subject is a person: the client needs names, not keys.
        candidates = []
        if poll.kind in (VoteKind.ELECTION, VoteKind.COUNCIL):
            account = await tally(session, poll)
            for raw_ in poll.subject.get("candidates") or []:
                who = await session.get(Identity, uuid.UUID(raw_))
                candidates.append(
                    {
                        "id": raw_,
                        "name": None if who is None else who.name,
                        "votes": account.get(raw_, 0),
                    }
                )
        result.append(
            {
                "id": str(poll.id),
                "kind": poll.kind.value,
                "law": poll.subject.get("law"),
                "value": poll.subject.get("value"),
                "candidates": candidates,
                "choice": (
                    None
                    if mine is None or mine.choice_identity_id is None
                    else str(mine.choice_identity_id)
                ),
                "closes_at": poll.closes_at.isoformat(),
                "threshold": poll.threshold,
                "quorum": float(poll.quorum_share),
                "electorate": poll.electorate,
                "yes": pro,
                "no": contra,
                "mine": None if mine is None else mine.yes,
                "voters": poll.voters,
                "may_vote": await may_vote_in(session, city, identity_id, poll),
            }
        )
    return result


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


async def open_election(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity | None = None,
    *,
    now: datetime | None = None,
) -> Vote:
    """Convene a ruler election. Candidates nominate themselves while the poll runs."""
    if not elects_ruler(city):
        raise NotElective("устав города не отдал власть выборам: правитель определяется иначе")
    return await _open(
        session,
        constants,
        city,
        by,
        kind=VoteKind.ELECTION,
        subject={"candidates": []},
        now=now,
    )


async def open_recall(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity,
    *,
    now: datetime | None = None,
) -> Vote:
    """Convene a ruler recall. If it passes, the office is vacated and an election follows."""

    if not recallable(city):
        raise NotElective("устав города не допускает отзыва правителя")
    ruler = await town.ruler(session, city)
    if ruler is None:
        raise VoteError("отзывать некого: правителя нет")
    return await _open(
        session,
        constants,
        city,
        by,
        kind=VoteKind.RECALL,
        subject={"office": str(ruler.id), "who": str(ruler.identity_id)},
        now=now,
    )


async def nominate(
    session: AsyncSession, city: City, who: Identity, vote: Vote, *, now=None
) -> Vote:
    """Nominate yourself for ruler. Yourself, not on somebody's proposal."""
    moment = now or datetime.now(UTC)
    if vote.kind not in (VoteKind.ELECTION, VoteKind.COUNCIL):
        raise NotCandidate("это не выборы: выдвигаться некуда")
    if vote.state is not VoteState.OPEN:
        raise NotCandidate("выдвигаются, пока идут выборы")
    if vote.closes_at <= moment:
        raise Closed("выборы закрыты")
    if not await may_vote_in(session, city, who.id, vote, now=moment):
        raise NotCandidate(
            "выдвигается тот, у кого есть голос в этих выборах: "
            + ("члены совета" if vote.voters == COUNCIL_VOTERS else "граждане")
        )

    candidates = list(vote.subject.get("candidates") or [])
    if str(who.id) in candidates:
        return vote
    candidates.append(str(who.id))
    vote.subject = {**vote.subject, "candidates": candidates}
    await session.flush()
    await events.record(
        session,
        EventKind.VOTE_NOMINATED,
        actor_identity_id=who.id,
        city_id=str(city.id),
        vote_id=str(vote.id),
        who=who.name,
    )
    return vote


async def choose(
    session: AsyncSession,
    city: City,
    identity: Identity,
    vote: Vote,
    candidate: Identity,
    *,
    now: datetime | None = None,
) -> Ballot:
    """Cast a vote for a candidate. One vote: changing one's mind before the deadline is allowed."""
    moment = now or datetime.now(UTC)
    if vote.kind not in (VoteKind.ELECTION, VoteKind.COUNCIL):
        raise VoteError("это не выборы: здесь голосуют «за» или «против»")
    if vote.state is not VoteState.OPEN or vote.closes_at <= moment:
        raise Closed("голосование закрыто: опоздавший голос итога не меняет")
    if not await may_vote_in(session, city, identity.id, vote, now=moment):
        raise NoVoice(
            "голоса нет: в этих выборах решают "
            + ("члены совета" if vote.voters == COUNCIL_VOTERS else "граждане")
        )
    if str(candidate.id) not in (vote.subject.get("candidates") or []):
        raise NotCandidate(f"{candidate.name} не выдвигался")

    ballot = (
        await session.execute(
            select(Ballot).where(Ballot.vote_id == vote.id, Ballot.identity_id == identity.id)
        )
    ).scalar_one_or_none()
    if ballot is None:
        ballot = Ballot(
            vote_id=vote.id,
            identity_id=identity.id,
            yes=True,
            choice_identity_id=candidate.id,
        )
        session.add(ballot)
    else:
        ballot.choice_identity_id = candidate.id
    await session.flush()
    await events.record(
        session,
        EventKind.VOTE_CAST,
        actor_identity_id=identity.id,
        city_id=str(city.id),
        vote_id=str(vote.id),
        choice=candidate.name,
    )
    return ballot


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


async def _finish_election(session: AsyncSession, vote: Vote, city) -> str:
    """Tally the election: whoever has more votes is the ruler.

    An election has no threshold (D-162): demanding a majority of all cast
    would leave the city without a ruler with three candidates. The quorum is
    the common one.
    """

    account = await tally(session, vote)
    submitted = sum(account.values())
    quorum_needed = float(vote.quorum_share) / PERCENT * vote.electorate
    if submitted < quorum_needed:
        return "кворум не собран"
    if not account:
        return "не проголосовал никто"

    best = max(account.values())
    winners = [who for who, votes in account.items() if votes == best]
    if len(winners) > 1:
        #: A tie is not resolved by the engine: sortition is a separate charter
        #: option, and inventing it here is not allowed (D-065).
        return "ничья: победитель не определён"

    winner = await session.get(Identity, uuid.UUID(winners[0]))
    if winner is None:  # pragma: no cover -- the candidate lives among identities
        return "победитель исчез"
    await town.hand_over(session, city, winner)
    vote.subject = {**vote.subject, "winner": str(winner.id)}
    return f"избран {winner.name}"


async def _finish_recall(session: AsyncSession, vote: Vote, city, elapsed: bool) -> None:
    """The recall passed -- the office is vacated, and an election is convened at once."""

    if not elapsed:
        return
    await town.dismiss(session, city)
    if elects_ruler(city):
        await open_election(session, current(), city, None)


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


async def open_charter(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity,
    question_id: str,
    option_id: str,
    value: float | None = None,
    *,
    now: datetime | None = None,
) -> Vote:
    """Convene a poll on a charter amendment (D-163).

    The threshold comes from `charter_amendment`, not `law_threshold`: a city
    may pass laws by simple majority and require two thirds for the
    constitution -- the vault asks about that separately.
    """
    if sealed(city):
        raise Sealed("устав этого города не меняется: так решил он сам")
    poll = await _open(
        session,
        constants,
        city,
        by,
        kind=VoteKind.CHARTER,
        subject={"question": question_id, "option": option_id, "param": value},
        now=now,
    )
    poll.threshold = AMENDMENT_THRESHOLD[answer(city, AMENDMENT, BY_RULER)]
    await session.flush()
    return poll


async def _finish_charter(session: AsyncSession, vote: Vote, city) -> None:
    """The amendment passed: the charter answer changes as if the ruler gave it."""
    question = str(vote.subject.get("question"))
    charter = dict(city.charter or {})
    charter[question] = vote.subject.get("option")
    city.charter = charter
    value = vote.subject.get("param")
    if value is not None:
        params = dict(city.charter_params or {})
        params[question] = value
        city.charter_params = params
    await session.flush()
    await events.record(
        session,
        EventKind.CITY_CHARTER_SET,
        node_id=city.node_id,
        city_id=str(city.id),
        question=question,
        option=vote.subject.get("option"),
        by_vote=True,
    )


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


def council_mode(city: City) -> str:
    return answer(city, COUNCIL, NO_COUNCIL)


def council_seats(city: City) -> int:
    """How many seats the charter set. Zero seats equals no council."""
    return int(param(city, COUNCIL))


def has_council(city: City) -> bool:
    return council_mode(city) != NO_COUNCIL and council_seats(city) > 0


async def council_of(session: AsyncSession, city: City) -> list[CouncilSeat]:
    """Occupied council seats."""
    return list(
        (
            await session.execute(
                select(CouncilSeat).where(
                    CouncilSeat.city_id == city.id,
                    CouncilSeat.vacated_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )


async def in_council(session: AsyncSession, city: City, identity_id: uuid.UUID) -> bool:
    return any(place.identity_id == identity_id for place in await council_of(session, city))


def voters_for(city: City, kind: VoteKind) -> str:
    """Who votes on this subject (D-164, D-165).

    The circle is determined by the subject **and** the charter: the council
    approves a law if so stated; the ruler is elected and recalled by whoever
    the charter gave it to. Everything else is the citizens' business.

    An empty chamber locks neither laws nor authority: a city with zero seats
    decides itself, as a whole city, and a law is applied by whoever proposed
    it. A charter that cannot be executed literally is executed by meaning
    rather than blocking the city forever.
    """
    by_council = {
        VoteKind.LAW: answer(city, APPROVAL, "ruler") == BY_COUNCIL,
        VoteKind.ELECTION: answer(city, SELECTION, "founder") == ELECTED_BY_COUNCIL,
        VoteKind.RECALL: answer(city, RECALL_RULE, "never") == RECALL_BY_COUNCIL,
    }.get(kind, False)
    if by_council and has_council(city):
        return COUNCIL_VOTERS
    return CITIZENS


async def may_propose(session: AsyncSession, city: City, identity_id: uuid.UUID) -> bool:
    """Whether this person may propose laws (`lawmaker`).

    The `laws` right always proposes a law -- that is authority. The council is
    added to it when the charter answers "the council proposes": then there are
    as many legislators as seats, and the ruler is not the only one among them.
    """
    if answer(city, LAWMAKER, "ruler") != BY_COUNCIL:
        return False
    return await in_council(session, city, identity_id)


async def seat(session: AsyncSession, city: City, who: Identity, *, how: str) -> CouncilSeat:
    """Seat a person on the council. No more seats than the charter set."""
    if not has_council(city):
        raise NoCouncil("устав этого города не заводит совета")
    occupied_ = await council_of(session, city)
    if any(place.identity_id == who.id for place in occupied_):
        return next(m for m in occupied_ if m.identity_id == who.id)
    if len(occupied_) >= council_seats(city):
        raise NoCouncil(
            f"в совете {council_seats(city)} мест, и все заняты: сначала освободить место"
        )

    place = CouncilSeat(city_id=city.id, identity_id=who.id, how=how)
    session.add(place)
    await session.flush()
    await events.record(
        session,
        EventKind.COUNCIL_SEATED,
        actor_identity_id=who.id,
        node_id=city.node_id,
        city_id=str(city.id),
        who=who.name,
        how=how,
    )
    return place


async def vacate(session: AsyncSession, city: City, who: Identity) -> bool:
    """Vacate a seat. The record stays: who voted is a matter for the court."""
    for place in await council_of(session, city):
        if place.identity_id != who.id:
            continue
        place.vacated_at = datetime.now(UTC)
        await session.flush()
        await events.record(
            session,
            EventKind.COUNCIL_VACATED,
            node_id=city.node_id,
            city_id=str(city.id),
            who=who.name,
        )
        return True
    return False


async def appoint_to_council(
    session: AsyncSession, city: City, by: Identity, who: Identity
) -> CouncilSeat:
    """Appoint to the council. Only where the charter gave the seats to the ruler."""

    if council_mode(city) != APPOINTED_COUNCIL:
        raise NoCouncil("места этого совета не назначают: устав отдал их выборам")
    await town.require(session, by.id, city, Power.OFFICES)
    if not await may_vote(session, city, who.id):
        raise NoVoice("в совет садятся граждане, отвечающие цензу устава")
    return await seat(session, city, who, how=APPOINTED_COUNCIL)


async def open_council_election(
    session: AsyncSession,
    constants: Constants,
    city: City,
    by: Identity | None = None,
    *,
    now: datetime | None = None,
) -> Vote:
    """Convene a council election: as many win as there are seats."""
    if council_mode(city) != ELECTED_COUNCIL or council_seats(city) <= 0:
        raise NoCouncil("устав этого города не выбирает совет")
    return await _open(
        session,
        constants,
        city,
        by,
        kind=VoteKind.COUNCIL,
        subject={"candidates": [], "seats": council_seats(city)},
        now=now,
    )


async def _finish_council(session: AsyncSession, vote: Vote, city) -> str:
    """Tally the council election: seats go to those with more votes."""
    account = await tally(session, vote)
    submitted = sum(account.values())
    quorum_needed = float(vote.quorum_share) / PERCENT * vote.electorate
    if submitted < quorum_needed:
        return "кворум не собран"
    if not account:
        return "не проголосовал никто"

    seats = int(vote.subject.get("seats") or 0)
    #: More votes -- higher seat; on a tie the order is set by key, and that is
    #: not sortition: sortition is a separate charter option, absent here (D-162).
    winners = sorted(account.items(), key=lambda pair: (-pair[1], pair[0]))[:seats]

    #: The previous membership is vacated entirely: an election renews the
    #: chamber rather than appending to it.
    for place in await council_of(session, city):
        who = await session.get(Identity, place.identity_id)
        if who is not None:
            await vacate(session, city, who)
    planted = 0
    for raw_, _ in winners:
        who = await session.get(Identity, uuid.UUID(raw_))
        if who is None:  # pragma: no cover -- the candidate lives among identities
            continue
        await seat(session, city, who, how=ELECTED_COUNCIL)
        planted += 1
    return f"избрано мест: {planted}"
