# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""What the farm tests share: the farmstead and the crops they sow.

A kit, not a conftest: pytest does not collect it, and a real fixture must
not live here (CLAUDE.md) -- these are plain helpers imported by name.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import breed, farm, world
from src.engine.farm import life
from src.models.farm import PlotState
from src.models.identity import Body
from src.models.inventory import Item
from src.models.job import Job, JobKind, JobState
from src.units import HARDINESS_SCALE, PERCENT, amount_float

SPELT = "spelt"
BEANS = "beans"
#: The least demanding crop of the catalog: its fertility norm is 10, which is
#: exactly what made the uncapped soil share a tenfold multiplier (OQ-107).
BROME = "brome"


async def _farmstead(
    session: AsyncSession, *, water: str = "river", fertility: float = 55, area: float = 200
):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session,
        f"terra.farm.{stamp}",
        "Хутор",
        area_m2=area,
        properties={"water": water, "fertility": fertility},
    )
    identity = await world.create_identity(session, f"Фермер-{stamp}")
    body = await world.print_body(session, identity, node)
    #: The holder runs the estate: the fixture's farmer has already taken their plot.
    node.owner_identity_id = identity.id
    await session.flush()
    return node, identity, body


def _norms(constants: Constants, catalog: Catalog, culture: str = SPELT) -> life.Norms:
    """The crop's own norms: what a bed of the base line asks for (D-296)."""
    plant = catalog.plants.by_id(culture)
    return life.norms(constants, plant, breed.traits_of_plant(plant))


def _weather(rain: float = 0.0, river: bool = False, temperature: float | None = None):
    """A place that does not breathe: one temperature for every hour."""
    return life.Weather(rain=rain, river=river, temperature_at=lambda _hours: temperature)


async def _sown(session, constants, catalog, body, *, culture: str = SPELT, area: float = 10):
    """A plot brought to sowing, skipping the wait for ploughing."""
    plot = await farm.mark(session, constants, body, name="грядка", area=area)
    plot.state = PlotState.PLOWED
    await session.flush()
    cultivar = await breed.landrace(session, catalog, culture)
    pocket = await world.body_container(session, body)
    seeds = await breed.seed_lot(session, catalog, pocket.id, cultivar, 200, PERCENT)
    return await farm.sow(session, constants, catalog, body, plot, seeds)


async def _hands_free(session: AsyncSession, body: Body) -> None:
    """End an action's minutes: the job that held the hands is swept."""
    jobs = await session.execute(
        select(Job).where(Job.body_id == body.id, Job.kind == JobKind.FARM_CARE.value)
    )
    for job in jobs.scalars():
        job.state = JobState.CANCELLED
    await session.flush()


async def _stock(session: AsyncSession, pocket_id: uuid.UUID, goods: str) -> float:
    """What the pocket holds of one thing, as a number."""
    left = await session.scalar(
        select(func.coalesce(func.sum(Item.amount), 0)).where(
            Item.container_id == pocket_id, Item.type_key == goods
        )
    )
    return amount_float(int(left))


def _stand(constants: Constants, plant) -> float:
    """The share an unthinned stand keeps at the harvest (D-297): every test of
    the harvest formula reaps a bed nobody thinned."""
    return (
        1 - plant.traits.density_risk / HARDINESS_SCALE * constants[R.FARM_CROWD_PENALTY] / PERCENT
    )
