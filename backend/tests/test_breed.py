# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Breeding: seeds, cultivars, crossing, degradation (D-057, D-067).

Checked is what the system was introduced for: **an experienced farmer's
advantage without skills and levels**.

* one sows with seeds, not harvest: the batch has a cultivar and its own strength;
* harvest returns own seed as a multiple of the sowing norm (`farm.seed_return`);
* selection holds the fund, without it the fund degrades, and a hybrid also segregates;
* crossing takes a full cycle, costs seeds and needs a nursery;
* a too similar cultivar **does not sprout** -- the gate is in biology, not the interface;
* the author names the cultivar, and only once it became stable.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import breed, farm, world
from src.models.farm import PlotState
from src.models.inventory import Item
from src.models.plant import Variety
from src.units import PERCENT, amount_float

SPELT = "spelt"


async def _farm(session: AsyncSession, *, area: float = 100, nursery: bool = False):
    stamp = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session,
        f"terra.field.{stamp}",
        "Поле",
        area_m2=area * 4,
        properties={"water": "river", "fertility": 60},
    )
    if nursery:
        yard = await world.node_container(session, node)
        await world.grant_item(session, yard, "breeding_nursery", quality=60, origin="тест")
    identity = await world.create_identity(session, f"Фермер-{stamp}")
    body = await world.print_body(session, identity, node)
    node.owner_identity_id = identity.id
    await session.flush()
    return node, identity, body


async def _seeds(
    session: AsyncSession, catalog: Catalog, body, cultivar: Variety, qty=500, strength=PERCENT
) -> Item:
    pocket = await world.body_container(session, body)
    return await breed.seed_lot(session, catalog, pocket.id, cultivar, qty, strength)


async def _until_harvest(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body,
    seeds: Item,
    *,
    area: float = 100,
    care_count: int | None = None,
):
    """Survey, plough, sow and bring the plot to ripeness."""
    plot = await farm.mark(session, constants, body, name="Делянка", area=area)
    plot.state = PlotState.PLOWED
    await session.flush()
    await farm.sow(session, constants, catalog, body, plot, seeds)

    plant = catalog.plants.by_id(plot.culture_id)
    #: The life by the test's hands (D-293): ripeness is checked in the farming
    #: tests, and the health stands for the care given -- whole, or a share of the cycle.
    plot.growth = Decimal(100)
    plot.health = Decimal(100 if care_count is None else round(100 * care_count / plant.cycle_days))
    await session.flush()
    return plot, plot.settled_at


# --- seeds -------------------------------------------------------------------


