# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The city's bioprinter: one of it, and the city puts it up (D-312).

The machine a city counts its land from (D-307) and the door its newcomers
come through (D-208) are one and the same, and neither may have two
candidates. So a second printer does not go up on a city's land at all, and
when the city has none only the authority restores one. Beyond the walls
nothing is refused, and the prison keeps its own (D-174).

Three doors lead a printer to stand, and all three are checked here: putting
up a machine that lies, the start of the batch that makes one, and the finish
of that batch twenty hours later.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from city_kit import _built, _capital, _printer, _resident
from src.constants import Catalog, Constants
from src.engine import city as town
from src.engine import world


async def test_a_city_has_one_printer(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A second bioprinter does not go up beside the first (D-312).

    The machine a city counts from is its centre (D-307) and its door (D-208),
    and neither is an answer that may have two candidates: while one stands,
    `city.core` can never change its mind about which node that is. Refused to
    the authority too -- this is not a right anybody holds but a rule about the
    city.
    """
    from src.engine import station

    city, core = await _capital(session, catalog)
    president, body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)
    await _built(session, core)
    #: The city already has one: this is what makes the rule bite.
    await _printer(session, core, standing=True)
    assert await town.core(session, city) is not None

    second = await _printer(session, core, standing=False)
    with pytest.raises(station.OnePrinter):
        await station.place(session, catalog, body, second)


async def test_the_city_puts_the_printer_up_and_not_the_plot_holder(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Lost its machine, a city has no centre and no door until the authority
    restores one -- and the holder of a plot inside the city may not (D-312).

    This is the hole the rule closes: `station.place` asks for the right to the
    **node**, and on a bought plot that right is the holder's. Without this the
    city's door and every land rate in town moved by one person's decision, in
    somebody's private yard, which is what D-208 refuses.
    """
    from src.engine import station

    city, core = await _capital(session, catalog)
    assert await town.core(session, city) is None, "город без машины"

    holder, holder_body = await _resident(session, core, "Держатель")
    plot = await world.create_node(session, f"{core.key}.plot", "Участок", area_m2=100)
    plot.parent_id = core.parent_id
    plot.owner_city_id = city.id
    plot.owner_identity_id = holder.id
    await session.flush()
    await _built(session, plot)
    holder_body.node_id = plot.id
    await session.flush()

    mine = await _printer(session, plot, standing=False)
    with pytest.raises(station.CityPlaces):
        await station.place(session, catalog, holder_body, mine)

    #: And the city itself may, on its own ground.
    president, city_body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)
    await _built(session, core)
    theirs = await _printer(session, core, standing=False)
    stood = await station.place(session, catalog, city_body, theirs)

    assert stood.installed
    centre = await town.core(session, city)
    assert centre is not None and centre.id == core.id, "город снова с центром"


async def test_a_printer_is_not_made_inside_a_city_either(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A station built in place stands where it is made (D-268), so the batch
    is the same door -- and it is refused before the twenty hours, not after.
    """
    from src.engine import craft, station

    city, core = await _capital(session, catalog)
    president, body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)
    await _printer(session, core, standing=True)
    await world.learn(session, president, world.BIOPRINTER)

    with pytest.raises(station.OnePrinter):
        await craft.start(session, constants, catalog, body, world.BIOPRINTER, 1)


async def test_the_prison_keeps_its_own_printer(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The one machine the rule does not count, and the one it does not refuse
    (D-174, D-312).

    A prisoner who dies in the face is printed where the prison is, or the
    death becomes an escape through the capital. The penal colony stands on
    city land, so without an exception the rule would forbid the city the very
    printer D-174 requires -- and, worse, would count that printer as the
    city's own: a city that lost the machine it grew from would then measure
    its land from the penal colony and could never build itself a new centre.
    """
    from src.engine import justice, station

    city, core = await _capital(session, catalog)
    president, body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)

    prison = await world.create_node(session, f"{core.key}.gaol", "Каторга", area_m2=100)
    prison.parent_id = core.parent_id
    prison.owner_city_id = city.id
    await session.flush()
    yard = await world.node_container(session, prison)
    await world.grant_item(session, yard, justice.PRISON_CLASS, quality=60, origin="тест")
    await _built(session, prison)
    assert await justice.is_prison(session, prison)

    #: The city already has its own machine: what would refuse anywhere else.
    await _printer(session, core, standing=True)
    body.node_id = prison.id
    await session.flush()

    stood = await station.place(
        session, catalog, body, await _printer(session, prison, standing=False)
    )
    assert stood.installed, "тюрьме принтер положен"

    #: And it is not the city's: neither its centre nor its door.
    centre = await town.core(session, city)
    assert centre is not None and centre.id == core.id
    assert not await world.is_door(session, prison)

    #: Lost its own machine, the city is centreless -- the prison's does not
    #: stand in for it -- and may build a new one.
    from src.models.inventory import Item

    printer = (
        (
            await session.execute(
                select(Item).where(
                    Item.container_id == (await world.node_container(session, core)).id,
                    Item.type_key.in_(world.station_names(world.BIOPRINTER)),
                )
            )
        )
        .scalars()
        .one()
    )
    await session.delete(printer)
    await session.flush()
    assert await town.core(session, city) is None, "каторжный принтер городу не центр"


async def test_beyond_the_walls_a_printer_goes_up_freely(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Land outside a city is nobody's, a printer on it opens no door
    (`world.is_door`), and a city is founded where one already stands (D-023).
    That road stays open -- the rule is about cities, not about machines.
    """
    from src.engine import station

    stamp = uuid.uuid4().hex[:8]
    wild = await world.create_node(session, f"terra.wild.{stamp}", "Пустошь", area_m2=100)
    settler, body = await _resident(session, wild, "Поселенец")
    await _built(session, wild)

    lying = await _printer(session, wild, standing=False)
    stood = await station.place(session, catalog, body, lying)
    assert stood.installed


async def _plot_of(session: AsyncSession, city, core, holder):
    """A bought plot inside the city: the hole the rule is for."""
    plot = await world.create_node(session, f"{core.key}.yard", "Двор", area_m2=100)
    plot.parent_id = core.parent_id
    plot.owner_city_id = city.id
    plot.owner_identity_id = holder.id
    await session.flush()
    await _built(session, plot)
    return plot


async def test_the_plot_holder_does_not_make_one_either(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The door that matters is the batch, not the placing: a printer is built
    in place (D-268), so a holder would never need to "put it up" at all.
    """
    from src.engine import craft, station

    city, core = await _capital(session, catalog)
    holder, body = await _resident(session, core, "Держатель")
    plot = await _plot_of(session, city, core, holder)
    body.node_id = plot.id
    await session.flush()
    await world.learn(session, holder, world.BIOPRINTER)

    with pytest.raises(station.CityPlaces):
        await craft.start(session, constants, catalog, body, world.BIOPRINTER, 1)


async def test_a_batch_of_two_stands_one(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The door is asked once for a batch, and the machines go up at its end:
    without the second half of the rule `units=2` would stand two (D-312).

    The start refuses such a batch outright inside a city; the finish is what
    holds when a batch got past the start -- queued behind another, thawed
    after the master came back, or overtaken by a printer put up meanwhile.
    """
    from src.engine import craft

    city, core = await _capital(session, catalog)
    president, body = await _resident(session, core, "Президент")
    await town.install_founder(session, city, president)
    await world.learn(session, president, world.BIOPRINTER)

    with pytest.raises(craft.CraftError):
        await craft.start(session, constants, catalog, body, world.BIOPRINTER, 2)
