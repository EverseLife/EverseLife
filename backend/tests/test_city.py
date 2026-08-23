# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The city as an institution: offices, laws, treasury, settlement grant (D-153, D-154).

Checked is what the administration exists for at all:

* power, not office: the engine looks at `powers`, not the post's title;
* only what you have yourself can be given -- otherwise `offices` gives everything;
* a city law beats the vault default, and the tariff reaches the pool;
* the settlement grant is **a transfer from the treasury**, once per identity,
  and an empty treasury pays nothing.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.engine import city as town
from src.engine import energy, ledger, world
from src.models.city import Power
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer, Node
from src.units import money


async def _capital(session: AsyncSession, catalog: Catalog, *, funds: float = 0):
    """A city with a delegate node, built-up area and a founder."""
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session,
        f"terra.city.{stamp}",
        "Столица",
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
        properties={"кольцо": 0},
    )
    city = await town.found(session, catalog, delegate, "Столица")
    core.owner_city_id = city.id
    await session.flush()
    #: Governing is in-person (D-155): decisions are made where the
    #: "Administration" stands. In the starting world that is a separate node, in the test -- the
    #: core.
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, town.HALL, quality=65, origin="тест")

    if funds:
        treasury = await town.treasury(session, city)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=treasury.id,
            amount=money(funds),
        )
    return city, core


async def _resident(session: AsyncSession, node, name: str):
    identity = await world.create_identity(session, f"{name}-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, node)
    return identity, body


# --- offices and powers ------------------------------------------------------


async def test_city_arises_working(session: AsyncSession, catalog: Catalog) -> None:
    """The charter is filled with vault defaults: no forty-question form (D-130)."""
    city, _ = await _capital(session, catalog)
    assert city.charter, "устав пуст: город возник неработающим"
    assert city.charter["ruler_selection"] == "founder"
    #: No own decisions yet -- so the vault default applies.
    assert town.law(catalog, city, "tax_trade") == (catalog.laws.code_law_defaults()["tax_trade"])


async def test_founder_gets_full_authority(session: AsyncSession, catalog: Catalog) -> None:
    city, core = await _capital(session, catalog)
    president, _ = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)

    assert await town.powers_of(session, president.id, city) == set(town.FOUNDER_POWERS)
    #: Authority is taken the first time, afterwards only by appointment.
    with pytest.raises(town.CityError):
        await town.install_founder(session, city, president)


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


async def test_law_not_edited_without_power(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Authority is an office, not an intention."""
    city, core = await _capital(session, catalog)
    passerby, _ = await _resident(session, core, "Прохожий")
    with pytest.raises(town.NotAllowed):
        await town.set_law(session, constants, catalog, passerby, city, "tax_trade", "10")


async def test_can_give_only_own(session: AsyncSession, catalog: Catalog) -> None:
    """Otherwise anyone given `offices` appoints themselves everything else."""
    city, core = await _capital(session, catalog)
    president, president_body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)

    hr_officer, hr_body = await _resident(session, core, "Кадровик")
    await town.appoint(
        session,
        president,
        city,
        hr_officer,
        title="Кадровик",
        powers=(Power.OFFICES.value,),
        body=president_body,
    )
    third, _ = await _resident(session, core, "Третий")
    with pytest.raises(town.NotAllowed):
        await town.appoint(
            session,
            hr_officer,
            city,
            third,
            title="Казначей",
            powers=(Power.TREASURY.value,),
            body=hr_body,
        )
    #: And what one has is passed on.
    await town.appoint(
        session,
        hr_officer,
        city,
        third,
        title="Помощник",
        powers=(Power.OFFICES.value,),
        body=hr_body,
    )
    assert Power.OFFICES.value in await town.powers_of(session, third.id, city)


# --- laws --------------------------------------------------------------------


async def test_city_decision_beats_default(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    city, core = await _capital(session, catalog)
    president, body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)

    await town.set_law(session, constants, catalog, president, city, "tax_trade", "11", body=body)
    assert town.law(catalog, city, "tax_trade") == "11"
    assert town.law_number(constants, catalog, city, "tax_trade") == 11


async def test_tariff_reaches_pool(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The authority's decision must reach the meter, not stay a record (D-085)."""
    city, core = await _capital(session, catalog)
    president, body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)

    pool = await energy.pool_of(session, constants, core)
    assert pool is not None
    await town.set_law(
        session, constants, catalog, president, city, "energy_tariff", "9", body=body
    )
    await session.refresh(pool)
    assert float(pool.tariff) == 9


async def test_default_by_reference_expands_to_constant(
    constants: Constants, catalog: Catalog
) -> None:
    """`energy_tariff` is given in the vault as a reference to a constant -- and it is read."""
    from src.constants import registry as R

    value = town.law_number(constants, catalog, None, "energy_tariff")
    assert value == constants[R.ENERGY_TARIFF_DEFAULT]


# --- treasury and settlement grant -------------------------------------------


