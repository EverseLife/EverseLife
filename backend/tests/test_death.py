# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Death and body printing (D-012, D-028, D-032, D-033, D-040).

The vault's acceptance is checked verbatim:

* "Death loses the body and things, but not knowledge and account" (07-implementation-map, E1);
* "The first body is instant; the capital always prints, but 12 hours" (E3);
* part of the worn stays at the place of death, and in damaged form;
* the city sells not life but speed: the paid door is faster than the free one.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import city as town
from src.engine import death, energy, ledger, ruins, travel, world
from src.models.identity import Body, BodyState, Knowledge
from src.models.inventory import Item
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer
from src.units import MINUTES_PER_HOUR, amount_float, money


async def _world(session: AsyncSession, catalog: Catalog, *, treasury: float = 0):
    """The capital with two doors: the eternal Forerunners' Printer and the city printer."""
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
    #: The core is also this city's door (D-206): the whole built-up area here
    #: is two nodes, and a road out of it has to be tied to something.
    core = await world.create_node(
        session,
        f"terra.city.{stamp}.core",
        "Ядро",
        area_m2=120,
        parent=delegate,
        properties={"ring": 0, death.PRECURSOR: True, travel.EXIT: True},
    )
    forge = await world.create_node(
        session,
        f"terra.city.{stamp}.forge",
        "forge",
        area_m2=200,
        parent=delegate,
        properties={"ring": 2},
    )
    city = await town.found(session, catalog, delegate, "Столица")
    for node in (core, forge):
        node.owner_city_id = city.id
    await session.flush()

    #: The **original** stands in the core (D-028): free, slow and one of a kind,
    #: and it is a relic rather than a mark on the ground (D-232). The forge gets
    #: an ordinary printer, the kind a city builds for itself.
    await ruins.grant_relic(session, core, death.PRINTER, origin="тест: наследие Предтеч")
    await world.grant_item(
        session,
        await world.node_container(session, forge),
        death.PRINTER,
        quality=60,
        origin="тест",
    )
    #: City decisions are made in the administration (D-155): without it the
    #: president cannot even allow printing at the treasury's expense.
    await world.grant_item(
        session,
        await world.node_container(session, core),
        town.HALL,
        quality=65,
        origin="тест",
    )
    forge_yard = await world.node_container(session, forge)
    await world.grant_item(session, forge_yard, death.IRON, amount=50, quality=55, origin="тест")

    if treasury:
        account = await town.treasury(session, city)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=account.id,
            amount=money(treasury),
        )
    return city, core, forge


async def _outpost(session: AsyncSession, catalog: Catalog, name: str = "Балка"):
    """A city founded on one node: the printer the founding was allowed on is its core."""
    stamp = uuid.uuid4().hex[:8]
    ground = await world.create_node(
        session, f"terra.wild.{stamp}", name, area_m2=300, layer=Layer.PLANET
    )
    yard = await world.node_container(session, ground)
    await world.grant_item(session, yard, death.PRINTER, quality=50, origin="тест")
    city = await town.found(session, catalog, ground, name)
    await session.flush()
    return city, ground


async def _resident(session: AsyncSession, node, name: str, *, funds: float = 0):
    identity, body = await world.spawn(session, f"{name}-{uuid.uuid4().hex[:6]}", node)
    if funds:
        account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=account.id,
            amount=money(funds),
        )
    return identity, body


async def _pool(session: AsyncSession, constants: Constants, node, qty: float):
    pool = await energy.pool_of(session, constants, node)
    pool.stored = Decimal(str(qty))
    await session.flush()
    return pool


# --- death -------------------------------------------------------------------


async def test_death_takes_things_but_not_knowledge_and_account(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """E1 acceptance verbatim: body and things -- yes, knowledge and account -- no."""
    _, core, _ = await _world(session, catalog)
    identity, body = await _resident(session, core, "Шахтёр", funds=40)
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "iron_pickaxe", quality=60, origin="тест")
    await world.grant_item(session, pocket, "coal", amount=100, quality=50, origin="тест")
    await world.learn(session, identity, "nails")

    await death.die(session, constants, body, cause="cave_in")

    assert body.state is BodyState.DEAD and body.died_at is not None
    left = (
        (await session.execute(select(Item).where(Item.container_id == pocket.id))).scalars().all()
    )
    assert left == [], "карман погибшего пуст: вещи гибнут вместе с телом"

    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    assert await ledger.balance(session, account.id) == money(40), "счёт телу не принадлежит"
    knowledge = (
        (await session.execute(select(Knowledge).where(Knowledge.identity_id == identity.id)))
        .scalars()
        .all()
    )
    assert knowledge, "знание живёт в личности и не теряется"


