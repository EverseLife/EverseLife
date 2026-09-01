# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Agrotech as knowledge (D-057).

Why seeds and knowledge are separated: **anything can be planted, but grown
well only knowing what the plant needs**. No bans; the difference is what the
farmer sees.

* without agrotech the summary gives symptoms and not a single norm number;
* with agrotech -- norms and the remainder to them;
* the agrotech of the eight base crops lies in the Library and is taken on foot;
* the agrotech of a bred cultivar is known only to its author.
"""

from __future__ import annotations

import random
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import breed, farm, world
from src.models.farm import PlotState
from src.models.identity import KnowledgeKind
from src.units import PERCENT

SPELT = "spelt"


async def _field(session: AsyncSession, *, library: bool = False, nursery: bool = False):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session,
        f"terra.agro.{stamp}",
        "Поле",
        area_m2=400,
        #: Dry ground on purpose: the water norm is a norm only where water is
        #: carried (D-126), so a river-fed field would hide the very number
        #: these tests are about -- and thirst is what a dry field shows.
        properties={"water": "none", "fertility": 30, "library": library},
    )
    if nursery:
        yard = await world.node_container(session, node)
        await world.grant_item(session, yard, "breeding_nursery", quality=60, origin="тест")
    identity = await world.create_identity(session, f"Новичок-{stamp}")
    body = await world.print_body(session, identity, node)
    node.owner_identity_id = identity.id
    await session.flush()
    return node, identity, body


async def _sown(session, constants, catalog, body, *, fertility=30.0):
    """A plot with growing spelt on poor land -- so that there is something to ail."""
    cultivar = await breed.landrace(session, catalog, SPELT)
    pocket = await world.body_container(session, body)
    seeds = await breed.seed_lot(session, catalog, pocket.id, cultivar, 500, PERCENT)
    plot = await farm.mark(session, constants, body, name="грядка", area=10)
    plot.state = PlotState.PLOWED
    from decimal import Decimal

    plot.fertility = Decimal(str(fertility))
    await session.flush()
    await farm.sow(session, constants, catalog, body, plot, seeds)
    return plot, cultivar


# --- what is seen ------------------------------------------------------------


async def test_without_agrotech_symptoms_visible_not_norms(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Guess: overwatering or drought? Knowledge turns this into a task."""
    _, identity, body = await _sown_field(session, constants, catalog)

    (line,) = await farm.survey(session, constants, catalog, identity.id)
    assert line["agrotech"] is False
    assert "symptoms" in line and line["symptoms"], "симптом обязан быть виден"
    for num in ("ripe_at", "missed_days", "water_need", "fertility_required"):
        assert num not in line, f"норма {num} без агротехники не показывается"

    #: The land is poor -- the leaf is pale; a day without a round -- the leaf is limp.
    assert "pale" in line["symptoms"]
    assert "thirst" in line["symptoms"]


async def test_with_agrotech_norms_and_remainder_visible(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, identity, body = await _sown_field(session, constants, catalog)
    await world.learn(session, identity, SPELT, kind=KnowledgeKind.AGROTECH)

    (line,) = await farm.survey(session, constants, catalog, identity.id)
    assert line["agrotech"] is True
    assert "symptoms" not in line
    plant = catalog.plants.by_id(SPELT)
    assert line["fertility_required"] == pytest.approx(plant.requires.fertility)
    assert line["water_need"] == pytest.approx(constants[R.FARM_WATER_PER_M2] * 10)
    assert line["ripe_at"] and line["missed_days"] >= 0


async def _sown_field(session, constants, catalog):
    node, identity, body = await _field(session)
    await _sown(session, constants, catalog, body)
    return node, identity, body


# --- where knowledge comes from ----------------------------------------------


async def test_base_agrotech_lies_in_library_and_taken_on_foot(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Free and unconditional, but only in person (D-053)."""
    _, _, in_field = await _field(session)
    with pytest.raises(breed.BreedError):
        await breed.copy_agrotech(session, catalog, in_field, SPELT)

    _, identity, in_library = await _field(session, library=True)
    knowledge = await breed.copy_agrotech(session, catalog, in_library, SPELT)
    assert knowledge is not None

    cultivar = await breed.landrace(session, catalog, SPELT)
    assert await breed.knows_agrotech(session, identity.id, cultivar)


async def test_bred_agrotech_known_only_to_author(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The breeder's monopoly: it cannot be taken in the Library (D-057)."""
    _, author_identity, author = await _field(session, nursery=True)
    _, foreign_identity, _ = await _field(session)

    base = await breed.landrace(session, catalog, SPELT)
    from src.models.plant import Variety

    other = Variety(
        culture_id=SPELT,
        name="Скороспелка",
        stable=True,
        generation=0,
        traits={
            **base.traits,
            "yield_per_m2": base.traits["yield_per_m2"] * 2,
            "cycle_days": base.traits["cycle_days"] / 2,
        },
    )
    session.add(other)
    await session.flush()

    pocket = await world.body_container(session, author)
    a = await breed.seed_lot(session, catalog, pocket.id, base, 500, PERCENT)
    b = await breed.seed_lot(session, catalog, pocket.id, other, 500, PERCENT)
    nursery = await breed.cross(session, constants, catalog, author, a, b)
    hybrid = await breed.gather_cross(
        session,
        constants,
        catalog,
        author,
        nursery,
        now=nursery.ready_at,
        rng=random.Random(7),
    )
    assert hybrid is not None

    assert await breed.knows_agrotech(session, author_identity.id, hybrid)
    assert not await breed.knows_agrotech(session, foreign_identity.id, hybrid)
    #: And it is not in the Library: only the base eight get there. Here the
    #: author additionally does not stand in the Library, so the refusal comes
    #: earlier -- on presence (D-053).

    with pytest.raises(breed.BreedError):
        await breed.copy_agrotech(session, catalog, author, str(hybrid.id))