async def test_only_steward_spends_treasury(session: AsyncSession, catalog: Catalog) -> None:
    city, core = await _capital(session, catalog, funds=100)
    president, body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)
    resident, resident_body = await _resident(session, core, "Житель")

    with pytest.raises(town.NotAllowed):
        await town.spend(session, resident, city, resident, money(10), body=resident_body)

    await town.spend(session, president, city, resident, money(10), memo="жалованье", body=body)
    account = await ledger.account_for(session, AccountKind.IDENTITY, resident.id)
    assert await ledger.balance(session, account.id) == money(10)
    assert await town.treasury_balance(session, city) == money(90)


async def test_empty_treasury_does_not_pay(session: AsyncSession, catalog: Catalog) -> None:
    """An empty treasury is a political event, not a reason to print money."""
    city, core = await _capital(session, catalog)
    president, body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)
    resident, _ = await _resident(session, core, "Житель")

    with pytest.raises(town.NotEnoughTreasury):
        await town.spend(session, president, city, resident, money(10), body=body)


async def test_newcomer_printed_with_zero(session: AsyncSession, catalog: Catalog) -> None:
    """The world hands out no money: any such issue dilutes everyone's money (D-153)."""
    city, core = await _capital(session, catalog)
    identity, _ = await world.spawn(session, f"Новичок-{uuid.uuid4().hex[:6]}", core)
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    assert await ledger.balance(session, account.id) == 0


async def test_settlement_grant_paid_by_city_once(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """This is a transfer, not emission: not a coin appears in the world."""
    city, core = await _capital(session, catalog, funds=500)
    president, body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)
    await town.set_law(
        session, constants, catalog, president, city, "newcomer_grant", "50", body=body
    )
    before = await town.treasury_balance(session, city)

    identity, _ = await world.spawn(session, f"Новичок-{uuid.uuid4().hex[:6]}", core)
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    assert await ledger.balance(session, account.id) == money(50)
    assert await town.treasury_balance(session, city) == before - money(50)

    #: A second time to the same person in the same city -- zero.
    assert await town.welcome(session, constants, catalog, city, identity) == 0


async def test_settlement_grant_zero_by_default(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A city that decided nothing pays nothing: the vault default is zero."""
    city, core = await _capital(session, catalog, funds=500)
    identity, _ = await world.spawn(session, f"Новичок-{uuid.uuid4().hex[:6]}", core)
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    assert await ledger.balance(session, account.id) == 0


# --- the city's word to newcomers (D-183) ------------------------------------


async def test_city_word_written_by_authority_seen_by_newcomer(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The announcement is recruitment: whoever admits citizens edits it."""
    city, core = await _capital(session, catalog)
    president, body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)
    #: Until written -- the city is silent, and the engine does not make it up.
    assert city.about == ""
    yard = await world.node_container(session, core)
    await world.grant_item(session, yard, world.BIOPRINTER, quality=50, origin="тест")

    await town.describe(
        session,
        president,
        city,
        "  Шахта, кузня и работа с первого дня.  ",
        body=body,
    )
    assert city.about == "Шахта, кузня и работа с первого дня."

    doors = await world.doors(session, constants, catalog)
    said = {door["node"]: door["about"] for door in doors}
    assert said[core.key] == "Шахта, кузня и работа с первого дня."


async def test_city_word_not_given_to_stranger(session: AsyncSession, catalog: Catalog) -> None:
    """The `citizens` right, not "I live here": authority is an office (D-155)."""
    city, core = await _capital(session, catalog)
    president, president_body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)
    treasurer, treasurer_body = await _resident(session, core, "Казначей")
    await town.appoint(
        session,
        president,
        city,
        treasurer,
        title="Казначей",
        powers=(Power.TREASURY.value,),
        body=president_body,
    )

    with pytest.raises(town.NotAllowed):
        await town.describe(session, treasurer, city, "казна щедра", body=treasurer_body)


async def test_city_word_length_limited(session: AsyncSession, catalog: Catalog) -> None:
    """The card is compared by eye: a page of text does not go on it."""
    from src.runtime import CITY_ABOUT_LIMIT

    city, core = await _capital(session, catalog)
    president, body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)

    with pytest.raises(town.CityError):
        await town.describe(session, president, city, "а" * (CITY_ABOUT_LIMIT + 1), body=body)
    assert city.about == ""


# --- city land ---------------------------------------------------------------


async def test_city_hands_out_plots(session: AsyncSession, catalog: Catalog) -> None:
    """Civic land is not taken -- the city gives it (D-089)."""
    city, core = await _capital(session, catalog)
    president, president_body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)
    resident, body = await _resident(session, core, "Житель")

    plot = await world.create_node(
        session,
        f"terra.lot.{uuid.uuid4().hex[:8]}",
        "Участок",
        area_m2=100,
        parent=await session.get(type(core), core.parent_id),
        properties={"участок": True},
    )
    plot.owner_city_id = city.id
    await session.flush()

    body.node_id = plot.id
    await town.allot(session, president, city, plot, resident, body=president_body)
    assert plot.owner_identity_id == resident.id

    #: And an allotted plot is not handed out a second time: it is title, not a
    #: queue. Taking land by hand is gone entirely (D-198).
    other, _ = await _resident(session, core, "Второй")
    with pytest.raises(world.LandError):
        await world.grant_node(session, plot, other)


