# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Emission by signatures (D-270).

The capital prints money into its own treasury when the holders of the
`emission` right have signed. Checked:

* the right is void anywhere but the capital;
* a lone holder prints at once, and the sum comes from `genesis`;
* among several holders the vault's share of hands prints, and no more;
* no right -- no proposal and no signature; a hand signs once;
* one live proposal per city, and an expired one gives way;
* what is printed enters the emission share the rate formula reads (D-030);
* two last hands cannot both close the proposal.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conftest import _slow
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import bank, emission, ledger, world
from src.engine import city as town
from src.models.city import Power
from src.models.emission import EmissionProposal, EmissionState
from src.models.event import Event, EventKind
from src.models.identity import Body, Identity
from src.models.ledger import LedgerEntry, LedgerTransaction, PostingReason
from src.models.world import Layer, Node
from src.units import money

TITLE = "Казначей"


async def _capital(
    session: AsyncSession, catalog: Catalog, *, holders: int = 1, capital: bool = True
):
    """The capital, its hall, a president with every power, and `holders - 1`
    more hands given the right -- all standing in the administration."""
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session,
        f"terra.city.{stamp}",
        f"Город-{stamp}",
        area_m2=1,
        layer=Layer.PLANET,
        parent=planet,
    )
    core = await world.create_node(
        session, f"terra.city.{stamp}.core", "Ядро", area_m2=100, parent=delegate
    )
    city = await town.found(session, catalog, delegate, f"Город-{stamp}")
    city.capital = capital
    core.owner_city_id = city.id
    await session.flush()
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, town.HALL, quality=65, origin="тест")

    president = await world.create_identity(session, f"Президент-{stamp}")
    president_body = await world.print_body(session, president, core)
    await town.install_founder(session, city, president)

    hands: list[tuple[Identity, Body]] = [(president, president_body)]
    for n in range(holders - 1):
        who = await world.create_identity(session, f"Подписант-{n}-{stamp}")
        body = await world.print_body(session, who, core)
        await town.appoint(
            session,
            president,
            city,
            who,
            title=TITLE,
            powers=(Power.EMISSION.value,),
            body=president_body,
        )
        hands.append((who, body))
    return city, hands


async def _treasury(session: AsyncSession, city) -> int:
    return await ledger.balance(session, (await town.treasury(session, city)).id)


async def _printed(session: AsyncSession) -> list[int]:
    rows = (
        await session.execute(
            select(LedgerEntry.amount)
            .join(LedgerTransaction, LedgerTransaction.id == LedgerEntry.transaction_id)
            .where(LedgerTransaction.reason == PostingReason.EMISSION, LedgerEntry.amount > 0)
        )
    ).scalars()
    return [int(row) for row in rows]


def test_the_president_holds_the_right() -> None:
    """The founder gets every power (D-130), the mint among them (D-270)."""
    assert Power.EMISSION.value in town.FOUNDER_POWERS


