# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The map is a neighbourhood, not a world (D-240).

Until this rule the map answered with every node there was, to anybody, with no
token: exploration was worth nothing, because a planet opened by somebody
else's scout was on everybody's screen the same second, and another planet's
surface could be read by whoever clicked its sphere.

What is checked here is the whole of the rule:

* two steps of the graph around the body, and the third node is **not** there;
* the sky is everybody's -- planets are arithmetic over the epoch, not
  intelligence -- and it carries no way in: another planet's surface is absent,
  so there is nothing to expand;
* whatever is visible brings its parents, or a plot would arrive with no city
  to stand in and the layer above it would come out empty;
* no body, no surface: an anonymous reader gets the sky.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.routes.public import _standing
from src.engine import account as accounts
from src.engine import sight, travel, world
from src.models.identity import Account
from src.models.world import Layer, Node, Planet, Surface


async def _sphere(session: AsyncSession, planet: Planet) -> Node:
    return await world.create_node(
        session, planet.value, planet.value.title(), area_m2=1, planet=planet, layer=Layer.SPACE
    )


async def _node(
    session: AsyncSession, key: str, parent: Node | None, *, planet=Planet.TERRA, layer=Layer.PLANET
) -> Node:
    return await world.create_node(
        session,
        f"{key}.{uuid.uuid4().hex[:6]}",
        key,
        area_m2=100,
        planet=planet,
        layer=layer,
        parent=parent,
    )


async def _chain(session: AsyncSession, nodes: list[Node]) -> None:
    for one, other in zip(nodes, nodes[1:], strict=False):
        await travel.connect(session, one, other, base_seconds=60, surface=Surface.ROAD)


async def _graph(session: AsyncSession) -> tuple[list[Node], list]:
    return await sight.read(session)


async def test_the_map_reaches_two_steps_and_no_further(session: AsyncSession) -> None:
    """Two, because one shows the ways out with nothing to choose between them."""
    terra = await _sphere(session, Planet.TERRA)
    row = [await _node(session, f"terra.step{i}", terra) for i in range(5)]
    await _chain(session, row)

    nodes, edges = await _graph(session)
    seen = sight.around(row[0], nodes=nodes, edges=edges)

    assert row[0].id in seen
    assert row[1].id in seen, "сосед виден"
    assert row[2].id in seen, "и сосед соседа"
    assert row[3].id not in seen, "третий шаг уже за туманом"
    assert row[4].id not in seen


async def test_another_planet_has_no_surface_to_expand(session: AsyncSession) -> None:
    """The sphere is drawn; what is on it is not in the answer at all."""
    terra = await _sphere(session, Planet.TERRA)
    pyroxis = await _sphere(session, Planet.PYROXIS)
    home = await _node(session, "terra.capital", terra)
    plateau = await _node(session, "pyroxis.anvil", pyroxis, planet=Planet.PYROXIS)

    nodes, edges = await _graph(session)
    seen = sight.around(home, nodes=nodes, edges=edges)

    assert pyroxis.id in seen, "планета в небе видна всем: это арифметика орбит"
    assert plateau.id not in seen, "а её поверхность — нет: туда надо долететь"


async def test_what_is_seen_brings_its_parents(session: AsyncSession) -> None:
    """A node is drawn on the layer of its group: without the group, nowhere."""
    terra = await _sphere(session, Planet.TERRA)
    city = await _node(session, "terra.capital", terra)
    inside = await _node(session, "terra.capital.lot", city, layer=Layer.CITY)
    await _chain(session, [city, inside])

    nodes, edges = await _graph(session)
    seen = sight.around(inside, nodes=nodes, edges=edges)

    assert city.id in seen and terra.id in seen


async def test_a_reader_with_no_body_gets_the_sky(session: AsyncSession) -> None:
    """The surface asks for a body; the system does not."""
    terra = await _sphere(session, Planet.TERRA)
    home = await _node(session, "terra.capital", terra)

    nodes, edges = await _graph(session)
    seen = sight.around(None, nodes=nodes, edges=edges)

    assert terra.id in seen
    assert home.id not in seen


async def test_a_token_names_the_body_and_rubbish_names_nobody(session: AsyncSession) -> None:
    """The header is optional, and a stale tab gets a distant map, not an error."""
    terra = await _sphere(session, Planet.TERRA)
    home = await _node(session, "terra.capital", terra)
    identity = await world.create_identity(session, f"Ходок-{uuid.uuid4().hex[:6]}")
    body = await world.print_body(session, identity, home)
    account = await session.get(Account, identity.account_id)
    token = await accounts.issue_token(session, account)

    assert (await _standing(session, f"Bearer {token}")).id == body.node_id
    assert await _standing(session, "Bearer нет-такого") is None
    assert await _standing(session, None) is None
    assert await _standing(session, token) is None, "без схемы это не заголовок"
