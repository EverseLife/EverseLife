# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Founding a city, and belonging to one (D-159, D-160, D-281).

A city is founded working, on nobody's land, with its buildings up and a gate
in its wall -- and by somebody who belongs to no other city. Citizenship is one
per person, given outright by the door a newcomer chose or entered afterwards
by the charter, and left in the moment it is given up, unless a loan is open.
What a standing city may do lives in `test_city.py`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from city_kit import _capital, _resident
from src.constants import Catalog, Constants
from src.engine import city as town
from src.engine import net, world
from src.models.city import Power
from src.models.world import Layer, Node
from src.units import money


async def test_founder_is_a_citizen_of_own_city(session: AsyncSession, catalog: Catalog) -> None:
    """A ruler must not be a stranger at home (D-195).

    Without citizenship they could not vote, borrowed at a newcomer's rate and
    paid a visitor's duties in the city they themselves founded.
    """
    city, core = await _capital(session, catalog)
    president, _ = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)

    assert await town.is_citizen(session, president.id, city)


async def test_a_citizen_founds_no_city_of_their_own(
    session: AsyncSession, catalog: Catalog
) -> None:
    """Founding is entering a citizenship, and one does not enter without leaving (D-281).

    It used to be the one way to change city instantly: the previous
    citizenship ended by itself at the founding, delay and debt and all.
    """
    from src.models.city import Citizen

    old_city, old_core = await _capital(session, catalog)
    person, _ = await _resident(session, old_core, "Переселенец")
    session.add(Citizen(identity_id=person.id, city_id=old_city.id))
    await session.flush()

    new_city, _ = await _capital(session, catalog)
    with pytest.raises(town.AlreadyCitizen):
        await town.install_founder(session, new_city, person)
    assert await town.is_citizen(session, person.id, old_city), "прежнее гражданство цело"


# --- city founding by a player (D-023, D-098, D-159) -------------------------


async def _wasteland(session: AsyncSession, name: str = "Основатель"):
    """Nobody's node on the planet: found by exploration, owned by no one (D-198)."""
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    place = await world.create_node(
        session,
        f"terra.wild.{stamp}",
        "Место под город",
        area_m2=400,
        layer=Layer.PLANET,
        parent=planet,
        properties={"wild": True},
    )
    identity, body = await _resident(session, place, name)
    return place, identity, body


async def _build_up(session: AsyncSession, node, *, missing: str | None = None):
    """Place the four mandatory buildings in the node, except the named one."""
    from src.engine import death
    from src.engine import energy as power

    yard = await world.node_container(session, node)
    for_ = {
        "bioprinter": death.PRINTER,
        "administration": town.HALL,
        "market": "market_terminal",
        "power": power.WHEEL,
    }
    for role, machine in for_.items():
        if role == missing:
            continue
        await world.grant_item(session, yard, machine, quality=60, origin="тест")
    await session.flush()


