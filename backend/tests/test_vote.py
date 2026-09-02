# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Citizens' vote: term, census, quorum, threshold (D-036, D-161).

Checked is what the procedure was introduced for:

* a city that gave approval to citizens does not change a law by the ruler's stroke of a pen;
* only citizens have a vote, and only those meeting the charter's census;
* conditions are captured at opening: the ruler does not raise the threshold on the fly;
* the result applies **itself**, by a journal job, without anybody's participation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import energy, ledger, vote, world
from src.models.city import Citizen
from src.models.event import Event, EventKind
from src.models.ledger import AccountKind, PostingReason
from src.models.vote import Vote, VoteKind, VoteState
from src.models.world import Layer
from src.units import money

LAW, VALUE = "tax_trade", "7"


async def _city(session: AsyncSession, catalog: Catalog, **charter):
    """A city that gave laws to citizens, and its ruler."""
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session,
        f"terra.city.{stamp}",
        "Вече",
        area_m2=1,
        layer=Layer.PLANET,
        parent=planet,
    )
    core = await world.create_node(
        session,
        f"terra.city.{stamp}.core",
        "Ядро",
        area_m2=100,
        parent=delegate,
        properties={"ring": 0},
    )
    city = await town.found(session, catalog, delegate, "Вече")
    core.owner_city_id = city.id
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, town.HALL, quality=65, origin="тест")
    city.charter = {**city.charter, vote.APPROVAL: vote.BY_CITIZENS, **charter}
    await session.flush()

    ruler, body = await _resident(session, core, city, "Правитель")
    await town.install_founder(session, city, ruler)
    return city, core, ruler, body


async def _resident(session: AsyncSession, node, city, name: str, *, citizen=True):
    identity = await world.create_identity(session, f"{name}-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    if citizen:
        session.add(Citizen(identity_id=identity.id, city_id=city.id))
        await session.flush()
    return identity, body


async def _convene(session, constants, catalog, city, ruler, body) -> Vote:
    await town.set_law(session, constants, catalog, ruler, city, LAW, VALUE, body=body)
    going = await vote.open_votes(session, city)
    assert len(going) == 1
    return going[0]


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


async def _bring(session: AsyncSession, poll: Vote) -> None:
    """Run the tally -- the same way the worker would."""
    from sqlalchemy import select

    from src.models.job import Job, JobKind, JobState

    job = (
        (
            await session.execute(
                select(Job).where(
                    Job.kind == JobKind.VOTE_CLOSE.value, Job.state == JobState.PENDING
                )
            )
        )
        .scalars()
        .first()
    )
    assert job is not None
    await vote.close(session, job)
    job.state = JobState.DONE
    await session.flush()


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
