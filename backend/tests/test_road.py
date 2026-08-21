"""Roads as work on an edge (D-107, D-158).

Checked is what the road was introduced for at all:

* the surface rises **by a tier** for surface material and time, not by a button;
* a laid road opens the convoy a path that did not exist before it;
* without maintenance a road overgrows and returns to offroad;
* resurfacing costs exactly the share by which the road sagged.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import jobs, road, transport, travel, world
from src.models.world import Edge, Surface
from src.units import SCALE_MAX


async def _edge(
    session: AsyncSession, *, surface: Surface = Surface.TRAIL, surface_amount: float = 0
):
    stamp = uuid.uuid4().hex[:8]
    here = await world.create_node(session, f"terra.rda.{stamp}", "Здесь", area_m2=100)
    there = await world.create_node(session, f"terra.rdb.{stamp}", "Там", area_m2=100)
    edge = await travel.connect(
        session, here, there, base_seconds=600, surface=surface
    )
    identity = await world.create_identity(session, f"Дорожник-{stamp}")
    body = await world.print_body(session, identity, here)
    if surface_amount:
        pocket = await world.body_container(session, body)
        await world.grant_item(
            session, pocket, "Дорожное полотно", amount=surface_amount,
            origin="сценарий теста",
        )
    return here, there, body, edge


async def _finish(session: AsyncSession, job) -> None:
    """Run the work to the end -- the same way the worker would."""
    from src.models.job import JobState

    await road.finished(session, job)
    job.state = JobState.DONE
    await session.flush()


# --- laying ------------------------------------------------------------------


async def test_road_laid_for_surface_and_time(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    norm = constants[R.ROAD_SURFACE_PER_EDGE]
    _, _, body, edge = await _edge(session, surface_amount=norm)
    gone = datetime.now(UTC)

    job = await road.lay(session, constants, catalog, body, edge, now=gone)

    assert edge.surface is Surface.TRAIL, "до срока дорога не готова"
    assert job.run_at - gone == timedelta(hours=constants[R.ROAD_BUILD_HOURS])
    assert await road._surface_at_hand(session, body) == pytest.approx(0), (
        "полотно списывается вперёд, как материалы партии"
    )

    await _finish(session, job)
    assert edge.surface is Surface.ROAD
    assert float(edge.condition) == pytest.approx(SCALE_MAX)


async def test_no_road_without_surface(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A road is materials, not intent."""
    norm = constants[R.ROAD_SURFACE_PER_EDGE]
    _, _, body, edge = await _edge(session, surface_amount=norm / 2)
    with pytest.raises(road.NoSurfaceGoods):
        await road.lay(session, constants, catalog, body, edge)
    assert await road._surface_at_hand(session, body) == pytest.approx(norm / 2), (
        "отказ не съедает половину полотна"
    )


