# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The chamber: who sits in it, what it decides, and what it cannot lock (D-164, D-165).

A council is a smaller electorate over the same box. The charter says how many
seats there are and how they are filled, and from then on the council may hold
the approval of laws (D-164) and the turnover of power (D-165) instead of the
whole city.

The edge cases are the point of the file: a seat whose holder is no longer a
citizen does not vote, a councillor who left does not lock the quorum behind
them, and a chamber that is empty -- the charter names a council and nobody
sits in it -- does not lock the city out of its own laws and its own authority.
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import city as town
from src.engine import vote
from src.models.vote import VoteState
from vote_kit import LAW, VALUE, _bring, _city, _convene, _resident

# --- council (D-164) ---------------------------------------------------------


async def _with_council(session: AsyncSession, catalog: Catalog, *, seats: int, how: str):
    city, core, ruler, body = await _city(session, catalog)
    city.charter = {**city.charter, vote.COUNCIL: how}
    city.charter_params = {vote.COUNCIL: seats}
    await session.flush()
    return city, core, ruler, body


async def test_ruler_seats_council_no_more_than_charter_seats(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, core, ruler, _ = await _with_council(
        session, catalog, seats=1, how=vote.APPOINTED_COUNCIL
    )
    first, _ = await _resident(session, core, city, "Советник")
    second, _ = await _resident(session, core, city, "Второй")

    await vote.appoint_to_council(session, city, ruler, first)
    assert await vote.in_council(session, city, first.id)

    with pytest.raises(vote.NoCouncil):
        await vote.appoint_to_council(session, city, ruler, second)


async def test_elective_council_fills_as_many_as_seats(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, core, ruler, _ = await _with_council(session, catalog, seats=2, how=vote.ELECTED_COUNCIL)
    a, _ = await _resident(session, core, city, "А")
    b, _ = await _resident(session, core, city, "Б")
    v_, _ = await _resident(session, core, city, "В")

    election = await vote.open_council_election(session, constants, city, ruler)
    for who in (a, b, v_):
        await vote.nominate(session, city, who, election)
    await vote.choose(session, city, ruler, election, a)
    await vote.choose(session, city, a, election, a)
    await vote.choose(session, city, b, election, b)
    await vote.choose(session, city, v_, election, v_)

    await _bring(session, election)
    places = {m.identity_id for m in await vote.council_of(session, city)}
    assert a.id in places, "больше всех голосов — место"
    assert len(places) == 2, "мест ровно столько, сколько назначил устав"


async def test_council_approves_law_instead_of_citizens(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The same machine, another voter circle (D-164)."""
    city, core, ruler, body = await _with_council(
        session, catalog, seats=2, how=vote.APPOINTED_COUNCIL
    )
    city.charter = {**city.charter, vote.APPROVAL: vote.BY_COUNCIL}
    await session.flush()
    councillor, _ = await _resident(session, core, city, "Советник")
    stranger, _ = await _resident(session, core, city, "Горожанин")
    await vote.appoint_to_council(session, city, ruler, councillor)

    poll = await _convene(session, constants, catalog, city, ruler, body)
    assert poll.voters == vote.COUNCIL_VOTERS
    assert poll.electorate == 1, "кворум считается от совета, а не от города"

    with pytest.raises(vote.NoVoice):
        await vote.cast(session, city, stranger, poll, True)
    await vote.cast(session, city, councillor, poll, True)

    await _bring(session, poll)
    assert (city.laws or {}).get(LAW) == VALUE


async def test_a_seat_without_citizenship_does_not_vote(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A council seat is a citizen's seat (D-281).

    The vote belongs to citizens (D-160), and the council was the hole in that:
    its member kept voting in a city they had left. The row itself stays --
    who sat when is a matter for the court -- but it stops speaking, and with
    it goes the right to propose a law.
    """
    city, core, ruler, body = await _with_council(
        session, catalog, seats=2, how=vote.APPOINTED_COUNCIL
    )
    city.charter = {**city.charter, vote.APPROVAL: vote.BY_COUNCIL}
    await session.flush()
    councillor, _ = await _resident(session, core, city, "Советник")
    await vote.appoint_to_council(session, city, ruler, councillor)

    poll = await _convene(session, constants, catalog, city, ruler, body)
    await town.leave(session, councillor)

    assert not await vote.in_council(session, city, councillor.id)
    assert not await vote.may_propose(session, city, councillor.id)
    with pytest.raises(vote.NoVoice):
        await vote.cast(session, city, councillor, poll, True)


async def test_a_gone_councillor_does_not_lock_the_quorum(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The circle and the quorum are one number (D-164, D-281).

    A seat whose holder left the city cannot vote, so counting it into the
    electorate would lock the chamber: a council of two with a quorum of the
    whole would wait for ever on a vote nobody can cast.
    """
    city, core, ruler, body = await _with_council(
        session, catalog, seats=2, how=vote.APPOINTED_COUNCIL
    )
    city.charter = {**city.charter, vote.APPROVAL: vote.BY_COUNCIL, vote.QUORUM: "share"}
    city.charter_params = {**city.charter_params, vote.QUORUM: 100}
    await session.flush()
    staying, _ = await _resident(session, core, city, "Оставшийся")
    leaving, _ = await _resident(session, core, city, "Ушедший")
    await vote.appoint_to_council(session, city, ruler, staying)
    await vote.appoint_to_council(session, city, ruler, leaving)
    await town.leave(session, leaving)

    poll = await _convene(session, constants, catalog, city, ruler, body)
    assert poll.electorate == 1, "в круг идут места, за которыми стоит гражданин"

    await vote.cast(session, city, staying, poll, True)
    await _bring(session, poll)
    assert poll.state is VoteState.PASSED
    assert (city.laws or {}).get(LAW) == VALUE


async def test_council_member_proposes_law_without_laws_right(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """ "The council proposes" means as many legislators as seats."""
    city, core, ruler, _ = await _with_council(
        session, catalog, seats=2, how=vote.APPOINTED_COUNCIL
    )
    city.charter = {**city.charter, vote.LAWMAKER: vote.BY_COUNCIL}
    await session.flush()
    councillor, councillor_body = await _resident(session, core, city, "Советник")

    #: Without a council seat there are no rights at all.
    with pytest.raises(town.NotAllowed):
        await town.set_law(
            session,
            constants,
            catalog,
            councillor,
            city,
            LAW,
            VALUE,
            body=councillor_body,
        )

    await vote.appoint_to_council(session, city, ruler, councillor)
    await town.set_law(
        session,
        constants,
        catalog,
        councillor,
        city,
        LAW,
        VALUE,
        body=councillor_body,
    )
    assert await vote.open_votes(session, city), "внесённое ушло на голосование"


async def test_empty_chamber_does_not_lock_laws(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A council of zero seats equals no council (D-164).

    The charter gave approval to the chamber, and there is no chamber: the law
    is applied by whoever proposed it. Otherwise a city that answered "the
    council approves" and did not assemble one would stay without legislation forever.
    """
    city, _, ruler, body = await _with_council(session, catalog, seats=0, how=vote.ELECTED_COUNCIL)
    city.charter = {**city.charter, vote.APPROVAL: vote.BY_COUNCIL}
    await session.flush()

    await town.set_law(session, constants, catalog, ruler, city, LAW, VALUE, body=body)
    assert not await vote.open_votes(session, city), "голосовать некому"
    assert (city.laws or {}).get(LAW) == VALUE


async def test_cities_without_council_do_not_assemble_it(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, core, ruler, _ = await _city(session, catalog)
    someone, _ = await _resident(session, core, city, "Кто-то")
    with pytest.raises(vote.NoCouncil):
        await vote.appoint_to_council(session, city, ruler, someone)
    with pytest.raises(vote.NoCouncil):
        await vote.open_council_election(session, constants, city, ruler)


# --- the council elects and recalls the ruler (D-165) ------------------------


async def test_council_elects_ruler(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A parliamentary republic differs from direct democracy by the circle."""
    city, core, ruler, _ = await _with_council(
        session, catalog, seats=2, how=vote.APPOINTED_COUNCIL
    )
    city.charter = {**city.charter, vote.SELECTION: vote.ELECTED_BY_COUNCIL}
    await session.flush()
    councillor, _ = await _resident(session, core, city, "Советник")
    townsman, _ = await _resident(session, core, city, "Горожанин")
    await vote.appoint_to_council(session, city, ruler, councillor)

    election = await vote.open_election(session, constants, city, ruler)
    assert election.voters == vote.COUNCIL_VOTERS
    assert election.electorate == 1, "кворум считается от палаты"

    with pytest.raises(vote.NotCandidate):
        await vote.nominate(session, city, townsman, election)
    await vote.nominate(session, city, councillor, election)
    with pytest.raises(vote.NoVoice):
        await vote.choose(session, city, townsman, election, councillor)
    await vote.choose(session, city, councillor, election, councillor)

    await _bring(session, election)
    new = await town.ruler(session, city)
    assert new is not None and new.identity_id == councillor.id


async def test_council_recalls_ruler(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, core, ruler, _ = await _with_council(
        session, catalog, seats=1, how=vote.APPOINTED_COUNCIL
    )
    city.charter = {**city.charter, vote.RECALL_RULE: vote.RECALL_BY_COUNCIL}
    await session.flush()
    councillor, _ = await _resident(session, core, city, "Советник")
    await vote.appoint_to_council(session, city, ruler, councillor)

    recall = await vote.open_recall(session, constants, city, councillor)
    assert recall.voters == vote.COUNCIL_VOTERS
    await vote.cast(session, city, councillor, recall, True)

    await _bring(session, recall)
    assert recall.state is VoteState.PASSED
    assert await town.ruler(session, city) is None


async def test_empty_chamber_does_not_lock_authority(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A charter that cannot be executed literally is executed by meaning (D-165)."""
    city, _, ruler, _ = await _with_council(session, catalog, seats=0, how=vote.ELECTED_COUNCIL)
    city.charter = {**city.charter, vote.SELECTION: vote.ELECTED_BY_COUNCIL}
    await session.flush()

    election = await vote.open_election(session, constants, city, ruler)
    assert election.voters == vote.CITIZENS, "выбирает весь город, раз палаты нет"
