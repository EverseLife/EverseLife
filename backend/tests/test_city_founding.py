# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Founding a city, and belonging to one (D-159, D-160).

A city is founded working, on nobody's land, with its buildings up and a
gate in its wall; citizenship is one per person, entered by open doors,
invitation or the printer's conditions, and left freely but not instantly.
What a standing city may do lives in `test_city.py`.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from city_kit import _capital, _resident
from src.constants import Catalog, Constants
from src.engine import city as town
from src.engine import net, world
from src.models.city import Power
from src.models.world import Layer, Node


async def test_founder_is_a_citizen_of_own_city(session: AsyncSession, catalog: Catalog) -> None:
    """A ruler must not be a stranger at home (D-195).

    Without citizenship they could not vote, borrowed at a newcomer's rate and
    paid a visitor's duties in the city they themselves founded.
    """
    city, core = await _capital(session, catalog)
    president, _ = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)

    assert await town.is_citizen(session, president.id, city)


async def test_founding_ends_the_previous_citizenship(
    session: AsyncSession, catalog: Catalog
) -> None:
    """There is one citizenship per person (D-160): "left and founded my own"."""
    from src.models.city import Citizen

    old_city, old_core = await _capital(session, catalog)
    person, _ = await _resident(session, old_core, "Переселенец")
    session.add(Citizen(identity_id=person.id, city_id=old_city.id))
    await session.flush()

    new_city, _ = await _capital(session, catalog)
    await town.install_founder(session, new_city, person)

    assert await town.is_citizen(session, person.id, new_city)
    assert not await town.is_citizen(session, person.id, old_city)


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