async def test_death_en_route_cuts_transit(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A dead body arrives nowhere, and the transit must remember that.

    A state separate from "arrived": otherwise examining the episode shows an
    arrival where nobody arrived, and the journal job tries to deliver a corpse.
    """
    from src.models.travel import Travel, TravelState
    from src.models.world import Node

    _, core, _ = await _world(session, catalog)
    _, body = await _resident(session, core, "Ходок")
    there = await world.create_node(
        session, f"terra.dead.{uuid.uuid4().hex[:8]}", "Там", area_m2=100
    )
    await travel.connect(session, await session.get(Node, body.node_id), there, base_seconds=600)
    transit = await travel.depart(session, constants, body, there)

    await death.die(session, constants, body, cause="cave_in")

    transit = await session.get(Travel, transit.id)
    assert transit.state is TravelState.CANCELLED, "переход оборван, а не дошёл"
    assert body.node_id != there.id, "мёртвое тело никуда не приходит"
    assert await travel.current(session, body) is None


async def test_part_of_worn_stays_in_place_and_damaged(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Robbing the living pays better than killing: the dead leave a third, and that one damaged."""
    _, core, _ = await _world(session, catalog)
    _, body = await _resident(session, core, "Шахтёр")
    pocket = await world.body_container(session, body)
    await world.grant_item(session, pocket, "coal", amount=100, quality=50, origin="тест")

    survived = await death.die(session, constants, body, cause="обвал")
    share = constants[R.DEATH_SALVAGE_RATIO] / 100

    yard = await world.node_container(session, core)
    in_place = (
        (
            await session.execute(
                select(Item).where(Item.container_id == yard.id, Item.type_key == "coal")
            )
        )
        .scalars()
        .all()
    )
    assert len(in_place) == 1
    assert amount_float(in_place[0].amount) == pytest.approx(100 * share)
    assert float(in_place[0].condition) == pytest.approx(100 * share)
    assert survived == pytest.approx(100 * share)


# --- printing ----------------------------------------------------------------


async def test_forerunners_print_free_but_twelve_hours(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The upper bound of the resurrection price: nobody pays for longer than this (D-028)."""
    _, core, _ = await _world(session, catalog)
    identity, body = await _resident(session, core, "Погибший")
    await death.die(session, constants, body, cause="обвал")

    job = await death.order(session, constants, catalog, identity, core)
    hours = (job.run_at - body.died_at).total_seconds() / 3600
    assert hours == pytest.approx(constants[R.DEATH_PRINT_TIME_CAPITAL], rel=0.01)

    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    assert await ledger.balance(session, account.id) == 0, "у Предтеч печать бесплатна"


async def test_city_printer_takes_energy_iron_and_money(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The city sells speed: minutes instead of hours, but for resources (D-033)."""
    city, core, forge = await _world(session, catalog)
    identity, body = await _resident(session, core, "Богатый", funds=100)
    await death.die(session, constants, body, cause="обвал")

    pool = await _pool(session, constants, forge, 100_000)
    energy_before = float(pool.stored)
    yard = await world.node_container(session, forge)
    iron_before = await death._iron_here(session, forge)

    job = await death.order(session, constants, catalog, identity, forge)
    minutes = (job.run_at - body.died_at).total_seconds() / 60
    assert minutes == pytest.approx(constants[R.DEATH_PRINT_TIME_CITY], rel=0.01)
    assert minutes < constants[R.DEATH_PRINT_TIME_CAPITAL] * MINUTES_PER_HOUR

    assert float(pool.stored) == pytest.approx(energy_before - constants[R.ENERGY_BODY_PRINT])
    assert await death._iron_here(session, forge) == pytest.approx(
        iron_before - constants[R.DEATH_IRON_COST]
    )
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    paid = money(100) - await ledger.balance(session, account.id)
    assert paid > 0
    assert await town.treasury_balance(session, city) == paid
    assert yard is not None


async def test_city_printer_refuses_without_money(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """But the capital's door stays open meanwhile -- nobody drops out of the game."""
    _, core, forge = await _world(session, catalog)
    identity, body = await _resident(session, core, "Бедняк")
    await death.die(session, constants, body, cause="обвал")
    await _pool(session, constants, forge, 100_000)

    with pytest.raises(death.CannotPay):
        await death.order(session, constants, catalog, identity, forge)

    #: And the free door always works.
    job = await death.order(session, constants, catalog, identity, core)
    assert job is not None


async def test_city_can_print_at_own_expense(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The code-law `body_print` is the very argument to join a city (D-032)."""
    city, core, forge = await _world(session, catalog, treasury=500)
    president, president_body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)
    await town.set_law(
        session,
        constants,
        catalog,
        president,
        city,
        "body_print",
        town.EVERYONE,
        body=president_body,
    )

    identity, body = await _resident(session, core, "Бедняк")
    await death.die(session, constants, body, cause="обвал")
    await _pool(session, constants, forge, 100_000)

    job = await death.order(session, constants, catalog, identity, forge)
    assert job is not None
    account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
    assert await ledger.balance(session, account.id) == 0, "платит казна, не игрок"


async def test_print_brings_identity_back(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The body is new, the identity the same: name and obligations survive death."""
    _, core, _ = await _world(session, catalog)
    identity, body = await _resident(session, core, "Возвращённый")
    await death.die(session, constants, body, cause="обвал")

    job = await death.order(session, constants, catalog, identity, core)
    await death.printed(session, job)

    new_ = await death.alive_body(session, identity.id)
    assert new_ is not None and new_.id != body.id
    assert new_.node_id == core.id
    assert float(new_.stamina) == constants[R.BODY_STAMINA_MAX]

    #: A job retry after a failure does not become a second body (D-011).
    await death.printed(session, job)
    bodies = (
        (
            await session.execute(
                select(Body).where(Body.identity_id == identity.id, Body.state == BodyState.ALIVE)
            )
        )
        .scalars()
        .all()
    )
    assert len(bodies) == 1


async def test_no_print_for_living(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, core, _ = await _world(session, catalog)
    identity, _ = await _resident(session, core, "Живой")
    with pytest.raises(death.Alive):
        await death.order(session, constants, catalog, identity, core)


async def test_second_print_in_row_not_queued(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, core, _ = await _world(session, catalog)
    identity, body = await _resident(session, core, "Погибший")
    await death.die(session, constants, body, cause="обвал")

    await death.order(session, constants, catalog, identity, core)
    with pytest.raises(death.AlreadyPrinting):
        await death.order(session, constants, catalog, identity, core)


async def test_printers_visible_from_cloud(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The identity is in the Net: the door list is available to the dead too (D-033)."""
    _, core, forge = await _world(session, catalog)
    _, body = await _resident(session, core, "Погибший")
    await death.die(session, constants, body, cause="обвал")

    doors = await death.printers(session, constants)
    keys = {door["node"]: door for door in doors}
    assert core.key in keys and forge.key in keys
    assert keys[core.key]["precursor"] is True
    assert keys[core.key]["cost"] == 0
    assert keys[forge.key]["iron"] == constants[R.DEATH_IRON_COST]
    #: The fast door first: the player must compare doors by term.
    assert doors[0]["node"] == forge.key


async def test_printer_row_names_what_is_on_hand_beside_what_is_asked(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The dead choose from the cloud and stand nowhere, so the pool behind a
    printer is the one figure they cannot look up. Without it the row named the
    demand alone, and a city holding a fifth of the energy needed read exactly
    like one that could print."""
    _, core, forge = await _world(session, catalog)
    _, body = await _resident(session, core, "Погибший")
    await death.die(session, constants, body, cause="обвал")

    pool = await energy.pool_of(session, constants, forge)
    pool.stored = Decimal("277")
    await session.flush()

    doors = {door["node"]: door for door in await death.printers(session, constants)}
    assert doors[forge.key]["energy_here"] == 277
    assert doors[forge.key]["energy"] == constants[R.ENERGY_BODY_PRINT]
    #: The eternal printer asks nothing of a pool, and there is none behind it.
    assert doors[core.key]["energy"] == 0


# --- newcomer entry (D-013, D-182) -------------------------------------------


async def test_newcomer_doors_show_city_not_price(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The first body is free everywhere (D-040), so one chooses not the price but the people."""
    city, core, _ = await _world(session, catalog)
    city.laws = {"newcomer_grant": "50"}
    await _resident(session, core, "Старожил")
    await session.flush()

    keys = {door["node"]: door for door in await world.doors(session, constants, catalog)}
    assert keys[core.key]["city"] == "Столица"
    assert keys[core.key]["grant"] == money(50)
    assert keys[core.key]["population"] == 1
    #: Neither price nor term: they are not set for a newcomer, and lying about them is not allowed.
    assert "cost" not in keys[core.key] and "minutes" not in keys[core.key]


async def test_city_door_is_the_printer_it_grew_from(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One city -- one door: the machine it grew from (D-208).

    The capital has two working printers: the eternal one in the core and the
    city one at the forge. Only the core takes newcomers -- the forge prints the
    dead (D-033) and does not become a second entrance into the world.
    """
    await _world(session, catalog)
    _, ground = await _outpost(session, catalog)
    await _resident(session, ground, "Первопоселенец")
    await session.flush()

    doors = await world.doors(session, constants, catalog)
    core_ = next(door for door in doors if door["precursor"])
    #: A city on one node is its own core: there stands the printer the founding
    #: was allowed on, and it is the door.
    assert {door["node"] for door in doors} == {ground.key, core_["node"]}
    #: The Forerunners' Printer last: a fallback door without residents and without a treasury.
    assert doors[-1]["node"] == core_["node"]


async def test_second_printer_of_city_is_no_door(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A printer put in a workshop prints the dead but leads nobody into the world."""
    _, _, forge = await _world(session, catalog)

    keys = {door["node"] for door in await world.doors(session, constants, catalog)}
    assert forge.key not in keys
    #: The list and the check by key are one rule: what is not offered does not open.
    assert await world.door(session, forge.key) is None
    #: For the dead the forge is still a printer, and a fast one at that (D-028).
    assert forge.key in {printer["node"] for printer in await death.printers(session, constants)}


async def test_printer_outside_a_city_is_no_door(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A machine on nobody's land: nothing was founded on it, and it is no entrance."""
    await _world(session, catalog)
    wild = await world.create_node(
        session,
        f"terra.wild.{uuid.uuid4().hex[:6]}",
        "Заимка",
        area_m2=200,
        layer=Layer.PLANET,
    )
    yard = await world.node_container(session, wild)
    await world.grant_item(session, yard, death.PRINTER, quality=50, origin="тест")
    await session.flush()

    keys = {door["node"] for door in await world.doors(session, constants, catalog)}
    assert wild.key not in keys
    assert await world.door(session, wild.key) is None


async def test_penal_colony_is_not_door_for_newcomer(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The prison printer prints only the held (D-174) and does not lead into the world."""
    from src.engine import justice

    _, core, _ = await _world(session, catalog)
    prison = await world.create_node(
        session, f"terra.jail.{uuid.uuid4().hex[:6]}", "prison", area_m2=100
    )
    yard = await world.node_container(session, prison)
    await world.grant_item(session, yard, death.PRINTER, quality=40, origin="тест")
    await world.grant_item(session, yard, justice.PRISON_CLASS, quality=40, origin="тест")
    await session.flush()

    keys = {door["node"] for door in await world.doors(session, constants, catalog)}
    assert prison.key not in keys
    assert await world.door(session, prison.key) is None
    #: An ordinary door by key opens -- otherwise there would be nothing to choose.
    assert (await world.door(session, core.key)) is not None


async def test_only_node_with_printer_called_door(session: AsyncSession, catalog: Catalog) -> None:
    """A foreign key and a node without a printer refuse alike: nowhere to print."""
    _, core, _ = await _world(session, catalog)
    field = await world.create_node(
        session, f"terra.field.{uuid.uuid4().hex[:6]}", "Пойма", area_m2=400
    )
    await session.flush()

    assert await world.door(session, field.key) is None
    assert await world.door(session, "нет-такого-узла") is None
    assert (await world.door(session, core.key)) is not None
