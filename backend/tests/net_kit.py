# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The Net tests' shared scene: a city whose channel is official.

Used by `test_net.py` and `test_query_budget.py` -- both need a reader whose
channel list has all three sources in it, and the city is the only one that
takes building. Not collected by pytest.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog
from src.engine import city as town
from src.engine import world
from src.models.world import Layer


async def _capital(session: AsyncSession, catalog: Catalog):
    """A city, its core node, and the founder who may write in its channel.

    Not `city_kit._capital`: that one builds a treasury and a town hall for the
    city files and knows nothing of who speaks. What the Net wants is the
    founder installed, because the right to post is a city power (D-222).
    """
    stamp = uuid.uuid4().hex[:8]
    planet = await world.create_node(
        session, f"terra.{stamp}", "Терра", area_m2=1, layer=Layer.SPACE
    )
    delegate = await world.create_node(
        session, f"terra.city.{stamp}", "Столица", area_m2=1, layer=Layer.PLANET, parent=planet
    )
    core = await world.create_node(
        session, f"terra.city.{stamp}.core", "Ядро", area_m2=100, parent=delegate
    )
    founder = await world.create_identity(session, f"Основатель-{stamp}")
    await world.print_body(session, founder, core)
    #: Stamped: a city name is unique across the world
    #: (`uq_city_name_lower`), and this kit raises two capitals in one test.
    city = await town.found(session, catalog, delegate, f"Столица-{stamp}")
    await town.install_founder(session, city, founder)
    core.owner_city_id = city.id
    await session.flush()
    return city, core, founder
