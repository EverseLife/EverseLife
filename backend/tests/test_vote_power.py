# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Turnover of power by vote: election, recall, term of office, charter (D-162, D-163).

The same ballot box as the law poll next door -- same census, same quorum,
same threshold -- and a different subject, which is the whole point of D-162:
the machine is one, the subjects are many. What is checked here is that the
outcome **executes itself**: the elected receive the previous ruler's set of
rights, the recalled lose the office and an election follows, an expired term
takes the post down without anybody's hand, and a charter amendment obeys the
bar the charter set for changing itself.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import city as town
from src.engine import vote
from src.models.vote import VoteKind, VoteState
from vote_kit import _bring, _city, _resident

# --- election and recall (D-162) ---------------------------------------------


async def _elective(session: AsyncSession, catalog: Catalog, **charter):
    """A city that gave power to elections."""
    return await _city(
        session,
        catalog,
        **{vote.SELECTION: vote.ELECTED, vote.RECALL_RULE: vote.RECALL_BY_CITIZENS},
        **charter,
    )


async def test_elected_gets_authority(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The office passes by tally, not by appointment (D-162)."""
    city, core, ruler, _ = await _elective(session, catalog)
    rival, _ = await _resident(session, core, city, "Соперник")
    voter, _ = await _resident(session, core, city, "Избиратель")

    election = await vote.open_election(session, constants, city, ruler)
    await vote.nominate(session, city, ruler, election)
    await vote.nominate(session, city, rival, election)
    await vote.choose(session, city, voter, election, rival)
    await vote.choose(session, city, rival, election, rival)
    await vote.choose(session, city, ruler, election, ruler)

    await _bring(session, election)
    new = await town.ruler(session, city)
    assert new is not None and new.identity_id == rival.id
    assert await town.may(session, rival.id, city, "laws"), (
        "избранный получает набор прежнего правителя"
    )
    assert not await town.may(session, ruler.id, city, "laws"), "прежняя должность сложена"


async def test_the_view_names_the_asker_among_the_candidates(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A name is not an identity, so the client cannot tell which candidate is
    itself -- and while it could not, the interface went on offering
    "Выдвинуться" to somebody already standing, and the second press was a
    refusal the interface had promised."""
    city, core, ruler, _ = await _elective(session, catalog)
    rival, _ = await _resident(session, core, city, "Соперник")
    election = await vote.open_election(session, constants, city, ruler)
    await vote.nominate(session, city, ruler, election)
    await vote.nominate(session, city, rival, election)

    seen = await vote.view(session, city, ruler.id)
    (poll,) = [one for one in seen if one["id"] == str(election.id)]
    assert {one["name"]: one["own"] for one in poll["candidates"]} == {
        ruler.name: True,
        rival.name: False,
    }
    #: And from the other side of the same election.
    seen = await vote.view(session, city, rival.id)
    (poll,) = [one for one in seen if one["id"] == str(election.id)]
    assert {one["name"]: one["own"] for one in poll["candidates"]} == {
        ruler.name: False,
        rival.name: True,
    }


async def test_only_citizens_nominated(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, core, ruler, _ = await _elective(session, catalog)
    election = await vote.open_election(session, constants, city, ruler)
    guest, _ = await _resident(session, core, city, "Гость", citizen=False)
    with pytest.raises(vote.NotCandidate):
        await vote.nominate(session, city, guest, election)


async def test_election_takes_no_yes_or_no_ballot(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The mirror of the refusal `choose` gives a yes-or-no poll.

    An election counts people, not approvals: a bare "yes" cast into one is a
    ballot for nobody, counted among those who voted and choosing none of them.
    Only the interface's good manners kept such a ballot out, and commands go
    down the socket raw (D-224).
    """
    city, core, ruler, _ = await _elective(session, catalog)
    election = await vote.open_election(session, constants, city, ruler)
    await vote.nominate(session, city, ruler, election)

    with pytest.raises(vote.VoteError):
        await vote.cast(session, city, ruler, election, True)

    #: And the tally is untouched by the attempt.
    pro, contra = await vote.standing(session, election)
    assert (pro, contra) == (0, 0)


async def test_no_vote_for_non_nominee(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, core, ruler, _ = await _elective(session, catalog)
    election = await vote.open_election(session, constants, city, ruler)
    stranger, _ = await _resident(session, core, city, "Посторонний")
    with pytest.raises(vote.NotCandidate):
        await vote.choose(session, city, ruler, election, stranger)


async def test_tie_does_not_transfer_authority(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Sortition is a separate charter option, and inventing it is not allowed (D-065)."""
    city, core, ruler, _ = await _elective(session, catalog)
    rival, _ = await _resident(session, core, city, "Соперник")
    election = await vote.open_election(session, constants, city, ruler)
    await vote.nominate(session, city, ruler, election)
    await vote.nominate(session, city, rival, election)
    await vote.choose(session, city, ruler, election, ruler)
    await vote.choose(session, city, rival, election, rival)

    await _bring(session, election)
    assert election.state is VoteState.FAILED
    remained = await town.ruler(session, city)
    assert remained is not None and remained.identity_id == ruler.id


async def test_city_without_elective_charter_does_not_convene_them(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, _, ruler, _ = await _city(session, catalog)
    with pytest.raises(vote.NotElective):
        await vote.open_election(session, constants, city, ruler)


async def test_recall_removes_ruler_and_convenes_election(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The city does not stay without authority longer than one poll (D-162)."""
    city, core, ruler, _ = await _elective(session, catalog)
    unhappy, _ = await _resident(session, core, city, "Недовольный")
    one_more, _ = await _resident(session, core, city, "Тоже недовольный")

    recall = await vote.open_recall(session, constants, city, unhappy)
    await vote.cast(session, city, unhappy, recall, True)
    await vote.cast(session, city, one_more, recall, True)
    await vote.cast(session, city, ruler, recall, False)

    await _bring(session, recall)
    assert recall.state is VoteState.PASSED
    assert await town.ruler(session, city) is None, "должность снята"
    going = await vote.open_votes(session, city)
    assert [g.kind for g in going] == [VoteKind.ELECTION], "выборы созваны сразу"


async def test_recall_forbidden_by_charter_not_convened(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, core, ruler, _ = await _city(session, catalog)
    someone, _ = await _resident(session, core, city, "Кто-то")
    with pytest.raises(vote.NotElective):
        await vote.open_recall(session, constants, city, someone)


# --- term of office and charter amendment (D-163) ----------------------------


async def test_term_of_office_removes_post_itself(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """ "Elected for thirty days" must not mean "until they remember themselves"."""
    from sqlalchemy import select as _select

    from src.models.job import Job, JobKind, JobState

    city, core, ruler, _ = await _elective(session, catalog)
    city.charter = {**city.charter, vote.TERM: vote.FIXED_TERM}
    city.charter_params = {vote.TERM: 30}
    await session.flush()

    successor, _ = await _resident(session, core, city, "Сменщик")
    election = await vote.open_election(session, constants, city, ruler)
    await vote.nominate(session, city, successor, election)
    await vote.choose(session, city, ruler, election, successor)
    await _bring(session, election)

    term = (
        (
            await session.execute(
                _select(Job).where(
                    Job.kind == JobKind.RULER_TERM.value, Job.state == JobState.PENDING
                )
            )
        )
        .scalars()
        .first()
    )
    assert term is not None, "срок поставлен при вступлении в должность"

    await town.term_ended(session, term)
    assert await town.ruler(session, city) is None, "должность снята по сроку"
    going = await vote.open_votes(session, city)
    assert VoteKind.ELECTION in [g.kind for g in going], "выборный город идёт на выборы"


async def test_charter_edited_by_vote(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The ruler does not forbid their own recall where the charter is given to citizens."""
    city, core, ruler, body = await _city(session, catalog)
    city.charter = {**city.charter, vote.AMENDMENT: "two_thirds"}
    await session.flush()
    supporter, _ = await _resident(session, core, city, "Сторонник")

    #: We change the recall from the default "not allowed" to "by citizens'
    #: vote": a ruler given the charter would not have done that to themselves.
    await town.set_charter(
        session,
        catalog,
        ruler,
        city,
        vote.RECALL_RULE,
        vote.RECALL_BY_CITIZENS,
        body=body,
    )
    assert city.charter[vote.RECALL_RULE] != vote.RECALL_BY_CITIZENS, (
        "правка ушла на голосование, а не применилась"
    )

    poll = (await vote.open_votes(session, city))[0]
    assert poll.kind is VoteKind.CHARTER
    assert poll.threshold == vote.TWO_THIRDS, "у конституции свой порог"

    await vote.cast(session, city, ruler, poll, True)
    await vote.cast(session, city, supporter, poll, True)
    await _bring(session, poll)
    assert city.charter[vote.RECALL_RULE] == vote.RECALL_BY_CITIZENS, "принятое применилось само"


async def test_two_thirds_not_reached(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, core, ruler, body = await _city(session, catalog)
    city.charter = {**city.charter, vote.AMENDMENT: "two_thirds"}
    await session.flush()
    before = city.charter[vote.RECALL_RULE]
    contra, _ = await _resident(session, core, city, "Против")

    await town.set_charter(
        session,
        catalog,
        ruler,
        city,
        vote.RECALL_RULE,
        vote.RECALL_BY_CITIZENS,
        body=body,
    )
    poll = (await vote.open_votes(session, city))[0]
    await vote.cast(session, city, ruler, poll, True)
    await vote.cast(session, city, contra, poll, False)

    await _bring(session, poll)
    assert poll.state is VoteState.FAILED
    assert city.charter[vote.RECALL_RULE] == before


async def test_sealed_charter_does_not_change(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`never` is executed literally: the charter cannot be opened from inside (D-163)."""
    city, _, ruler, body = await _city(session, catalog)
    city.charter = {**city.charter, vote.AMENDMENT: vote.NEVER}
    await session.flush()

    with pytest.raises(vote.Sealed):
        await town.set_charter(
            session,
            catalog,
            ruler,
            city,
            vote.RECALL_RULE,
            vote.RECALL_BY_CITIZENS,
            body=body,
        )