async def test_sow_with_seeds_not_harvest(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Seeds are an item: they are bought, stolen and lost with death (D-057)."""
    _, _, body = await _farm(session)
    cultivar = await breed.landrace(session, catalog, SPELT)
    seeds = await _seeds(session, catalog, body, cultivar)
    before = seeds.amount

    plot = await farm.mark(session, constants, body, name="Делянка", area=50)
    plot.state = PlotState.PLOWED
    await session.flush()
    await farm.sow(session, constants, catalog, body, plot, seeds)

    assert plot.variety_id == cultivar.id, "сорт переехал на делянку"
    went = amount_float(before - seeds.amount)
    assert went == pytest.approx(constants[R.FARM_SEED_RATE] * 50)


async def test_cannot_sow_harvest(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Grain is food, not sowing material: it has no cultivar."""
    _, _, body = await _farm(session)
    pocket = await world.body_container(session, body)
    grain = await world.grant_item(session, pocket, "grain", amount=500, quality=50, origin="тест")
    plot = await farm.mark(session, constants, body, name="Делянка", area=50)
    plot.state = PlotState.PLOWED
    await session.flush()

    with pytest.raises(breed.NotSeeds):
        await farm.sow(session, constants, catalog, body, plot, grain)


async def test_harvest_leaves_own_seed(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The fund comes back as a multiple of what was sown (`farm.seed_return`, D-257)."""
    _, _, body = await _farm(session)
    cultivar = await breed.landrace(session, catalog, SPELT)
    seeds = await _seeds(session, catalog, body, cultivar)
    plot, moment = await _until_harvest(session, constants, catalog, body, seeds)

    collected = await farm.harvest(
        session, constants, catalog, body, plot, select_seed=True, now=moment
    )
    plant = catalog.plants.by_id(SPELT)
    pocket = await world.body_container(session, body)
    fund = (
        (
            await session.execute(
                select(Item).where(Item.container_id == pocket.id, Item.type_key == plant.seed)
            )
        )
        .scalars()
        .all()
    )
    new_ = sum(amount_float(i_.amount) for i_ in fund if i_.id != seeds.id)
    #: The same soil, care and strength shares scale the goods and the seeds,
    #: so the shares cancel: seeds = seed_rate * seed_return * goods / yield.
    assert new_ == pytest.approx(
        constants[R.FARM_SEED_RATE]
        * constants[R.FARM_SEED_RETURN]
        * collected
        / plant.yield_per_m2,
        rel=0.01,
    )
    #: The OQ-112 promise: a full-care cycle multiplies the fund -- the farmer
    #: lives off it instead of walking back to the meadow every cycle.
    assert new_ > constants[R.FARM_SEED_RATE] * 100, "фонд обязан покрыть свой пересев"


# --- degradation -------------------------------------------------------------


async def test_fund_degrades_without_selection_holds_with_it(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The seed fund needs care: otherwise a crop is established once and for all."""
    cultivar = await breed.landrace(session, catalog, SPELT)
    drop = constants[R.BREED_DEGRADATION_PER_GEN]

    with_selection = breed.next_vigor(constants, cultivar, PERCENT, selected=True)
    without_selection = breed.next_vigor(constants, cultivar, PERCENT, selected=False)
    assert with_selection == PERCENT
    assert without_selection == pytest.approx(PERCENT + drop)
    assert drop < 0, "вольт задал потерю отрицательной — движок её складывает"


async def test_hybrid_seeds_segregate_more(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A hybrid is good once: the buyer will come back -- that is the business (D-057)."""
    cultivar = await breed.landrace(session, catalog, SPELT)
    #: A hybrid always has an author -- an authorless non-base row is exactly
    #: what `uq_variety_authorless` refuses.
    breeder = await world.create_identity(session, f"Селекционер-{uuid.uuid4().hex[:8]}")
    hybrid = Variety(
        culture_id=SPELT,
        name=None,
        generation=1,
        stable=False,
        author_identity_id=breeder.id,
        traits=cultivar.traits,
    )
    session.add(hybrid)
    await session.flush()

    of_cultivar = breed.next_vigor(constants, cultivar, PERCENT, selected=False)
    of_hybrid = breed.next_vigor(constants, hybrid, PERCENT, selected=False)
    assert of_hybrid < of_cultivar
    assert of_hybrid == pytest.approx(
        PERCENT + constants[R.BREED_DEGRADATION_PER_GEN] + constants[R.BREED_HYBRID_DECAY]
    )


async def test_weak_seed_yields_less(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Batch strength is not decoration: it is directly in the harvest."""
    _, _, full = await _farm(session)
    _, _, weak = await _farm(session)
    cultivar = await breed.landrace(session, catalog, SPELT)

    many = await _seeds(session, catalog, full, cultivar, strength=PERCENT)
    little = await _seeds(session, catalog, weak, cultivar, strength=PERCENT / 2)

    plot_a, moment_a = await _until_harvest(session, constants, catalog, full, many)
    plot_b, moment_b = await _until_harvest(session, constants, catalog, weak, little)
    harvest_a = await farm.harvest(session, constants, catalog, full, plot_a, now=moment_a)
    harvest_b = await farm.harvest(session, constants, catalog, weak, plot_b, now=moment_b)
    assert harvest_b == pytest.approx(harvest_a / 2, rel=0.01)


# --- crossing ----------------------------------------------------------------


async def test_crossing_takes_cycle_and_needs_nursery(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Breeding is an occupation of weeks: the result does not come at once."""
    _, _, without_nursery = await _farm(session)
    cultivar = await breed.landrace(session, catalog, SPELT)
    a = await _seeds(session, catalog, without_nursery, cultivar)
    b = await _seeds(session, catalog, without_nursery, cultivar)
    with pytest.raises(breed.NoNursery):
        await breed.cross(session, constants, catalog, without_nursery, a, b)

    _, _, breeder = await _farm(session, nursery=True)
    one = await _seeds(session, catalog, breeder, cultivar)
    other = await _seeds(session, catalog, breeder, cultivar)
    #: The moment is set explicitly: the database sets `started_at`, and its
    #: `now()` is frozen for the transaction -- comparing it with the test clock is pointless.
    start = datetime.now(UTC)
    nursery = await breed.cross(session, constants, catalog, breeder, one, other, now=start)

    plant = catalog.plants.by_id(SPELT)
    cycle = timedelta(hours=plant.cycle_days * constants[R.TIME_DAY_TERRA])
    assert nursery.ready_at == start + cycle
    with pytest.raises(breed.BreedError):
        await breed.gather_cross(
            session,
            constants,
            catalog,
            breeder,
            nursery,
            now=nursery.started_at,
        )


async def test_too_similar_cultivar_does_not_sprout(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The gate is built into biology: the breeder gets an empty bed (D-067)."""
    _, _, body = await _farm(session, nursery=True)
    cultivar = await breed.landrace(session, catalog, SPELT)
    a = await _seeds(session, catalog, body, cultivar)
    b = await _seeds(session, catalog, body, cultivar)

    #: The parents are one and the same base cultivar: the offspring is indistinguishable from it.
    nursery = await breed.cross(session, constants, catalog, body, a, b)
    came_out = await breed.gather_cross(
        session,
        constants,
        catalog,
        body,
        nursery,
        now=nursery.ready_at,
        rng=random.Random(1),
    )
    assert came_out is None, "неотличимое не прорастает"
    assert nursery.done and nursery.result_variety_id is None


async def test_different_parents_give_new_cultivar(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Traits are the parents' mean with deviation: the vault formula verbatim."""
    _, identity, body = await _farm(session, nursery=True)
    base = await breed.landrace(session, catalog, SPELT)
    #: The second parent is noticeably different -- such appears in the world
    #: by selection, so it has an author (`uq_variety_authorless`).
    other = Variety(
        culture_id=SPELT,
        name="Скороспелка",
        generation=0,
        stable=True,
        author_identity_id=identity.id,
        traits={
            **base.traits,
            "yield_per_m2": base.traits["yield_per_m2"] * 2,
            "cycle_days": base.traits["cycle_days"] / 2,
        },
    )
    session.add(other)
    await session.flush()

    a = await _seeds(session, catalog, body, base)
    b = await _seeds(session, catalog, body, other)
    nursery = await breed.cross(session, constants, catalog, body, a, b)
    hybrid = await breed.gather_cross(
        session,
        constants,
        catalog,
        body,
        nursery,
        now=nursery.ready_at,
        rng=random.Random(7),
    )

    assert hybrid is not None, "разные родители дают различимое потомство"
    assert hybrid.author_identity_id == identity.id
    assert not hybrid.stable, "первое поколение — гибрид, а не сорт"
    #: Each trait is between the parents', with deviation per the vault.
    deviation = breed._drift_share(constants)  # noqa: SLF001
    for key in ("yield_per_m2", "cycle_days"):
        one, two = base.traits[key], other.traits[key]
        middle = (one + two) / 2
        spread_ = abs(one - two)
        assert abs(hybrid.traits[key] - middle) <= spread_ * deviation * 2 + 1e-6


async def test_wild_ancestor_is_a_second_parent(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The meadow's cultivar is not the base one with a discount (D-260).

    Its traits differ by the vault map, and that difference is the genetic
    material breeding stands on -- with one cultivar in the world, crossing
    was self times self and the spread was zero.
    """
    wild = await breed.wild_ancestor(session, constants, catalog, SPELT)
    base = await breed.landrace(session, catalog, SPELT)
    assert wild.id != base.id, "дикий предок и базовый сорт — разные строки"
    assert wild.wild and not base.wild
    assert wild.stable and wild.author_identity_id is None

    factors = constants[R.BREED_WILD_TRAITS]
    for key, factor in factors.items():
        assert wild.traits[key] == pytest.approx(base.traits[key] * factor)
    #: The map's whole point: the two cultivars clear the viability gate.
    assert breed.distance(wild.traits, base.traits) >= (constants[R.BREED_DISTINCTNESS_THRESHOLD])

    #: Lazy creation is idempotent, and the base cultivar keeps resolving to
    #: itself with the wild one present: the flag tells them apart.
    assert (await breed.wild_ancestor(session, constants, catalog, SPELT)).id == wild.id
    assert (await breed.landrace(session, catalog, SPELT)).id == base.id


async def test_authorless_cultivar_carries_a_key_not_a_literal(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Base and wild lines store no display word (D-251).

    The wire names them by their plants-domain key and the client says the
    word in the reader's language; a stored Russian name froze one language
    into rows every language reads. An author's name is a mark and travels
    literally; a nameless hybrid travels as its generation.
    """
    base = await breed.landrace(session, catalog, SPELT)
    wild = await breed.wild_ancestor(session, constants, catalog, SPELT)
    assert base.name is None and wild.name is None, "authorless rows keep no display literal"

    assert breed.shown_as(catalog, base) == {"key": SPELT}
    wild_key = catalog.plants.by_id(SPELT).wild_id
    assert wild_key and wild_key != SPELT, "the vault pins a wild key of its own"
    assert breed.shown_as(catalog, wild) == {"key": wild_key}

    author = await world.create_identity(session, f"Селекционер-{uuid.uuid4().hex[:8]}")
    hybrid = Variety(
        culture_id=SPELT,
        name=None,
        author_identity_id=author.id,
        generation=2,
        stable=False,
        traits=base.traits,
    )
    session.add(hybrid)
    await session.flush()
    assert breed.shown_as(catalog, hybrid) == {"hybrid": 2}
    hybrid.name = "Заря"
    assert breed.shown_as(catalog, hybrid) == {"name": "Заря"}


async def test_the_viability_gate_is_symmetric(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Improvement and degradation measure the same against a neighbour (D-260).

    Dividing by the larger of the two let a worsening through and never an
    improvement: x0.85 measured 15% while x1.15 measured 13% -- the gate bred
    degradations by arithmetic alone.
    """
    base = await breed.landrace(session, catalog, SPELT)
    better = {**base.traits, "yield_per_m2": base.traits["yield_per_m2"] * 1.15}
    worse = {**base.traits, "yield_per_m2": base.traits["yield_per_m2"] * 0.85}
    up = breed.distance(better, base.traits)
    down = breed.distance(worse, base.traits)
    assert up == pytest.approx(down)
    assert up == pytest.approx(15.0)


async def test_wild_cross_sprouts_and_the_hybrid_carries_heterosis(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Wild x base is the crossing the world starts with, and it works (D-260).

    The F1 lot leaves the nursery stronger than a hundred: the hybrid outdoes
    both parents through lot strength -- and exactly one sowing long.
    """
    _, _, body = await _farm(session, nursery=True)
    wild = await breed.wild_ancestor(session, constants, catalog, SPELT)
    base = await breed.landrace(session, catalog, SPELT)

    a = await _seeds(session, catalog, body, wild)
    b = await _seeds(session, catalog, body, base)
    nursery = await breed.cross(session, constants, catalog, body, a, b)
    hybrid = await breed.gather_cross(
        session,
        constants,
        catalog,
        body,
        nursery,
        now=nursery.ready_at,
        rng=random.Random(7),
    )
    assert hybrid is not None, "дикий предок и базовый сорт различимы — потомство всходит"

    pocket = await world.body_container(session, body)
    lot = (
        await session.execute(
            select(Item).where(Item.container_id == pocket.id, Item.variety_id == hybrid.id)
        )
    ).scalar_one()
    assert float(lot.vigor) == pytest.approx(breed.FULL_VIGOR + constants[R.BREED_HYBRID_VIGOR])

    #: Heterosis is not inherited: the offspring starts from a hundred at
    #: best, and without selection the hybrid also segregates.
    kept = breed.next_vigor(constants, hybrid, float(lot.vigor), selected=True)
    assert kept == pytest.approx(breed.FULL_VIGOR)
    dropped = breed.next_vigor(constants, hybrid, float(lot.vigor), selected=False)
    assert dropped == pytest.approx(
        breed.FULL_VIGOR + constants[R.BREED_DEGRADATION_PER_GEN] + constants[R.BREED_HYBRID_DECAY]
    )


async def test_name_only_for_stable_cultivar_and_only_by_author(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """The author's name is attached forever -- like a craftsman's mark on a product."""
    _, _, author = await _farm(session)
    _, _, foreign = await _farm(session)
    base = await breed.landrace(session, catalog, SPELT)
    hybrid = Variety(
        culture_id=SPELT,
        name=None,
        generation=1,
        stable=False,
        traits=base.traits,
        author_identity_id=author.identity_id,
    )
    session.add(hybrid)
    await session.flush()

    with pytest.raises(breed.NotStable):
        await breed.name_variety(session, author, hybrid, "Тэрновка")

    #: Generations of selection bring the hybrid to constancy.
    threshold = constants[R.BREED_GENERATIONS_TO_STABILIZE]
    for _ in range(int(threshold.max)):
        await breed.select_generation(session, constants, hybrid)
    assert hybrid.stable

    with pytest.raises(breed.BreedError):
        await breed.name_variety(session, foreign, hybrid, "Чужовка")

    await breed.name_variety(session, author, hybrid, "Тэрновка")
    assert hybrid.name == "Тэрновка"