# --- narrow rights and presence (D-155) --------------------------------------


async def test_right_to_one_law_does_not_open_others(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The "minister of economy" edits duties and does not touch the tax -- that is the whole
    point."""
    city, core = await _capital(session, catalog)
    president, president_body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)

    minister, minister_body = await _resident(session, core, "Министр")
    await town.appoint(
        session,
        president,
        city,
        minister,
        title="Министр экономики",
        powers=("law:import_duty", "law:export_duty", Power.DASHBOARD.value),
        body=president_body,
    )

    await town.set_law(
        session,
        constants,
        catalog,
        minister,
        city,
        "import_duty",
        "7",
        body=minister_body,
    )
    assert town.law(catalog, city, "import_duty") == "7"

    #: And the tax is not theirs: the right is narrow, and the engine checks that.
    with pytest.raises(town.NotAllowed):
        await town.set_law(
            session,
            constants,
            catalog,
            minister,
            city,
            "tax_trade",
            "1",
            body=minister_body,
        )


async def test_broad_right_covers_narrow(session: AsyncSession, catalog: Catalog) -> None:
    """A `laws` holder may grant `law:toll`; a `law:toll` holder may not."""
    city, core = await _capital(session, catalog)
    president, body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)

    assert await town.may(session, president.id, city, "law:toll")

    narrow, narrow_body = await _resident(session, core, "Узкий")
    await town.appoint(
        session,
        president,
        city,
        narrow,
        title="Смотритель дорог",
        powers=("law:toll", Power.OFFICES.value),
        body=body,
    )
    other, _ = await _resident(session, core, "Другой")
    with pytest.raises(town.NotAllowed):
        await town.appoint(
            session,
            narrow,
            city,
            other,
            title="Казначей",
            powers=(Power.LAWS.value,),
            body=narrow_body,
        )


async def test_authority_exercised_in_administration(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Authority exercisable from across the ocean needs neither a capital nor roads."""
    city, core = await _capital(session, catalog)
    president, body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)

    #: We leave the town hall for an adjacent node of the same city.
    warehouse = await world.create_node(
        session,
        f"terra.store.{uuid.uuid4().hex[:8]}",
        "Склад",
        area_m2=100,
        parent=await session.get(type(core), core.parent_id),
    )
    warehouse.owner_city_id = city.id
    body.node_id = warehouse.id
    await session.flush()

    with pytest.raises(town.NotAllowed):
        await town.set_law(
            session, constants, catalog, president, city, "tax_trade", "9", body=body
        )

    #: Back -- and the decision passes.
    body.node_id = core.id
    await session.flush()
    await town.set_law(session, constants, catalog, president, city, "tax_trade", "9", body=body)
    assert town.law(catalog, city, "tax_trade") == "9"


async def test_disconnected_administration_does_not_govern(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Did not pay -- the city is blind and mute: that is the price of maintenance (D-140,
    D-149)."""
    from src.engine import utility

    city, core = await _capital(session, catalog)
    president, body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)

    meter = await utility.meter_of(session, core)
    assert meter is not None
    meter.cut_off = True
    await session.flush()

    with pytest.raises(town.NotAllowed):
        await town.set_law(
            session, constants, catalog, president, city, "tax_trade", "9", body=body
        )


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
        properties={"дикий": True},
    )
    identity, body = await _resident(session, place, name)
    return place, identity, body


async def _build_up(session: AsyncSession, node, *, missing: str | None = None):
    """Place the four mandatory buildings in the node, except the named one."""
    from src.engine import death
    from src.engine import energy as power

    yard = await world.node_container(session, node)
    for_ = {
        "биопринтер": death.PRINTER,
        "администрация": town.HALL,
        "рынок": "Терминал маркетплейса",
        "источник энергии": power.WHEEL,
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
    await _build_up(session, place, missing="биопринтер")

    with pytest.raises(town.NotReady) as refusal:
        await town.establish(session, constants, catalog, body, "Недоград")
    assert "биопринтер" in str(refusal.value), "отказ называет, чего не хватает"
    assert await town.missing_for_foundation(session, place) == ("биопринтер",)


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


async def test_city_land_taken_by_law_code(session: AsyncSession, catalog: Catalog) -> None:
    """`build_permit` by default gives plots to citizens (D-089, D-160)."""
    city, _ = await _capital(session, catalog)
    assert town.may_take_city_land(catalog, city, True)
    assert not town.may_take_city_land(catalog, city, False)

    city.laws = {**city.laws, "build_permit": "все"}
    assert town.may_take_city_land(catalog, city, False), "город вправе открыться"