async def test_a_city_name_is_taken_only_once(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One name, one city -- and case does not make a second one free.

    The name becomes the name of the city's official channel, and the Net
    tells channel names apart ignoring case: `net.channel.create` refuses a
    second "Novograd" typed by hand. Founding used to hand one out, which is
    the same disagreement between the two doors that the name's length had.
    """
    place, _, body = await _wasteland(session)
    await _build_up(session, place)
    await town.establish(session, constants, catalog, body, "Новоград")

    #: A second place, so that what refuses is the name and not the node.
    other, _, stranger = await _wasteland(session, "Второй")
    await _build_up(session, other)

    with pytest.raises(town.CityError) as refusal:
        await town.establish(session, constants, catalog, stranger, "Новоград")
    assert refusal.value.key == "city-found-name-taken"
    assert refusal.value.params["name"] == "Новоград"

    #: Case is not a way round it: the Net would call these one name.
    with pytest.raises(town.CityError) as ignoring_case:
        await town.establish(session, constants, catalog, stranger, "новоГРАД")
    assert ignoring_case.value.key == "city-found-name-taken"

    #: A different name on that same place still founds -- the refusal is
    #: about the name, and nothing else got broken on the way to it.
    town_of_theirs = await town.establish(session, constants, catalog, stranger, "Второград")
    assert town_of_theirs.name == "Второград"


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


async def test_exit_free_but_delayed(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Otherwise one leaves the city right before a verdict (D-160)."""
    from datetime import UTC, datetime, timedelta

    from src.constants import registry as R

    city, core = await _capital(session, catalog)
    identity, body = await _resident(session, core, "Уходящий")
    await town.join(session, body, city)

    gone = datetime.now(UTC)
    entry = await town.leave(session, constants, identity, now=gone)
    assert entry.leaving_at == gone + timedelta(days=constants[R.CITY_EXIT_DELAY])
    assert await town.is_citizen(session, identity.id, city), "до срока человек ещё гражданин"

    #: The term is up -- the journal job closes the citizenship.
    from sqlalchemy import select as _select

    from src.models.job import Job, JobKind, JobState

    job = (
        (
            await session.execute(
                _select(Job).where(
                    Job.kind == JobKind.CITIZENSHIP_EXIT.value,
                    Job.state == JobState.PENDING,
                )
            )
        )
        .scalars()
        .first()
    )
    assert job is not None
    await town.exited(session, job)
    assert await town.citizenship(session, identity.id) is None


async def test_print_condition_grants_citizenship_for_term(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Consent was given by choosing the door: no admission needed, the term holds (D-184)."""
    from datetime import UTC, datetime

    city, core = await _capital(session, catalog)
    city.laws = {"spawn_citizenship": "обязательно", "spawn_term": "3"}
    await session.flush()

    was_printed = datetime.now(UTC)
    newcomer = await world.create_identity(session, f"Связанный-{uuid.uuid4().hex[:6]}")
    entry = await town.bind(session, constants, catalog, city, newcomer, now=was_printed)
    assert entry is not None and await town.is_citizen(session, newcomer.id, city)
    assert entry.bound_until == was_printed + timedelta(days=3)

    #: Cannot leave before the term: that is the enforcement of the condition.
    with pytest.raises(town.Bound):
        await town.leave(session, constants, newcomer, now=was_printed + timedelta(days=2))
    #: After the term -- an ordinary exit with the delay (D-160).
    gone = await town.leave(session, constants, newcomer, now=was_printed + timedelta(days=4))
    assert gone.leaving_at is not None


async def test_obligation_binds_person_not_city(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Exile breaks the term: otherwise the city cannot get rid of whom it bound itself."""
    from datetime import UTC, datetime

    city, core = await _capital(session, catalog)
    president, _ = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)
    city.laws = {"spawn_citizenship": "обязательно", "spawn_term": "10"}
    await session.flush()

    newcomer = await world.create_identity(session, f"Лишний-{uuid.uuid4().hex[:6]}")
    await town.bind(session, constants, catalog, city, newcomer, now=datetime.now(UTC))

    await town.exile(session, president, city, newcomer)
    assert await town.citizenship(session, newcomer.id) is None


async def test_city_without_conditions_binds_no_one(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The vault default is "no": printing neither gives nor requires citizenship."""
    city, core = await _capital(session, catalog)
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, world.BIOPRINTER, quality=50, origin="тест")

    newcomer, _ = await world.spawn(session, f"Вольный-{uuid.uuid4().hex[:6]}", core)
    assert await town.citizenship(session, newcomer.id) is None

    door = next(d for d in await world.doors(session, constants, catalog) if d["node"] == core.key)
    assert door["citizenship"] is False and door["term"] == 0


async def test_forerunner_print_carries_no_conditions(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The machine is nobody's: the city does not hang citizenship on it (D-028, D-184).

    Otherwise in a one-city world no unconditional door would remain at all,
    and "one can always refuse" would stop working.
    """
    from src.engine import death

    city, core = await _capital(session, catalog)
    core.properties = {**core.properties, death.PRECURSOR: True}
    city.laws = {"spawn_citizenship": "обязательно", "spawn_term": "5"}
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, world.BIOPRINTER, quality=50, origin="тест")
    await session.flush()

    freeman, _ = await world.spawn(session, f"Ничей-{uuid.uuid4().hex[:6]}", core)
    assert await town.citizenship(session, freeman.id) is None

    door = next(d for d in await world.doors(session, constants, catalog) if d["node"] == core.key)
    assert door["precursor"] is True
    assert door["citizenship"] is False and door["term"] == 0


async def test_print_conditions_visible_before_choice(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """What the engine will enforce, the newcomer must read on the card, not in a refusal."""
    city, core = await _capital(session, catalog)
    city.laws = {
        "spawn_citizenship": "обязательно",
        "spawn_term": "7",
        "tax_trade": "12",
    }
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, world.BIOPRINTER, quality=50, origin="тест")
    await session.flush()

    door = next(d for d in await world.doors(session, constants, catalog) if d["node"] == core.key)
    assert door["citizenship"] is True
    assert door["term"] == 7
    assert door["tax"] == 12


async def test_print_binds_newcomer_immediately(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The condition takes effect at the same moment as the body: otherwise it is an
    announcement."""
    city, core = await _capital(session, catalog)
    city.laws = {"spawn_citizenship": "обязательно", "spawn_term": "2"}
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, world.BIOPRINTER, quality=50, origin="тест")
    await session.flush()

    newcomer, _ = await world.spawn(session, f"Принятый-{uuid.uuid4().hex[:6]}", core)
    entry = await town.citizenship(session, newcomer.id)
    assert entry is not None and entry.city_id == city.id
    assert entry.bound_until is not None
    with pytest.raises(town.Bound):
        await town.leave(session, constants, newcomer)


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
    city.laws = {**city.laws, "body_print": "гражданам"}
    await session.flush()

    guest, _ = await _resident(session, core, "Гость")
    own, own_body = await _resident(session, core, "Горожанин")
    await town.join(session, own_body, city)

    assert not await death._city_pays(session, constants, core, guest.id)
    assert await death._city_pays(session, constants, core, own.id)