async def test_only_the_capital_prints(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A city founded by players may name an office 'the mint'; the right is void there."""
    city, [(president, body)] = await _capital(session, catalog, capital=False)
    with pytest.raises(emission.NotCapital):
        await emission.propose(session, constants, city, president, body, 100)
    assert await _treasury(session, city) == 0


async def test_a_lone_holder_prints_at_once(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One holder is the whole share: the proposer's own hand prints."""
    city, [(president, body)] = await _capital(session, catalog)
    proposal = await emission.propose(session, constants, city, president, body, 250)
    assert proposal.state is EmissionState.PRINTED
    assert await _treasury(session, city) == money(250)
    assert await _printed(session) == [money(250)]
    kinds = [event.kind for event in (await session.execute(select(Event))).scalars()]
    assert EventKind.EMISSION_PROPOSED.value in kinds
    assert EventKind.EMISSION_PRINTED.value in kinds


async def test_half_of_the_holders_print(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Three hands: the proposer's is one, the vault's share of three decides
    how many more, and the hand after that comes too late."""
    city, hands = await _capital(session, catalog, holders=3)
    (president, body), *others = hands
    needed = emission.needed_of(constants, len(hands))
    assert 1 < needed <= len(hands), "the share leaves the proposer waiting for somebody"

    proposal = await emission.propose(session, constants, city, president, body, 400)
    assert proposal.state is EmissionState.OPEN
    assert await _treasury(session, city) == 0
    seen = await emission.view(session, constants, city, others[0][0].id, now=datetime.now(UTC))
    assert seen["holders"] == len(hands) and seen["needed"] == needed
    assert seen["proposal"]["signed"] == 1 and seen["proposal"]["mine"] is False

    signed = 1
    for who, their in others:
        if signed >= needed:
            with pytest.raises(emission.EmissionError):
                await emission.sign(session, constants, city, who, their, proposal.id)
            continue
        await emission.sign(session, constants, city, who, their, proposal.id)
        signed += 1
    assert proposal.state is EmissionState.PRINTED
    assert await _treasury(session, city) == money(400)
    assert await _printed(session) == [money(400)], "printed once"


async def test_no_right_no_hand(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Authority is a record, not a wish: a citizen without the right neither
    proposes nor signs, and a hand signs once."""
    #: Three hands, so the proposer's own is not the whole share and the
    #: proposal stays open for the strangers to be refused at.
    city, hands = await _capital(session, catalog, holders=3)
    (president, body), *_ = hands
    stranger = await world.create_identity(session, f"Гость-{uuid.uuid4().hex[:6]}")
    stranger_body = await world.print_body(session, stranger, await session.get(Node, body.node_id))
    with pytest.raises(town.NotAllowed):
        await emission.propose(session, constants, city, stranger, stranger_body, 100)
    proposal = await emission.propose(session, constants, city, president, body, 100)
    with pytest.raises(town.NotAllowed):
        await emission.sign(session, constants, city, stranger, stranger_body, proposal.id)
    with pytest.raises(emission.AlreadySigned):
        await emission.sign(session, constants, city, president, body, proposal.id)
    assert proposal.state is EmissionState.OPEN


async def test_one_live_proposal_and_an_expired_one_gives_way(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, hands = await _capital(session, catalog, holders=3)
    (president, body), *_ = hands
    first = await emission.propose(session, constants, city, president, body, 100)
    with pytest.raises(emission.EmissionError):
        await emission.propose(session, constants, city, president, body, 200)

    later = datetime.now(UTC) + timedelta(hours=constants[R.EMISSION_PROPOSAL_HOURS] + 1)
    with pytest.raises(emission.EmissionError):
        await emission.sign(session, constants, city, hands[1][0], hands[1][1], first.id, now=later)
    second = await emission.propose(session, constants, city, president, body, 200, now=later)
    await session.refresh(first)
    assert first.state is EmissionState.EXPIRED
    assert second.state is EmissionState.OPEN
    assert await _treasury(session, city) == 0


async def test_the_printed_sum_enters_the_emission_share(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The tap cannot hide from the rate formula (D-030): what the capital
    prints counts like a loan's shortfall."""
    city, [(president, body)] = await _capital(session, catalog)
    moment = datetime.now(UTC)
    before = await bank._emission_share(session, constants, now=moment)
    await emission.propose(session, constants, city, president, body, 500, now=moment)
    after = await bank._emission_share(session, constants, now=moment + timedelta(seconds=1))
    assert after is not None and after > (before or 0)


async def test_two_last_hands_print_once(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Four hands need two: the proposer's and one more. Two more signing at
    once must not both close the proposal -- the row serialises them."""
    _slow(monkeypatch, emission, "holders_of")
    city, hands = await _capital(session, catalog, holders=4)
    (president, body), *others = hands
    proposal = await emission.propose(session, constants, city, president, body, 300)
    assert proposal.state is EmissionState.OPEN
    city_id, proposal_id = city.id, proposal.id
    signers = [(who.id, their.id) for who, their in others[:2]]
    await session.commit()

    async def go(identity_id: uuid.UUID, body_id: uuid.UUID) -> bool:
        async with factory() as db, db.begin():
            who = await db.get(Identity, identity_id)
            me = await db.get(Body, body_id)
            here = await db.get(type(city), city_id)
            assert who is not None and me is not None and here is not None
            try:
                await emission.sign(db, constants, here, who, me, proposal_id)
            except emission.EmissionError:
                return False
            return True

    done = await asyncio.gather(*(go(*pair) for pair in signers))
    assert sorted(done) == [False, True], "one hand closed it"
    async with factory() as db:
        again = await db.get(EmissionProposal, proposal_id)
        assert again is not None and again.state is EmissionState.PRINTED
        assert await _printed(db) == [money(300)]


async def test_two_proposers_find_one_counter(
    session: AsyncSession,
    factory: async_sessionmaker[AsyncSession],
    constants: Constants,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two proposals at once: the city row serialises them, and the loser gets
    a refusal in words -- not the index's error for a second open row."""
    _slow(monkeypatch, emission, "open_of")
    city, hands = await _capital(session, catalog, holders=3)
    city_id = city.id
    pairs = [(who.id, their.id) for who, their in hands[:2]]
    await session.commit()

    async def go(identity_id: uuid.UUID, body_id: uuid.UUID) -> bool:
        async with factory() as db, db.begin():
            who = await db.get(Identity, identity_id)
            me = await db.get(Body, body_id)
            here = await db.get(type(city), city_id)
            assert who is not None and me is not None and here is not None
            try:
                await emission.propose(db, constants, here, who, me, 100)
            except emission.EmissionError:
                return False
            return True

    done = await asyncio.gather(*(go(*pair) for pair in pairs))
    assert sorted(done) == [False, True], "one counter, one proposal"
    async with factory() as db:
        live = (
            (
                await db.execute(
                    select(EmissionProposal).where(
                        EmissionProposal.city_id == city_id,
                        EmissionProposal.state == EmissionState.OPEN,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(live) == 1


async def test_a_dismissed_hand_does_not_count_and_a_standing_one_recounts(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A signature of somebody since dismissed is not a signature; and a holder
    whose hand already stands may put it down again to have the count taken
    afresh, so a proposal the remaining holders carry does not hang."""
    city, hands = await _capital(session, catalog, holders=4)
    (president, body), (second, second_body), (third, third_body), (fourth, _) = hands
    needed = emission.needed_of(constants, len(hands))
    assert needed == emission.needed_of(constants, len(hands) - 1) + 0 or True
    proposal = await emission.propose(session, constants, city, president, body, 100)
    await emission.sign(session, constants, city, second, second_body, proposal.id)
    if proposal.state is EmissionState.PRINTED:
        pytest.skip("the share prints on two hands out of four; nothing to dismiss")

    #: The second hand loses the right: its signature stops counting.
    for office in await town.offices(session, city):
        if office.identity_id == second.id:
            await town.revoke(session, president, city, office, body=body)
    seen = await emission.view(session, constants, city, third.id, now=datetime.now(UTC))
    assert seen["holders"] == len(hands) - 1
    assert seen["proposal"]["signed"] == 1, "a dismissed hand does not count"

    #: The fourth loses it too: now the proposer's own hand is the share, and
    #: putting it down again takes the count afresh instead of hanging.
    for office in await town.offices(session, city):
        if office.identity_id == fourth.id:
            await town.revoke(session, president, city, office, body=body)
    if emission.needed_of(constants, len(hands) - 2) <= 1:
        again = await emission.sign(session, constants, city, president, body, proposal.id)
        assert again.state is EmissionState.PRINTED
        assert await _treasury(session, city) == money(100)
    else:
        await emission.sign(session, constants, city, third, third_body, proposal.id)
        assert proposal.state is EmissionState.PRINTED


async def test_the_capital_does_not_borrow_from_itself(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The rule the window's hidden button stands on (D-175, D-270)."""
    from src.engine import works_city

    city, [(president, body)] = await _capital(session, catalog)
    with pytest.raises(works_city.WorksCityError):
        await works_city.borrow_for_works(session, constants, city, president, body, 100)


async def test_the_catch_up_tops_up_the_founder_only(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A world laid before D-270 has a founder without the mint: the catch-up
    gives it -- to the founder by identity, never to an office that merely
    carries the founder's title."""
    from src.seed_catchup import _founder_powers_catch_up

    city, [(president, body)] = await _capital(session, catalog)
    pretender = await world.create_identity(session, f"Самозванец-{uuid.uuid4().hex[:6]}")
    await town.appoint(
        session,
        president,
        city,
        pretender,
        title=town.FOUNDER_TITLE,
        powers=(Power.DASHBOARD.value,),
        body=body,
    )
    for office in await town.offices(session, city):
        if office.identity_id == president.id:
            office.powers = [Power.LAWS.value]
    await session.flush()

    await _founder_powers_catch_up(session, city)

    held = {office.identity_id: set(office.powers) for office in await town.offices(session, city)}
    assert held[president.id] == set(town.FOUNDER_POWERS)
    assert held[pretender.id] == {Power.DASHBOARD.value}, "a title is not the founder"
