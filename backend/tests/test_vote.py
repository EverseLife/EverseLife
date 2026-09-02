# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The citizens' poll over a law: term, census, quorum, threshold (D-036, D-161).

Checked is what the procedure was introduced for:

* a city that gave approval to citizens does not change a law by the ruler's stroke of a pen;
* only citizens have a vote, and only those meeting the charter's census;
* conditions are captured at opening: the ruler does not raise the threshold on the fly;
* the result applies **itself**, by a journal job, without anybody's participation.

The rest of the ballot box is next door, cut off this file when it crossed the
800-line bar: turnover of power in `test_vote_power.py` (election, recall, term
of office, charter amendment), the chamber in `test_vote_council.py`. The
ground all three stand on -- the voting city and the law put to it -- is in
`vote_kit.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import energy, ledger, vote
from src.models.event import Event, EventKind
from src.models.ledger import AccountKind, PostingReason
from src.models.vote import VoteState
from src.units import money
from vote_kit import LAW, VALUE, _bring, _city, _convene, _resident

# --- convening ---------------------------------------------------------------


async def test_law_goes_to_vote_not_applied(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`law_approval: citizens` -- the ruler convenes rather than decides."""
    city, _, ruler, body = await _city(session, catalog)
    poll = await _convene(session, constants, catalog, city, ruler, body)

    assert (city.laws or {}).get(LAW) != VALUE, "закон ещё не принят"
    assert poll.subject == {"law": LAW, "value": VALUE}
    assert poll.state is VoteState.OPEN


async def test_poll_term_from_vault(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, _, ruler, body = await _city(session, catalog)
    poll = await _convene(session, constants, catalog, city, ruler, body)
    lasts = poll.closes_at - poll.opened_at
    assert lasts == pytest.approx(
        timedelta(hours=constants[R.VOTE_DURATION]), abs=timedelta(seconds=2)
    )


# --- who has a vote ----------------------------------------------------------


async def test_citizens_vote(session: AsyncSession, constants: Constants, catalog: Catalog) -> None:
    """Without this democracy is a multi-account contest (01-government-forms)."""
    city, core, ruler, body = await _city(session, catalog)
    poll = await _convene(session, constants, catalog, city, ruler, body)
    guest, _ = await _resident(session, core, city, "Гость", citizen=False)

    with pytest.raises(vote.NoVoice):
        await vote.cast(session, city, guest, poll, True)
    await vote.cast(session, city, ruler, poll, True)
    assert await vote.standing(session, poll) == (1, 0)


async def test_residency_census(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Yesterday's citizen does not decide the city's fate if the charter says so."""
    city, core, ruler, body = await _city(session, catalog, **{vote.QUALIFICATION: vote.RESIDENCE})
    city.charter_params = {vote.QUALIFICATION: 30}
    await session.flush()

    newcomer, _ = await _resident(session, core, city, "Новичок")
    assert not await vote.may_vote(session, city, newcomer.id)

    oldtimer = await town.citizenship(session, ruler.id)
    oldtimer.since = datetime.now(UTC) - timedelta(days=60)
    await session.flush()
    assert await vote.may_vote(session, city, ruler.id)


async def test_property_census(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, core, _, _ = await _city(session, catalog, **{vote.QUALIFICATION: vote.PROPERTY})
    city.charter_params = {vote.QUALIFICATION: 100}
    await session.flush()

    poor_, _ = await _resident(session, core, city, "Бедный")
    wealthy, _ = await _resident(session, core, city, "Богатый")
    account = await ledger.account_for(session, AccountKind.IDENTITY, wealthy.id)
    genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
    await ledger.transfer(
        session,
        PostingReason.GENESIS,
        debit=genesis.id,
        credit=account.id,
        amount=money(500),
        memo={},
    )

    assert not await vote.may_vote(session, city, poor_.id)
    assert await vote.may_vote(session, city, wealthy.id)


# --- tally -------------------------------------------------------------------


async def test_result_applied_itself_on_term(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The journal job counts and applies -- without anybody's participation."""
    city, core, ruler, body = await _city(session, catalog)
    poll = await _convene(session, constants, catalog, city, ruler, body)
    pro_, _ = await _resident(session, core, city, "Сторонник")
    await vote.cast(session, city, ruler, poll, True)
    await vote.cast(session, city, pro_, poll, True)

    await _bring(session, poll)
    assert poll.state is VoteState.PASSED
    assert (city.laws or {}).get(LAW) == VALUE, "закон принят сам"


async def test_a_passed_law_is_carried_through_and_announced(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The citizens' road ends where the ruler's own does: in the world.

    Closing a poll used to write the value into `city.laws` and stop there --
    so a tariff decided by the city never reached the meter that charges by
    it, and nothing said the law had moved. Both roads take the same step now.
    """
    city, core, ruler, body = await _city(session, catalog)
    pool = await energy.pool_of(session, constants, core)
    assert pool is not None

    await town.set_law(session, constants, catalog, ruler, city, "energy_tariff", "9", body=body)
    (poll,) = await vote.open_votes(session, city)
    supporter, _ = await _resident(session, core, city, "Сторонник")
    await vote.cast(session, city, ruler, poll, True)
    await vote.cast(session, city, supporter, poll, True)

    await _bring(session, poll)
    assert poll.state is VoteState.PASSED
    await session.refresh(pool)
    assert float(pool.tariff) == 9, "the city decided the tariff and the meter never heard"

    told = (
        (await session.execute(select(Event).where(Event.kind == EventKind.CITY_LAW_SET.value)))
        .scalars()
        .all()
    )
    assert len(told) == 1, "a law changed by the city was announced once"
    assert told[0].payload["law"] == "energy_tariff"
    assert told[0].payload["now"] == "9"
    #: Nobody decided alone: the proposer convened, the citizens decided.
    assert told[0].actor_identity_id is None


async def test_no_majority_law_fails(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, core, ruler, body = await _city(session, catalog)
    poll = await _convene(session, constants, catalog, city, ruler, body)
    contra1, _ = await _resident(session, core, city, "Против")
    contra2, _ = await _resident(session, core, city, "Тоже против")
    await vote.cast(session, city, ruler, poll, True)
    await vote.cast(session, city, contra1, poll, False)
    await vote.cast(session, city, contra2, poll, False)

    await _bring(session, poll)
    assert poll.state is VoteState.FAILED
    assert (city.laws or {}).get(LAW) != VALUE


async def test_quorum_not_met(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A minority does not decide for the city if the charter requires a quorum."""
    city, core, ruler, body = await _city(session, catalog, **{vote.QUORUM: "share"})
    city.charter_params = {vote.QUORUM: 60}
    await session.flush()
    for number in range(4):
        await _resident(session, core, city, f"Гражданин{number}")

    poll = await _convene(session, constants, catalog, city, ruler, body)
    assert poll.electorate == 5
    await vote.cast(session, city, ruler, poll, True)

    await _bring(session, poll)
    assert poll.state is VoteState.FAILED, "один голос из пяти — не кворум"


async def test_conditions_captured_at_opening(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The ruler does not raise the threshold on seeing they are losing (D-161)."""
    city, core, ruler, body = await _city(session, catalog)
    poll = await _convene(session, constants, catalog, city, ruler, body)
    assert poll.threshold == vote.SIMPLE

    city.charter = {**city.charter, vote.THRESHOLD: vote.UNANIMOUS}
    await session.flush()

    contra, _ = await _resident(session, core, city, "Против")
    await vote.cast(session, city, ruler, poll, True)
    await vote.cast(session, city, contra, poll, False)
    supporter, _ = await _resident(session, core, city, "Сторонник")
    await vote.cast(session, city, supporter, poll, True)

    await _bring(session, poll)
    assert poll.state is VoteState.PASSED, "судят по правилам созыва"


async def test_can_change_mind_before_term(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, _, ruler, body = await _city(session, catalog)
    poll = await _convene(session, constants, catalog, city, ruler, body)
    await vote.cast(session, city, ruler, poll, True)
    await vote.cast(session, city, ruler, poll, False)
    assert await vote.standing(session, poll) == (0, 1), "голос один"


async def test_late_vote_not_accepted(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, _, ruler, body = await _city(session, catalog)
    poll = await _convene(session, constants, catalog, city, ruler, body)
    late = poll.closes_at + timedelta(minutes=1)
    with pytest.raises(vote.Closed):
        await vote.cast(session, city, ruler, poll, True, now=late)