async def test_city_founded_by_player(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Exploration found a place, four buildings made it a city (D-098)."""
    place, identity, body = await _wasteland(session)
    await _build_up(session, place)

    city = await town.establish(session, constants, catalog, body, "Новоград")

    assert city.name == "Новоград"
    assert city.founder_identity_id == identity.id
    assert await town.of_node(session, place) is not None, (
        "узел-представитель — территория собственного города"
    )
    #: The founder governs from the first second: a city without authority is not a city.
    assert await town.may(session, identity.id, city, Power.LAWS)
    assert await town.may(session, identity.id, city, Power.TREASURY)


async def test_a_founded_city_gets_a_gate(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A city has a door from the first second (D-206).

    Without it there would be nowhere to tie a road from beyond the walls, and
    exploration from inside would refuse instead of laying a trail. A city
    founded on a single node is its own gate -- that node is the whole city.
    """
    from src.engine import travel

    place, _, body = await _wasteland(session)
    await _build_up(session, place)

    city = await town.establish(session, constants, catalog, body, "Новоград")

    door = await town.gate(session, city)
    assert door is not None and door.id == place.id
    assert await travel.is_exit(session, place)

    #: And the door works: a road from the wild reaches the city through it.
    steppe = await world.create_node(
        session,
        f"terra.steppe.{uuid.uuid4().hex[:8]}",
        "Степь",
        area_m2=300,
        layer=Layer.PLANET,
        parent=await session.get(Node, place.parent_id),
    )
    await travel.connect(session, place, steppe, base_seconds=1800)


async def test_no_city_without_buildings(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The entry threshold is buildings, not a coin (D-023)."""
    place, _, body = await _wasteland(session)
    await _build_up(session, place, missing="bioprinter")

    #: By the key and what it quotes, not by the sentence: the wording is the
    #: locale's, and what is missing is a message of its own (D-251 wave IV).
    with pytest.raises(town.NotReady) as refusal:
        await town.establish(session, constants, catalog, body, "Недоград")
    assert refusal.value.key == "city-found-not-ready"
    quoted = refusal.value.inner["missing"]
    assert [one.key for one in quoted] == ["city-role-bioprinter"], (
        "отказ называет, чего не хватает"
    )
    assert await town.missing_for_foundation(session, place) == ("bioprinter",)


async def test_no_city_founded_on_foreign_land(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """A city is not founded over a living owner's head.

    Nobody's land needs no title (D-198) -- so the plot here is deliberately
    civic and held by someone: only then is there somebody else's land at all.
    """
    place, holder, body = await _wasteland(session)
    await _build_up(session, place)
    await own_plot(place, holder)
    place.owner_city_id = None
    await session.flush()
    _, alien = await _resident(session, place, "Чужак")

    with pytest.raises(town.NotYours):
        await town.establish(session, constants, catalog, alien, "Чужеград")


async def test_city_founded_on_nobodys_land(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Land outside a city is never privatized, and founding does not ask for title (D-198)."""
    place, _, body = await _wasteland(session)
    await _build_up(session, place)
    assert place.owner_identity_id is None

    city = await town.establish(session, constants, catalog, body, "Новоград")
    assert place.owner_city_id == city.id


async def test_land_under_city_goes_to_city(
    session: AsyncSession, constants: Constants, catalog: Catalog, own_plot
) -> None:
    """The location becomes city territory, and the deed is cancelled (D-159)."""
    from sqlalchemy import select

    from src.models.estate import Deed

    place, identity, body = await _wasteland(session)
    await _build_up(session, place)
    #: A held plot: the founder's own yard with a deed on it -- that is the
    #: paper the founding has to cancel.
    await own_plot(place, identity)
    place.owner_city_id = None
    await session.flush()
    deed = (
        await session.execute(select(Deed).where(Deed.node_id == place.id))
    ).scalar_one_or_none()
    assert deed is not None, "выдача участка сопровождается бумагой (D-116)"

    city = await town.establish(session, constants, catalog, body, "Новоград")

    assert place.owner_city_id == city.id
    assert place.owner_identity_id is None, "хозяин двора уступил месту власти"
    assert (
        await session.execute(select(Deed).where(Deed.node_id == place.id))
    ).scalar_one_or_none() is None, "городская земля бумагой не торгуется"


async def test_city_name_is_bounded(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A city name has a ceiling, and founding is the only place to set one.

    The name is not just the card's: it goes into the chronicle, into every
    refusal that quotes the city, and into the name of the city's official
    channel -- which is why the bound is no higher than the Net's own
    (`net.channel.create` would have refused what founding used to accept).
    """
    from src.runtime import CITY_NAME_LIMIT, NET_NAME_LIMIT

    assert CITY_NAME_LIMIT <= NET_NAME_LIMIT, (
        "имя города становится именем канала: потолок не выше сетевого"
    )

    place, _, body = await _wasteland(session)
    await _build_up(session, place)

    #: By the key, not the sentence: the wording is the locale's (D-251).
    with pytest.raises(town.CityError) as refusal:
        await town.establish(session, constants, catalog, body, "Г" * (CITY_NAME_LIMIT + 1))
    assert refusal.value.key == "city-found-name-too-long"
    assert refusal.value.params["limit"] == CITY_NAME_LIMIT

    #: The bound itself is allowed: it is a ceiling, not a step below one.
    city = await town.establish(session, constants, catalog, body, "Г" * CITY_NAME_LIMIT)
    assert len(city.name) == CITY_NAME_LIMIT

    #: And the whole point of the ceiling, end to end: the longest name a
    #: player can get through founding still fits the Net, because the city's
    #: official channel is named after the city. Checked here rather than in
    #: `test_net` because only this side runs the door that does the bounding
    #: -- the same assertion over a city made by `found` would hold whatever
    #: `establish` did with the name.
    channel = await net.city_channel(session, city)
    assert channel is not None
    assert channel.name == city.name
    assert len(channel.name) <= NET_NAME_LIMIT, (
        "имя канала города не длиннее того, что принимает net.channel.create"
    )


async def test_no_second_city_on_same_node(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    place, _, body = await _wasteland(session)
    await _build_up(session, place)
    await town.establish(session, constants, catalog, body, "Новоград")

    with pytest.raises(town.CityError):
        await town.establish(session, constants, catalog, body, "Второй")


# --- citizenship (D-160) -----------------------------------------------------


async def test_open_city_admits_immediately(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`citizenship_admission: open` -- signed up and a citizen."""
    from src.models.city import Citizen

    city, core = await _capital(session, catalog)
    _, body = await _resident(session, core, "Новичок")

    result = await town.join(session, body, city)
    assert isinstance(result, Citizen)
    assert await town.is_citizen(session, body.identity_id, city)


async def test_one_citizenship_per_person(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """No dual citizenship: leave the previous city first."""
    first, core1 = await _capital(session, catalog)
    second, core2 = await _capital(session, catalog)
    identity, body = await _resident(session, core1, "Перебежчик")
    await town.join(session, body, first)

    body.node_id = core2.id
    await session.flush()
    with pytest.raises(town.AlreadyCitizen):
        await town.join(session, body, second)


async def test_authority_decides_on_application(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`application` -- the application is filed and waits for the `citizens` right."""
    from src.models.city import Citizen

    city, core = await _capital(session, catalog)
    president, _ = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)
    city.charter = {**city.charter, town.ADMISSION: town.APPLICATION}
    await session.flush()

    applicant, body = await _resident(session, core, "Проситель")
    order = await town.join(session, body, city)
    assert not isinstance(order, Citizen), "сразу не принимают"
    assert not await town.is_citizen(session, applicant.id, city)

    #: Without the `citizens` right the application cannot be approved: the city's personnel is
    #: authority.
    stranger, _ = await _resident(session, core, "Посторонний")
    with pytest.raises(town.NotAllowed):
        await town.admit(session, stranger, city, applicant)

    await town.admit(session, president, city, applicant)
    assert await town.is_citizen(session, applicant.id, city)


async def test_invite_only_otherwise_no_entry(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """`invite` -- the authority calls, the person accepts. Without a call -- refusal."""
    city, core = await _capital(session, catalog)
    president, _ = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)
    city.charter = {**city.charter, town.ADMISSION: town.INVITE}
    await session.flush()

    guest, body = await _resident(session, core, "Гость")
    with pytest.raises(town.NotAllowed):
        await town.join(session, body, city)

    await town.invite(session, president, city, guest)
    await town.join(session, body, city)
    assert await town.is_citizen(session, guest.id, city)


async def test_exit_is_instant(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One asks and it is over: no declaration, no term served (D-281)."""
    city, core = await _capital(session, catalog)
    identity, body = await _resident(session, core, "Уходящий")
    await town.join(session, body, city)

    left = await town.leave(session, identity)
    assert left is not None and left.id == city.id
    assert await town.citizenship(session, identity.id) is None


async def test_an_open_loan_holds_the_citizenship(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The city answers for its borrowers, so the debtor settles up first (D-281).

    The loan is written straight into the table rather than borrowed through
    the bank: what is checked here is the exit's condition, and the bank's own
    road to the same rule is checked where the bank is (`test_bank_city`).
    """
    from src.models.bank import Loan, LoanState

    city, core = await _capital(session, catalog)
    identity, body = await _resident(session, core, "Должник")
    await town.join(session, body, city)

    debt = Loan(
        identity_id=identity.id,
        principal=money(100),
        outstanding=money(100),
        rate=10,
        city_id=city.id,
    )
    session.add(debt)
    await session.flush()

    with pytest.raises(town.InDebt):
        await town.leave(session, identity)
    assert await town.is_citizen(session, identity.id, city), "отказ не выпускает и не выгоняет"

    #: Settled -- and the same asking lets one out.
    debt.state = LoanState.REPAID
    debt.outstanding = 0
    await session.flush()
    assert await town.leave(session, identity) is not None
    assert await town.citizenship(session, identity.id) is None


async def test_the_door_makes_a_citizen(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Whoever chose the city is its citizen from the print, with nothing asked (D-281)."""
    city, core = await _capital(session, catalog)
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, world.BIOPRINTER, quality=50, origin="тест")
    #: Even a city that admits by invitation only: its own door is the invitation.
    city.charter = {**city.charter, town.ADMISSION: town.INVITE}
    await session.flush()

    newcomer, _ = await world.spawn(session, f"Новичок-{uuid.uuid4().hex[:6]}", core)
    entry = await town.citizenship(session, newcomer.id)
    assert entry is not None and entry.city_id == city.id

    #: And nothing holds it: no term was written, so the first minute is enough.
    assert await town.leave(session, newcomer) is not None


async def test_forerunner_printer_enrols_into_its_city(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The machine is nobody's, the person who steps out of it is not (D-281).

    The Forerunners' Printer stands on the capital's land in the world as
    seeded, and that land is what decides: "no conditions of the city" used to
    mean "no city at all", and a newcomer coming out of the eternal machine
    belonged nowhere until they asked somebody to take them in.
    """
    from src.engine import death

    city, core = await _capital(session, catalog)
    core.properties = {**core.properties, death.PRECURSOR: True}
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, world.BIOPRINTER, quality=50, origin="тест")
    await session.flush()

    freeman, _ = await world.spawn(session, f"Предтечин-{uuid.uuid4().hex[:6]}", core)
    entry = await town.citizenship(session, freeman.id)
    assert entry is not None and entry.city_id == city.id

    door = next(d for d in await world.doors(session, constants, catalog) if d["node"] == core.key)
    assert door["precursor"] is True and door["city"] == city.name


async def test_a_printer_outside_any_city_enrols_into_nothing(
    session: AsyncSession, catalog: Catalog
) -> None:
    """There is nowhere to write: a machine on nobody's land makes nobody's people."""
    from src.engine import death

    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    wild = await world.create_node(
        session,
        f"terra.wild.{stamp}",
        "Пустошь",
        area_m2=100,
        layer=Layer.PLANET,
        parent=planet,
        properties={death.PRECURSOR: True},
    )
    yard = await world.node_container(session, wild)
    await world.grant_item(session, yard, world.BIOPRINTER, quality=50, origin="тест")
    await session.flush()

    loner, _ = await world.spawn(session, f"Ничей-{uuid.uuid4().hex[:6]}", wild)
    assert await town.citizenship(session, loner.id) is None


async def test_the_card_shows_the_city_and_its_tax(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """What the engine will enforce, the newcomer reads on the card, not in a refusal.

    Citizenship is not a key of its own (D-225): a city door gives it and a
    door with no city cannot, so `city` on the card already says it.
    """
    city, core = await _capital(session, catalog)
    city.laws = {"tax_trade": "12"}
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, world.BIOPRINTER, quality=50, origin="тест")
    await session.flush()

    door = next(d for d in await world.doors(session, constants, catalog) if d["node"] == core.key)
    assert door["city"] == city.name
    assert door["tax"] == 12
    assert "citizenship" not in door and "term" not in door


async def test_exile_goes_by_court_right(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Exile is a sanction, not a personnel decision: right `justice`."""
    city, core = await _capital(session, catalog)
    president, _ = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)
    outcast, body = await _resident(session, core, "Изгой")
    await town.join(session, body, city)

    stranger, _ = await _resident(session, core, "Посторонний")
    with pytest.raises(town.NotAllowed):
        await town.exile(session, stranger, city, outcast)

    await town.exile(session, president, city, outcast)
    assert await town.citizenship(session, outcast.id) is None


async def test_city_prints_at_own_expense_only_for_own(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """ "citizens" means citizens: before D-160 the treasury paid for strangers."""
    from src.engine import death

    city, core = await _capital(session, catalog)
    city.laws = {**city.laws, "body_print": town.CITIZENS}
    await session.flush()

    guest, _ = await _resident(session, core, "Гость")
    own, own_body = await _resident(session, core, "Горожанин")
    await town.join(session, own_body, city)

    assert not await death._city_pays(session, constants, core, guest.id)
    assert await death._city_pays(session, constants, core, own.id)
