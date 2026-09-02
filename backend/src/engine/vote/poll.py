# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The ballot box (D-162, D-163, D-164): a vote opens over the charter's
rules, voices are cast by the right circle, and the close executes what
passed -- a law, an election, a recall, an amendment, a council. One box
for all of them, so no outcome has a second door.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Constants, current, current_catalog
from src.constants import registry as R
from src.engine import city as town
from src.engine import events
from src.engine.jobs import enqueue, handler
from src.engine.vote._base import (
    AMENDMENT,
    AMENDMENT_THRESHOLD,
    BY_RULER,
    COUNCIL_VOTERS,
    ELECTED_COUNCIL,
    QUORUM,
    SIMPLE,
    THRESHOLD,
    Closed,
    NoCouncil,
    NotCandidate,
    NotElective,
    NoVoice,
    Sealed,
    VoteError,
    answer,
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
from src.engine.vote.council import (
    council_mode,
    council_of,
    council_seats,
    in_council,
    seat,
    vacate,
    voters_for,
)
from src.models.city import City
from src.models.event import EventKind
from src.models.identity import Identity
from src.models.job import Job, JobKind
from src.models.vote import Ballot, Vote, VoteKind, VoteState
from src.units import PERCENT


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
        #: A seat whose holder left the city no longer votes (D-281), and the
        #: quorum may not count it: a chamber of three with one gone and a
        #: two-thirds quorum would lock for good, waiting for a vote nobody can
        #: cast. The same rule as `in_council`, asked of the seats already in
        #: hand rather than through it -- it would fetch them again per seat.
        seated: list[uuid.UUID] = []
        for place in await council_of(session, city):
            own = await town.citizenship(session, place.identity_id)
            if own is not None and own.city_id == city.id:
                seated.append(place.identity_id)
        return seated

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
    if vote.kind in (VoteKind.ELECTION, VoteKind.COUNCIL):
        #: The mirror of the refusal `choose` gives a yes-or-no poll. An
        #: election counts people, not approvals: every ballot in one names a
        #: candidate. A bare "yes" cast into it would be a ballot for nobody --
        #: counted among those who voted, choosing none of them -- and only the
        #: interface's own good manners kept them out, which is no rule at all
        #: for anyone sending commands straight down the socket (D-224).
        raise VoteError(key="vote-is-an-election")
    if vote.state is not VoteState.OPEN or vote.closes_at <= moment:
        raise Closed(key="vote-closed")
    if not await may_vote_in(session, city, identity.id, vote, now=moment):
        raise NoVoice(
            key="vote-no-voice-in-poll",
            voters="council" if vote.voters == COUNCIL_VOTERS else "citizens",
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
            #: The same step the authority's own road takes: writing the law is
            #: not all that follows a decision -- the tariff has to reach the
            #: meter, and the world has to be told which rule moved and from
            #: what. Nobody is named as the actor: the decision is the city's,
            #: and whoever proposed it did not make it.
            await town.apply_law(
                session,
                current(),
                current_catalog(),
                city,
                str(poll.subject.get("law")),
                poll.subject.get("value"),
            )
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


async def mine(session: AsyncSession, identity_id: uuid.UUID) -> tuple[City | None, list[dict]]:
    """The polls of one's **own** city one has a voice in, and that city.

    A vote is participation and travels the Net (D-161): a citizen down a mine,
    on the road or on another planet is one of the electorate all the same, and
    the poll has to reach them there rather than wait at the town hall. What
    other cities decide is not their business, and a feed of it would be noise.

    One reader, two consumers -- the Net tab and the return digest -- because
    the answer is one and the same, and a second copy of the walk from a person
    to their city's ballot box would drift.

    The city comes back **beside** the polls rather than on each of them: the
    digest names it in its line, and a copy of the same word on every row would
    be a key the client can already derive (D-225) -- there is one citizenship
    to a person, and its city is in `look`.
    """
    own = await town.citizenship(session, identity_id)
    if own is None:
        return None, []
    native = await town.by_id(session, own.city_id)
    if native is None:  # pragma: no cover -- citizenship in a city that is gone
        return None, []
    return native, [poll for poll in await view(session, native, identity_id) if poll["may_vote"]]


def unanswered(polls: list[dict]) -> list[dict]:
    """Those of them still waiting for this person: no ballot cast in either shape.

    A yes-or-no poll is answered by `mine`, an election by `choice`; one is
    always empty in the other's kind, so both are asked.
    """
    return [poll for poll in polls if poll["mine"] is None and poll["choice"] is None]


async def waiting(
    session: AsyncSession, identity_id: uuid.UUID, *, now: datetime | None = None
) -> int:
    """How many polls want this person's answer: the count on the Net tab.

    Counted, not assembled. `look` asks this on every read, and every citizen
    rereads `look` whenever anything happens in their city -- so building the
    whole ballot card of every poll (tally, candidates, each candidate's name)
    to arrive at a single number would put a dozen queries on the hottest path
    in the game. Instead one query finds the open polls of one's own city with
    no ballot of one's own in them, and the census is asked only of those.

    Two queries where nothing is running, one for somebody with no city -- and
    that is the usual state of the world.
    """
    moment = now or datetime.now(UTC)
    own = await town.citizenship(session, identity_id)
    if own is None:
        return 0
    #: No ballot of one's own is the whole test: a yes-or-no answer and a name
    #: chosen in an election are the same row, so the poll one has answered
    #: either way is not waiting.
    open_ = (
        (
            await session.execute(
                select(Vote).where(
                    Vote.city_id == own.city_id,
                    Vote.state == VoteState.OPEN,
                    ~select(Ballot.id)
                    .where(Ballot.vote_id == Vote.id, Ballot.identity_id == identity_id)
                    .exists(),
                )
            )
        )
        .scalars()
        .all()
    )
    if not open_:
        return 0
    city = await town.by_id(session, own.city_id)
    if city is None:  # pragma: no cover -- citizenship in a city that is gone
        return 0
    counted = 0
    for poll in open_:
        if await may_vote_in(session, city, identity_id, poll, now=moment):
            counted += 1
    return counted


async def view(session: AsyncSession, city: City, identity_id: uuid.UUID) -> list[dict]:
    """Ongoing polls through the client's eyes: subject, deadlines and own vote."""
    result: list[dict] = []
    for poll in await open_votes(session, city):
        pro, contra = await standing(session, poll)
        ballot = (
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
                        #: Whether this one is the asker. The client cannot work
                        #: it out: it knows its own name and nothing else, and a
                        #: name is not an identity (two people may share one).
                        #: Without it the "Выдвинуться" button stayed offered to
                        #: somebody already standing, and the second press is a
                        #: refusal the interface promised.
                        #:
                        #: Not `mine`: the poll around it already carries a
                        #: `mine`, and there it means "my ballot, yes or no".
                        #: One word with two meanings in one answer.
                        "own": raw_ == str(identity_id),
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
                    if ballot is None or ballot.choice_identity_id is None
                    else str(ballot.choice_identity_id)
                ),
                "closes_at": poll.closes_at.isoformat(),
                "threshold": poll.threshold,
                "quorum": float(poll.quorum_share),
                "electorate": poll.electorate,
                "yes": pro,
                "no": contra,
                "mine": None if ballot is None else ballot.yes,
                "voters": poll.voters,
                "may_vote": await may_vote_in(session, city, identity_id, poll),
            }
        )
    return result


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
        raise NotElective(key="vote-ruler-not-elected")
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
        raise NotElective(key="vote-no-recall")
    ruler = await town.ruler(session, city)
    if ruler is None:
        raise VoteError(key="vote-no-ruler-to-recall")
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
        raise NotCandidate(key="vote-not-an-election")
    if vote.state is not VoteState.OPEN:
        raise NotCandidate(key="vote-nominate-while-open")
    if vote.closes_at <= moment:
        raise Closed(key="vote-election-closed")
    if not await may_vote_in(session, city, who.id, vote, now=moment):
        raise NotCandidate(
            key="vote-nominee-needs-voice",
            voters="council" if vote.voters == COUNCIL_VOTERS else "citizens",
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
        raise VoteError(key="vote-is-a-poll")
    if vote.state is not VoteState.OPEN or vote.closes_at <= moment:
        raise Closed(key="vote-closed")
    if not await may_vote_in(session, city, identity.id, vote, now=moment):
        raise NoVoice(
            key="vote-no-voice-in-election",
            voters="council" if vote.voters == COUNCIL_VOTERS else "citizens",
        )
    if str(candidate.id) not in (vote.subject.get("candidates") or []):
        raise NotCandidate(key="vote-not-nominated", who=candidate.name)

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
        raise Sealed(key="city-charter-sealed")
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
        raise NoCouncil(key="vote-council-not-elected")
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
