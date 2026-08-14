"""Дороги как работа на ребре (D-107, D-158).

Проверяется то, ради чего дорога вообще введена:

* покрытие поднимается **на ступень** за полотно и время, а не кнопкой;
* уложенная дорога открывает обозу путь, которого до неё не было;
* без содержания дорога зарастает и возвращается в бездорожье;
* подсыпка стоит ровно в той доле, в какой дорога просела.
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


async def _ребро(
    session: AsyncSession, *, surface: Surface = Surface.TRAIL, полотна: float = 0
):
    метка = uuid.uuid4().hex[:8]
    здесь = await world.create_node(session, f"terra.rda.{метка}", "Здесь", area_m2=100)
    там = await world.create_node(session, f"terra.rdb.{метка}", "Там", area_m2=100)
    ребро = await travel.connect(
        session, здесь, там, base_seconds=600, surface=surface
    )
    identity = await world.create_identity(session, f"Дорожник-{метка}")
    body = await world.print_body(session, identity, здесь)
    if полотна:
        карман = await world.body_container(session, body)
        await world.grant_item(
            session, карман, road.SURFACE_GOODS, amount=полотна,
            origin="сценарий теста",
        )
    return здесь, там, body, ребро


async def _доделать(session: AsyncSession, job) -> None:
    """Прокрутить работу до конца — так же, как это сделал бы воркер."""
    from src.models.job import JobState

    await road.finished(session, job)
    job.state = JobState.DONE
    await session.flush()


# --- укладка ----------------------------------------------------------------


async def test_дорога_ложится_за_полотно_и_время(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    норма = constants[R.ROAD_SURFACE_PER_EDGE]
    _, _, body, ребро = await _ребро(session, полотна=норма)
    ушёл = datetime.now(UTC)

    job = await road.lay(session, constants, catalog, body, ребро, now=ушёл)

    assert ребро.surface is Surface.TRAIL, "до срока дорога не готова"
    assert job.run_at - ушёл == timedelta(hours=constants[R.ROAD_BUILD_HOURS])
    assert await road._surface_at_hand(session, body) == pytest.approx(0), (
        "полотно списывается вперёд, как материалы партии"
    )

    await _доделать(session, job)
    assert ребро.surface is Surface.ROAD
    assert float(ребро.condition) == pytest.approx(SCALE_MAX)


async def test_без_полотна_дорогу_не_кладут(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Дорога — это материалы, а не намерение."""
    норма = constants[R.ROAD_SURFACE_PER_EDGE]
    _, _, body, ребро = await _ребро(session, полотна=норма / 2)
    with pytest.raises(road.NoSurfaceGoods):
        await road.lay(session, constants, catalog, body, ребро)
    assert await road._surface_at_hand(session, body) == pytest.approx(норма / 2), (
        "отказ не съедает половину полотна"
    )


