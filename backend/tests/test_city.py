# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The city in power: laws, treasury, word and land (D-032, D-160).

Who may edit a law and spend the treasury, how a city decision beats the
default and a tariff reaches the pool, what the city says to a newcomer and
hands out in plots, and how far a right stretches. Founding a city and
belonging to one live in `test_city_founding.py`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from city_kit import _capital, _resident
from src.constants import Catalog, Constants
from src.engine import city as town
from src.engine import energy, ledger, world
from src.models.city import Power
from src.models.event import Event, EventKind
from src.models.ledger import AccountKind
from src.models.world import PLOT
from src.units import money

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


async def test_a_law_change_records_the_rule_that_was_in_force(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """«Было» is the rule, not the column it was written in.

    A law the city had never touched sat in nobody's column while the world
    charged the vault's default, and the announcement of the change read
    «было —»: true about the row, false about the world. What is recorded is
    what was being charged before and what is charged now.
    """
    city, core = await _capital(session, catalog)
    president, body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)
    assert not (city.laws or {}).get("tax_trade"), "the city has not decided this law yet"

    await town.set_law(session, constants, catalog, president, city, "tax_trade", "1", body=body)

    written = (
        (
            await session.execute(
                select(Event)
                .where(Event.kind == EventKind.CITY_LAW_SET.value)
                .order_by(Event.at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert written is not None
    assert written.payload["law"] == "tax_trade"
    assert written.payload["was"] == catalog.laws.code_law_defaults()["tax_trade"]
    assert written.payload["now"] == "1"


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
        properties={PLOT: True},
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


async def test_the_city_hands_out_plots_not_itself(session: AsyncSession, catalog: Catalog) -> None:
    """The core, the market, the town hall are not land in the queue (D-199).

    Allotment asked two things -- the node is the city's and nobody holds it
    yet -- and the capital's core answers both. The window never offered it:
    it lists only marked plots. The wire had no such rule, so one command with
    the core's key made the city's centre somebody's yard, and the yard's
    holder shut the gate on everybody: the market, the administration and the
    printer people come back to life at went behind one person's door.
    """
    city, core = await _capital(session, catalog)
    president, president_body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)
    resident, _ = await _resident(session, core, "Житель")

    assert core.owner_city_id == city.id, "the core is the city's own node"
    with pytest.raises(town.CityError):
        await town.allot(session, president, city, core, resident, body=president_body)
    assert core.owner_identity_id is None


async def test_the_former_holder_is_told_the_city_took_its_location_back(
    session: AsyncSession, catalog: Catalog
) -> None:
    """A title, a house and a door gone with no word said would be the world
    changing behind somebody's back (D-282, D-226).

    The only trace used to be `deed.retired`, which names no actor -- so the
    push addressed nobody, and if the node carried no paper at all nothing was
    written down. `cede` records its own event with the person who chose it;
    this is the case where the person did not choose.
    """
    city, core = await _capital(session, catalog)
    holder, _ = await _resident(session, core, "Захвативший")
    core.owner_identity_id = holder.id
    await session.flush()

    assert await town.reclaim(session, core, city) is True

    told = (
        (await session.execute(select(Event).where(Event.kind == EventKind.LAND_RECLAIMED.value)))
        .scalars()
        .all()
    )
    assert len(told) == 1
    assert told[0].actor_identity_id == holder.id, "бывшему хозяину говорят поимённо"
    assert told[0].node_id == core.id
    #: And the second pass finds nothing: the catch-up runs at every deploy.
    assert await town.reclaim(session, core, city) is False


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


async def test_city_land_taken_by_law_code(session: AsyncSession, catalog: Catalog) -> None:
    """`build_permit` by default gives plots to citizens (D-089, D-160)."""
    city, _ = await _capital(session, catalog)
    assert town.may_take_city_land(catalog, city, True)
    assert not town.may_take_city_land(catalog, city, False)

    city.laws = {**city.laws, "build_permit": "все"}
    assert town.may_take_city_land(catalog, city, False), "город вправе открыться"


# --- the treasury and other people's debts (D-280) ---------------------------


async def test_treasury_bails_out_only_its_own(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Its own line or its own citizen, and nobody else's.

    The treasury used to settle any loan by number, and since D-280 every such
    payment buys the debtor a credit limit -- so a city could raise a
    stranger's limit with public money, and take its share of the margin back
    on top.
    """
    from src.api.commands.city import _city_bail
    from src.api.registry import Refused
    from src.engine import bank

    city, core = await _capital(session, catalog, funds=1000)
    president, _ = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)

    stranger, stranger_body = await _resident(session, core, "Чужой")
    debt = await bank.borrow(session, constants, catalog, stranger, 100)
    assert debt.city_id is None, "чужой не гражданин: заём прямой у столицы"
    await session.flush()

    state = {"identity_id": president.id}
    with pytest.raises(Refused):
        await _city_bail(state, session, {"city": core.key, "loan": str(debt.id)})
    assert debt.outstanding == money(100), "казна не заплатила по чужому займу"

    #: Its own citizen -- the city stands for them, and that is what a city is for.
    await town.join(session, stranger_body, city)
    paid = await _city_bail(state, session, {"city": core.key, "loan": str(debt.id)})
    assert paid["paid"] > 0