async def test_laid_standing_at_edge_end(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """A road is laid on foot: there is no remote construction in this world (D-044)."""
    norm = constants[R.ROAD_SURFACE_PER_EDGE]
    _, _, body, _ = await _edge(session, surface_amount=norm)
    _, _, _, foreign_thing = await _edge(session)
    with pytest.raises(road.NotHere):
        await road.lay(session, constants, catalog, body, foreign_thing)


async def test_tiers_go_one_at_a_time(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Offroad -> road -> highway, and each tier is a separate project."""
    norm = constants[R.ROAD_SURFACE_PER_EDGE]
    _, _, body, edge = await _edge(session, surface_amount=norm * 3)

    await _finish(session, await road.lay(session, constants, catalog, body, edge))
    assert edge.surface is Surface.ROAD
    await _finish(session, await road.lay(session, constants, catalog, body, edge))
    assert edge.surface is Surface.PAVED

    with pytest.raises(road.TopSurface):
        await road.lay(session, constants, catalog, body, edge)


async def test_two_crews_do_not_lay_same_road(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    norm = constants[R.ROAD_SURFACE_PER_EDGE]
    _, _, body, edge = await _edge(session, surface_amount=norm * 2)
    await road.lay(session, constants, catalog, body, edge)
    with pytest.raises(road.AlreadyWorking):
        await road.lay(session, constants, catalog, body, edge)


# --- what all this is for (D-157) --------------------------------------------


async def test_laid_road_opens_way_for_convoy(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Exploration grows the map, the road makes it passable."""
    norm = constants[R.ROAD_SURFACE_PER_EDGE]
    here, there, body, edge = await _edge(session, surface_amount=norm)
    yard = await world.node_container(session, here)
    cart = await world.grant_item(
        session, yard, "Повозка", amount=1, origin="сценарий теста"
    )
    await transport.harness(session, constants, catalog, body, cart)

    with pytest.raises(transport.Impassable):
        await travel.depart(session, constants, body, there)

    await _finish(session, await road.lay(session, constants, catalog, body, edge))

    transit = await travel.depart(session, constants, body, there)
    assert transit is not None, "по уложенной дороге обоз идёт"


# --- overgrowing -------------------------------------------------------------


async def test_road_overgrows_without_maintenance(
    session: AsyncSession, constants: Constants
) -> None:
    """An abandoned road returns to offroad -- that is a sink, not a breakage."""
    _, _, _, edge = await _edge(session, surface=Surface.ROAD)
    step = constants[R.ROAD_DECAY_RATE]

    await road.decay(session, constants)
    assert float(edge.condition) == pytest.approx(SCALE_MAX - step)

    #: We bring it to the edge early: nobody will wait a hundred days in a test.
    edge.condition = Decimal(str(step))
    await session.flush()
    overgrown = await road.decay(session, constants)
    assert overgrown == 1
    assert edge.surface is Surface.TRAIL
    assert float(edge.condition) == pytest.approx(SCALE_MAX), (
        "ступень ниже начинает со свежего состояния, а не с нуля"
    )


async def test_offroad_does_not_overgrow_further(
    session: AsyncSession, constants: Constants
) -> None:
    """There are no tiers below the trail, and the daily pass does not touch it."""
    _, _, _, edge = await _edge(session, surface=Surface.TRAIL)
    before = float(edge.condition)
    assert await road.decay(session, constants) == 0
    assert float(edge.condition) == before


async def test_resurfacing_costs_fraction_of_laying(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """One that sagged by half needs half: otherwise maintaining is not worthwhile."""
    norm = constants[R.ROAD_SURFACE_PER_EDGE]
    _, _, body, edge = await _edge(session, surface=Surface.ROAD, surface_amount=norm)
    edge.condition = Decimal(str(SCALE_MAX / 2))
    await session.flush()

    assert road.needed(constants, edge, mend=True) == pytest.approx(norm / 2)
    job = await road.lay(session, constants, catalog, body, edge, mend=True)
    assert await road._surface_at_hand(session, body) == pytest.approx(norm / 2), (
        "подсыпка берёт половину, а не всё"
    )

    await _finish(session, job)
    assert edge.surface is Surface.ROAD, "подсыпка не поднимает ступень"
    assert float(edge.condition) == pytest.approx(SCALE_MAX)


async def test_nothing_to_resurface_on_intact_road(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    norm = constants[R.ROAD_SURFACE_PER_EDGE]
    _, _, body, edge = await _edge(session, surface=Surface.ROAD, surface_amount=norm)
    with pytest.raises(road.RoadError):
        await road.lay(session, constants, catalog, body, edge, mend=True)


# --- the work runs offline ---------------------------------------------------


async def test_road_laid_by_journal_job(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Closed the tab -- the road is laid anyway."""
    async with factory() as session, session.begin():
        _, _, body, edge = await _edge(
            session, surface_amount=constants[R.ROAD_SURFACE_PER_EDGE]
        )
        job = await road.lay(session, constants, catalog, body, edge)
        term, edge_id = job.run_at, edge.id

    assert await jobs.run_one(factory, now=term - timedelta(minutes=1)) is None
    job_row = await jobs.run_one(factory, now=term)
    assert job_row is not None and job_row.kind == "road.work"

    async with factory() as session:
        edge = await session.get(Edge, edge_id)
        assert edge.surface is Surface.ROAD


async def test_surface_is_craftable_at_all(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Road surface is made at a real machine, like everything else (D-216).

    It used to be written down as made at «Стройка» -- a leftover of the recipe
    kind D-106 abolished. The engine read that as "no machine needed", the
    client did not read it at all, and the surface could not be made by anyone:
    the road no player could lay started right here.
    """

    from src.engine import craft

    recipe = catalog.recipes.recipe("Дорожное полотно")
    assert recipe.station not in (None, *craft.BENCHLESS), (
        "полотно делают на рабочей станции, а не «на месте»"
    )
    method = craft.procedure(catalog, "Дорожное полотно")
    assert method.station == catalog.recipes.resolve(recipe.station)