async def test_кладут_стоя_в_конце_ребра(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Дорогу кладут ногами: удалённой стройки в этом мире нет (D-044)."""
    норма = constants[R.ROAD_SURFACE_PER_EDGE]
    _, _, body, _ = await _ребро(session, полотна=норма)
    _, _, _, чужое = await _ребро(session)
    with pytest.raises(road.NotHere):
        await road.lay(session, constants, catalog, body, чужое)


async def test_ступени_идут_по_одной(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Бездорожье → дорога → тракт, и каждая ступень — отдельный проект."""
    норма = constants[R.ROAD_SURFACE_PER_EDGE]
    _, _, body, ребро = await _ребро(session, полотна=норма * 3)

    await _доделать(session, await road.lay(session, constants, catalog, body, ребро))
    assert ребро.surface is Surface.ROAD
    await _доделать(session, await road.lay(session, constants, catalog, body, ребро))
    assert ребро.surface is Surface.PAVED

    with pytest.raises(road.TopSurface):
        await road.lay(session, constants, catalog, body, ребро)


async def test_две_бригады_одну_дорогу_не_кладут(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    норма = constants[R.ROAD_SURFACE_PER_EDGE]
    _, _, body, ребро = await _ребро(session, полотна=норма * 2)
    await road.lay(session, constants, catalog, body, ребро)
    with pytest.raises(road.AlreadyWorking):
        await road.lay(session, constants, catalog, body, ребро)


# --- ради чего всё это (D-157) ----------------------------------------------


async def test_уложенная_дорога_открывает_путь_обозу(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Разведка растит карту, дорога делает её проезжей."""
    норма = constants[R.ROAD_SURFACE_PER_EDGE]
    здесь, там, body, ребро = await _ребро(session, полотна=норма)
    двор = await world.node_container(session, здесь)
    телега = await world.grant_item(
        session, двор, "Повозка", amount=1, origin="сценарий теста"
    )
    await transport.harness(session, constants, catalog, body, телега)

    with pytest.raises(transport.Impassable):
        await travel.depart(session, constants, body, там)

    await _доделать(session, await road.lay(session, constants, catalog, body, ребро))

    переход = await travel.depart(session, constants, body, там)
    assert переход is not None, "по уложенной дороге обоз идёт"


# --- зарастание -------------------------------------------------------------


async def test_без_содержания_дорога_зарастает(
    session: AsyncSession, constants: Constants
) -> None:
    """Заброшенная дорога возвращается в бездорожье — это сток, а не поломка."""
    _, _, _, ребро = await _ребро(session, surface=Surface.ROAD)
    шаг = constants[R.ROAD_DECAY_RATE]

    await road.decay(session, constants)
    assert float(ребро.condition) == pytest.approx(SCALE_MAX - шаг)

    #: Досрочно доводим до края: сто суток в тесте никто ждать не станет.
    ребро.condition = Decimal(str(шаг))
    await session.flush()
    заросло = await road.decay(session, constants)
    assert заросло == 1
    assert ребро.surface is Surface.TRAIL
    assert float(ребро.condition) == pytest.approx(SCALE_MAX), (
        "ступень ниже начинает со свежего состояния, а не с нуля"
    )


async def test_бездорожье_не_зарастает_дальше(
    session: AsyncSession, constants: Constants
) -> None:
    """Ниже тропы ступеней нет, и суточный проход её не трогает."""
    _, _, _, ребро = await _ребро(session, surface=Surface.TRAIL)
    было = float(ребро.condition)
    assert await road.decay(session, constants) == 0
    assert float(ребро.condition) == было


async def test_подсыпка_стоит_доли_укладки(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Провалившаяся наполовину требует половины: иначе содержать невыгодно."""
    норма = constants[R.ROAD_SURFACE_PER_EDGE]
    _, _, body, ребро = await _ребро(session, surface=Surface.ROAD, полотна=норма)
    ребро.condition = Decimal(str(SCALE_MAX / 2))
    await session.flush()

    assert road.needed(constants, ребро, mend=True) == pytest.approx(норма / 2)
    job = await road.lay(session, constants, catalog, body, ребро, mend=True)
    assert await road._surface_at_hand(session, body) == pytest.approx(норма / 2), (
        "подсыпка берёт половину, а не всё"
    )

    await _доделать(session, job)
    assert ребро.surface is Surface.ROAD, "подсыпка не поднимает ступень"
    assert float(ребро.condition) == pytest.approx(SCALE_MAX)


async def test_целой_дороге_подсыпать_нечего(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    норма = constants[R.ROAD_SURFACE_PER_EDGE]
    _, _, body, ребро = await _ребро(session, surface=Surface.ROAD, полотна=норма)
    with pytest.raises(road.RoadError):
        await road.lay(session, constants, catalog, body, ребро, mend=True)


# --- работа идёт офлайн -----------------------------------------------------


async def test_дорога_ложится_заданием_журнала(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Закрыл вкладку — дорога всё равно ляжет."""
    async with factory() as session, session.begin():
        _, _, body, ребро = await _ребро(
            session, полотна=constants[R.ROAD_SURFACE_PER_EDGE]
        )
        job = await road.lay(session, constants, catalog, body, ребро)
        срок, ребро_id = job.run_at, ребро.id

    assert await jobs.run_one(factory, now=срок - timedelta(minutes=1)) is None
    задание = await jobs.run_one(factory, now=срок)
    assert задание is not None and задание.kind == "road.work"

    async with factory() as session:
        ребро = await session.get(Edge, ребро_id)
        assert ребро.surface is Surface.ROAD


async def test_полотно_вообще_изготавливается(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """«Стройка» — работа на месте, а не станок (D-158).

    До этого решения всё семейство «стройки» — от полотна до мастерской — не
    изготавливалось вовсе: движок искал в узле предмет с таким именем.
    """
    from src.engine import craft

    рецепт = catalog.recipes.recipe(road.SURFACE_GOODS)
    assert рецепт.station == craft.SITE, "полотно собирают на месте"
    способ = craft.procedure(catalog, road.SURFACE_GOODS)
    assert способ.station is None, "станка для этого не нужно"
