# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What the field automaton tests share: the field, its machine and its stores.

A kit, not a conftest: pytest does not collect it, and a real fixture must
not live here (CLAUDE.md) -- these are plain helpers imported by name.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from farm_kit import _hands_free
from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import agro, breed, energy, farm, ledger, storage, world
from src.models.agro import FieldAutomat
from src.models.event import Event, EventKind
from src.models.farm import Plot, PlotState
from src.models.identity import Body, Identity
from src.models.inventory import Container, Item
from src.models.ledger import AccountKind, PostingReason
from src.models.world import Layer, Node
from src.units import PERCENT, amount_float, money

SPELT = "spelt"
LUBRICANT = "lubricant"
MACHINE = "field_automaton"


@dataclass
class Field:
    node: Node
    yard: Container
    identity: Identity
    body: Body
    machine: Item


async def field(
    session: AsyncSession,
    constants: Constants,
    *,
    water: str = "none",
    fertility: float = 55,
    stored_energy: float = 10_000,
    funded: bool = True,
) -> Field:
    """A city field: the owner's node with a pool, and a field automaton standing in its yard."""
    stamp = uuid.uuid4().hex[:8]
    capital = await world.create_node(
        session, f"terra.agro.{stamp}", "Столица", area_m2=1, layer=Layer.PLANET
    )
    node = await world.create_node(
        session,
        f"terra.agro.{stamp}.field",
        "Поле",
        area_m2=5000,
        layer=Layer.PLANET,
        parent=capital,
        properties={"water": water, "fertility": fertility},
    )
    identity = await world.create_identity(session, f"Агроном-{stamp}")
    body = await world.print_body(session, identity, node)
    node.owner_identity_id = identity.id
    yard = await world.node_container(session, node)
    machine = await world.grant_item(session, yard, MACHINE, quality=70, origin="тест")
    pool = await energy.pool_of(session, constants, node)
    assert pool is not None
    pool.stored = Decimal(str(stored_energy))
    if funded:
        account = await ledger.account_for(session, AccountKind.IDENTITY, identity.id)
        genesis = await ledger.account_for(session, AccountKind.GENESIS, None)
        await ledger.transfer(
            session,
            PostingReason.GENESIS,
            debit=genesis.id,
            credit=account.id,
            amount=money(100_000),
            memo={},
        )
    await session.flush()
    return Field(node, yard, identity, body, machine)


async def liquid_in(
    session: AsyncSession, yard: Container, goods: str, units: float, vessel: str = "fuel_tank"
) -> Item:
    """A liquid standing in the yard, in a vessel of its own (D-230)."""
    tank = await world.grant_item(session, yard, vessel, quality=60, origin="тест")
    inside = await storage.inside(session, tank)
    return await world.grant_item(session, inside, goods, amount=units, quality=55, origin="тест")


async def chest(session: AsyncSession, yard: Container, kind: str = "chest") -> Item:
    return await world.grant_item(session, yard, kind, quality=60, origin="тест")


async def seeds_in(
    session: AsyncSession, catalog: Catalog, store: Item, culture: str, units: float
) -> Item:
    """A lot of the base cultivar's seeds, at full strength, in a storage."""
    cultivar = await breed.landrace(session, catalog, culture)
    inside = await storage.inside(session, store)
    return await breed.seed_lot(session, catalog, inside.id, cultivar, units, PERCENT)


async def goods_in(session: AsyncSession, store: Item, goods: str, units: float) -> Item:
    inside = await storage.inside(session, store)
    return await world.grant_item(session, inside, goods, amount=units, quality=60, origin="тест")


async def plot_of(
    session: AsyncSession, constants: Constants, body: Body, area: float = 40, name: str = "поле"
) -> Plot:
    return await farm.mark(session, constants, body, name=name, area=area)


async def growing(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    now: datetime,
    *,
    area: float = 40,
    culture: str = SPELT,
) -> Plot:
    """A plot sown by hand at `now`, the hands let go."""
    plot = await plot_of(session, constants, body, area)
    plot.state = PlotState.PLOWED
    await session.flush()
    cultivar = await breed.landrace(session, catalog, culture)
    pocket = await world.body_container(session, body)
    need = constants[R.FARM_SEED_RATE] * area
    lot = await breed.seed_lot(session, catalog, pocket.id, cultivar, need, PERCENT)
    await farm.sow(session, constants, catalog, body, plot, lot, now=now)
    await _hands_free(session, body)
    return plot


async def programmed(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    place: Field,
    steps: list[dict[str, Any]],
    plots: list[Plot],
    now: datetime,
    **stores: Item | None,
) -> FieldAutomat:
    """Set the machine as its owner would, with the stores named by keyword."""
    return await agro.program(
        session,
        constants,
        catalog,
        place.body,
        place.machine,
        steps=steps,
        plots=[str(plot.id) for plot in plots],
        seeds=None if stores.get("seeds") is None else stores["seeds"].id,
        fertilizer=None if stores.get("fertilizer") is None else stores["fertilizer"].id,
        harvest=None if stores.get("harvest") is None else stores["harvest"].id,
        now=now,
    )


def second_now() -> datetime:
    """A whole second of now: the machine's stamps compare to the second."""
    return datetime.now(UTC).replace(microsecond=0)


async def events_of(session: AsyncSession, kind: EventKind) -> int:
    """How many events of one kind the journal holds."""
    return int(await session.scalar(select(func.count()).where(Event.kind == kind.value)) or 0)


async def held_in(session: AsyncSession, store: Item, goods: str) -> float:
    """What a storage holds of one thing, as a number."""
    inside = await storage.inside(session, store, create=False)
    if inside is None:
        return 0.0
    total = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == inside.id, Item.type_key == goods
        )
    )
    return amount_float(int(total))
