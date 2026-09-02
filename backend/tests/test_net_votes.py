# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""A poll reaches the citizen in the Net (D-161, D-222).

A vote is participation and not governing: presence is needed to rule, and a
ballot is cast from the road. Until this the poll was drawn in one place only
-- the city's administration -- so the citizen who never opened that window was
never told at all, and the command that takes a ballot asked which city the
voter was **standing** in, which refused whoever was standing in none.

Checked here is the whole road of that: the tab is answered with one's own
city's polls and nobody else's, the tab's counter says how many still want an
answer, and the answer itself goes through from another planet.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.commands.city import _city_vote
from src.api.commands.look import _look
from src.api.commands.net import _net_threads
from src.constants import Catalog, Constants
from src.engine import city as town
from src.engine import vote, world
from src.models.city import Citizen
from src.models.identity import BodyState
from src.models.world import Layer, Planet

LAW, VALUE = "tax_trade", "7"


async def _city(session: AsyncSession, catalog: Catalog, name: str = "Вече"):
    """A city whose laws the citizens approve, and its ruler."""
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session, f"terra.city.{stamp}", name, area_m2=1, layer=Layer.PLANET, parent=planet
    )
    core = await world.create_node(
        session, f"terra.city.{stamp}.core", "Ядро", area_m2=100, parent=delegate
    )
    city = await town.found(session, catalog, delegate, name)
    core.owner_city_id = city.id
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, town.HALL, quality=65, origin="тест")
    city.charter = {**city.charter, vote.APPROVAL: vote.BY_CITIZENS}
    await session.flush()
    ruler, body = await _citizen(session, core, city, "Правитель")
    await town.install_founder(session, city, ruler)
    return city, core, ruler, body


async def _citizen(session: AsyncSession, node, city, name: str):
    identity = await world.create_identity(session, f"{name}-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    session.add(Citizen(identity_id=identity.id, city_id=city.id))
    await session.flush()
    return identity, body


async def _convene(session, constants, catalog, city, ruler, body):
    await town.set_law(session, constants, catalog, ruler, city, LAW, VALUE, body=body)
    (poll,) = await vote.open_votes(session, city)
    return poll


async def test_the_poll_arrives_in_the_net_wherever_one_stands(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The tab answers with one's own city's polls, named and with the city on them."""
    city, core, ruler, body = await _city(session, catalog)
    poll = await _convene(session, constants, catalog, city, ruler, body)
    citizen, elsewhere = await _citizen(session, core, city, "Гражданин")

    #: Away from the city, where the administration is not: that is the case
    #: the tab exists for.
    far = await world.create_node(
        session, f"pyroxis.far.{uuid.uuid4().hex[:6]}", "Далеко", area_m2=10, planet=Planet.PYROXIS
    )
    elsewhere.node_id = far.id
    await session.flush()

    answer = await _net_threads({"identity_id": citizen.id}, session, {})
    assert [line["id"] for line in answer["votes"]] == [str(poll.id)]
    line = answer["votes"][0]
    assert line["law"] == LAW, "the law travels as its id, the client names it"
    assert line["may_vote"] is True
    assert line["mine"] is None
    #: And no city on the row: there is one citizenship to a person, and its
    #: city is already in `look` -- a copy here would be a key the client can
    #: derive (D-225).
    assert "city" not in line


async def test_another_citys_poll_is_not_ones_own_affair(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A feed of what other cities decide would be noise, not news."""
    mine, core, ruler, body = await _city(session, catalog, "Своё")
    theirs, their_core, their_ruler, their_body = await _city(session, catalog, "Чужое")
    await _convene(session, constants, catalog, theirs, their_ruler, their_body)
    citizen, _ = await _citizen(session, core, mine, "Гражданин")

    answer = await _net_threads({"identity_id": citizen.id}, session, {})
    assert answer["votes"] == []


async def test_the_tab_counts_what_still_wants_an_answer(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The counter on the tab, and it goes out when the ballot is cast."""
    city, core, ruler, body = await _city(session, catalog)
    poll = await _convene(session, constants, catalog, city, ruler, body)
    citizen, _ = await _citizen(session, core, city, "Гражданин")

    seen = await _look({"identity_id": citizen.id}, session, {})
    assert seen["look"]["net_votes"] == 1

    await vote.cast(session, city, citizen, poll, True)
    seen = await _look({"identity_id": citizen.id}, session, {})
    assert seen["look"]["net_votes"] == 0, "an answered poll is not waiting for an answer"


async def test_a_ballot_is_cast_from_outside_the_city(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The command reads the city off the **ballot**, not off the ground.

    The regression: `city.vote` asked which city the body was standing in, so
    a citizen on another planet -- or simply in the wild, where no city is --
    could not answer their own city's poll at all. Casting is remote (D-161).
    """
    city, core, ruler, body = await _city(session, catalog)
    poll = await _convene(session, constants, catalog, city, ruler, body)
    citizen, away = await _citizen(session, core, city, "Гражданин")
    wild = await world.create_node(
        session, f"terra.wild.{uuid.uuid4().hex[:6]}", "Пустошь", area_m2=10
    )
    away.node_id = wild.id
    await session.flush()

    answer = await _city_vote({"identity_id": citizen.id}, session, {"vote": str(poll.id)})
    assert answer == {"yes": 0, "no": 1}
    assert (await vote.view(session, city, citizen.id))[0]["mine"] is False


async def test_a_body_is_not_asked_for_at_the_ballot_box(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A citizen waiting to be printed still answers their city (D-012, D-161).

    Deliberate rather than an oversight of the command that stopped asking for
    a live body: the electorate is counted at convening from the citizens, the
    dead among them, so a voice that is counted into the quorum and cannot be
    given would make the quorum unreachable by dying.
    """
    city, core, ruler, body = await _city(session, catalog)
    poll = await _convene(session, constants, catalog, city, ruler, body)
    citizen, dead = await _citizen(session, core, city, "Гражданин")
    dead.state = BodyState.DEAD
    await session.flush()

    answer = await _city_vote(
        {"identity_id": citizen.id}, session, {"vote": str(poll.id), "yes": True}
    )
    assert answer == {"yes": 1, "no": 0}


async def test_a_poll_one_has_no_voice_in_does_not_arrive(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The tab is an inbox: an item nobody can act on is noise in it."""
    city, core, ruler, body = await _city(session, catalog)
    await _convene(session, constants, catalog, city, ruler, body)
    stranger = await world.create_identity(session, f"Приезжий-{uuid.uuid4().hex[:6]}")
    await world.print_body(session, stranger, core)

    answer = await _net_threads({"identity_id": stranger.id}, session, {})
    assert answer["votes"] == []
    with pytest.raises(vote.NoVoice):
        await _city_vote(
            {"identity_id": stranger.id},
            session,
            {"vote": str((await vote.open_votes(session, city))[0].id)},
        )
