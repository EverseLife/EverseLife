# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The starting world: built by the same rules as a player's city (D-216).

There was no test for the seed at all, and it cost dearly. Renaming the
engine's constants to thing classes (D-215) left the capital holding «Терминал»
and «Верфь» -- names the vault does not know -- and `python -m src.seed` died
on its first look at the marketplace. Seven hundred tests missed it, because
every one of them builds its own small world by hand and none of them builds
*the* world.

So this file builds the real one and asks the questions only it can answer:
is everything in it a thing the vault knows, did it come from a recipe, and
does the engine recognise the city it was handed.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, ConstantError
from src.constants.catalog import ItemKind
from src.engine import justice, market, ship, world
from src.models.event import Event, EventKind
from src.models.inventory import Container, ContainerKind, Item
from src.models.world import Node
from src.seed import seed


@pytest.fixture
async def capital(session: AsyncSession) -> Node:
    """The starting world, created once for the test that asks about it."""
    return await seed(session)


async def _things(session: AsyncSession) -> list[tuple[str, str, int]]:
    rows = (
        await session.execute(
            select(Node.key, Item.type_key, func.sum(Item.amount))
            .join(Container, Container.owner_id == Node.id)
            .join(Item, Item.container_id == Container.id)
            .where(Container.kind == ContainerKind.NODE)
            .group_by(Node.key, Item.type_key)
        )
    ).all()
    return [(key, thing, total) for key, thing, total in rows]


def _known(catalog: Catalog, name: str) -> bool:
    """Whether the vault knows this thing at all: by recipe or by the registry."""
    book = catalog.recipes
    try:
        book.recipe(name)
        return True
    except ConstantError:
        return any(material.name == name for material in book.materials)


async def test_capital_holds_only_things_the_vault_knows(
    capital: Node, session: AsyncSession, catalog: Catalog
) -> None:
    """Every item of the starting world is a thing, not a class and not a word.

    This is the guard that was missing. A class name placed as an item looks
    like a machine in the database and is invisible to the engine, which finds
    machines by class membership -- so the marketplace had a terminal nobody
    could trade at.
    """
    unknown = [
        (key, thing) for key, thing, _ in await _things(session)
        if not _known(catalog, thing)
    ]
    assert not unknown, f"в столице стоит то, чего нет в вольте: {unknown}"


async def test_the_engine_recognises_the_city_it_was_handed(
    capital: Node, session: AsyncSession, catalog: Catalog
) -> None:
    """The seed places things; the engine looks for classes. They must meet."""
    async def node(key: str) -> Node:
        found = (
            await session.execute(select(Node).where(Node.key == key))
        ).scalar_one()
        return found

    #: Trade at all: no terminal, no order book (D-003).
    await market.terminal(session, await node("terra.capital.market"))
    #: A ship couples to whatever the spaceport class stands in (D-206).
    assert await world.has_station(session, await node("terra.capital.port"), ship.SPACEPORT)
    #: The library window is shown where its machine stands (D-176).
    assert await world.is_library(session, await node("terra.capital.library"))
    #: A penal colony is a machine, not a property (D-174).
    assert await justice.is_prison(session, await node("terra.capital.jail"))
    #: The door into the world never closes (D-028).
    assert await world.is_door(session, await node("terra.capital.core"))


async def test_the_capital_is_assembled_from_recipes(
    capital: Node, session: AsyncSession, catalog: Catalog
) -> None:
    """Matter enters the world once, as raw material, and the rest is made of it.

    The capital used to be conjured machine by machine. Now the seed walks the
    same ladder a player walks, so a broken rung -- a recipe without a
    composition, an input nobody makes, a circle -- stops the world from being
    created at all instead of handing the player a city they could not repeat.
    """
    payloads = (
        await session.execute(
            select(Event.payload).where(Event.kind == EventKind.ITEM_CREATED)
        )
    ).scalars().all()
    grounds = [str(payload.get("origin", "")) for payload in payloads]

    assert any("сырьё столицы" in ground for ground in grounds), "сырьё не пришло в мир"
    assert any("по рецепту" in ground for ground in grounds), "ничего не собрано переделом"
    #: Nothing arrives without a named ground (pillar P1).
    assert all(grounds), "материя появилась без основания"

    #: The assembly spends exactly what it laid out: leftovers would mean the
    #: bill and the spend disagree, and matter would quietly accumulate.
    book = catalog.recipes
    deliberate = {"Уголь", "Железная руда"}
    left = [
        (key, thing) for key, thing, _ in await _things(session)
        if book.is_raw(thing) and thing not in deliberate
    ]
    assert not left, f"после сборки в узлах осталось сырьё: {left}"


async def test_every_capital_machine_could_be_made_by_a_player(
    capital: Node, session: AsyncSession, catalog: Catalog
) -> None:
    """What the Forerunners built, a player can build too.

    Not a formality: the capital is the only picture of a finished city the
    player ever sees, and a machine in it that no recipe makes is a promise the
    world cannot keep.
    """
    from src.engine import craft

    book = catalog.recipes
    for _, thing, _ in await _things(session):
        try:
            recipe = book.recipe(thing)
        except ConstantError:
            continue
        if recipe.kind not in (ItemKind.STATION, ItemKind.FURNITURE):
            continue
        method = craft.procedure(catalog, thing)
        assert method.per_unit, f"«{thing}» не из чего делать"
        if method.station is not None:
            made = book.recipe(method.station)
            assert made.kind is ItemKind.STATION, (
                f"«{thing}» делается на «{method.station}», а это не станция"
            )
