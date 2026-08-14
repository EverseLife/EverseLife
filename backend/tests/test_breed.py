"""Селекция: семена, сорта, скрещивание, вырождение (D-057, D-067).

Проверяется то, ради чего система введена: **преимущество опытного фермера без
навыков и уровней**.

* сеют семенами, а не урожаем: у партии есть сорт и своя сила;
* уборка оставляет своё семя долей `farm.harvest_seed_share`;
* отбор держит фонд, без отбора он вырождается, а гибрид ещё и расщепляется;
* скрещивание идёт полный цикл, стоит семян и требует питомника;
* слишком похожий сорт **не всходит** — гейт в биологии, а не в интерфейсе;
* сорт называет автор, и только когда тот стал постоянным.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

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


async def _ферма(session: AsyncSession, *, площадь: float = 100, питомник: bool = False):
    метка = uuid.uuid4().hex[:8]
    node = await world.create_node(
        session, f"terra.field.{метка}", "Поле", area_m2=площадь * 4,
        properties={"вода": "река", "плодородие": 60},
    )
    if питомник:
        двор = await world.node_container(session, node)
        await world.grant_item(session, двор, breed.NURSERY, quality=60, origin="тест")
    identity = await world.create_identity(session, f"Фермер-{метка}")
    body = await world.print_body(session, identity, node)
    node.owner_identity_id = identity.id
    await session.flush()
    return node, identity, body


async def _семена(
    session: AsyncSession, catalog: Catalog, body, сорт: Variety, сколько=500, сила=PERCENT
) -> Item:
    карман = await world.body_container(session, body)
    return await breed.seed_lot(session, catalog, карман.id, сорт, сколько, сила)


async def _до_уборки(
    session: AsyncSession, constants: Constants, catalog: Catalog, body, семена: Item,
    *, площадь: float = 100, уходов: int | None = None,
):
    """Разметить, вспахать, посеять и довести делянку до спелости."""
    plot = await farm.mark(session, constants, body, name="Делянка", area=площадь)
    plot.state = PlotState.PLOWED
    await session.flush()
    await farm.sow(session, constants, catalog, body, plot, семена)

    plant = catalog.plants.by_id(plot.culture_id)
    #: Уход руками теста: сам обход проверяется в тестах земледелия.
    plot.care_credits = int(plant.cycle_days) if уходов is None else уходов
    момент = datetime.now(UTC) + timedelta(
        hours=plant.cycle_days * constants[R.TIME_DAY_TERRA] + 1
    )
    return plot, момент


# --- семена -----------------------------------------------------------------


async def test_сеют_семенами_а_не_урожаем(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Семена — предмет: их покупают, крадут и теряют со смертью (D-057)."""
    _, _, body = await _ферма(session)
    сорт = await breed.landrace(session, catalog, SPELT)
    семена = await _семена(session, catalog, body, сорт)
    было = семена.amount

    plot = await farm.mark(session, constants, body, name="Делянка", area=50)
    plot.state = PlotState.PLOWED
    await session.flush()
    await farm.sow(session, constants, catalog, body, plot, семена)

    assert plot.variety_id == сорт.id, "сорт переехал на делянку"
    ушло = amount_float(было - семена.amount)
    assert ушло == pytest.approx(constants[R.FARM_SEED_RATE] * 50)


async def test_урожаем_не_посеешь(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Зерно — еда, а не посевной материал: у него нет сорта."""
    _, _, body = await _ферма(session)
    карман = await world.body_container(session, body)
    зерно = await world.grant_item(
        session, карман, "Зерно", amount=500, quality=50, origin="тест"
    )
    plot = await farm.mark(session, constants, body, name="Делянка", area=50)
    plot.state = PlotState.PLOWED
    await session.flush()

    with pytest.raises(breed.NotSeeds):
        await farm.sow(session, constants, catalog, body, plot, зерно)


async def test_уборка_оставляет_своё_семя(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Доля урожая уходит в фонд, а не на продажу (`farm.harvest_seed_share`)."""
    _, _, body = await _ферма(session)
    сорт = await breed.landrace(session, catalog, SPELT)
    семена = await _семена(session, catalog, body, сорт)
    plot, момент = await _до_уборки(session, constants, catalog, body, семена)

    собрано = await farm.harvest(
        session, constants, catalog, body, plot, select_seed=True, now=момент
    )
    plant = catalog.plants.by_id(SPELT)
    карман = await world.body_container(session, body)
    фонд = (
        await session.execute(
            select(Item).where(
                Item.container_id == карман.id, Item.type_key == plant.seed
            )
        )
    ).scalars().all()
    новое = sum(amount_float(и.amount) for и in фонд if и.id != семена.id)
    assert новое == pytest.approx(
        собрано * constants[R.FARM_HARVEST_SEED_SHARE] / PERCENT, rel=0.01
    )


# --- вырождение -------------------------------------------------------------


async def test_без_отбора_фонд_вырождается_а_с_отбором_держится(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Семенной фонд требует ухода: иначе культуру заводят раз и навсегда."""
    сорт = await breed.landrace(session, catalog, SPELT)
    падение = constants[R.BREED_DEGRADATION_PER_GEN]

    с_отбором = breed.next_vigor(constants, сорт, PERCENT, selected=True)
    без_отбора = breed.next_vigor(constants, сорт, PERCENT, selected=False)
    assert с_отбором == PERCENT
    assert без_отбора == pytest.approx(PERCENT + падение)
    assert падение < 0, "вольт задал потерю отрицательной — движок её складывает"


async def test_семена_гибрида_расщепляются_сильнее(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Гибрид хорош один раз: покупатель вернётся — в этом и бизнес (D-057)."""
    сорт = await breed.landrace(session, catalog, SPELT)
    гибрид = Variety(
        culture_id=SPELT, name=None, generation=1, stable=False, traits=сорт.traits
    )
    session.add(гибрид)
    await session.flush()

    у_сорта = breed.next_vigor(constants, сорт, PERCENT, selected=False)
    у_гибрида = breed.next_vigor(constants, гибрид, PERCENT, selected=False)
    assert у_гибрида < у_сорта
    assert у_гибрида == pytest.approx(
        PERCENT + constants[R.BREED_DEGRADATION_PER_GEN] + constants[R.BREED_HYBRID_DECAY]
    )


async def test_слабое_семя_даёт_меньше_урожая(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Сила партии — не украшение: она прямо в урожае."""
    _, _, полный = await _ферма(session)
    _, _, слабый = await _ферма(session)
    сорт = await breed.landrace(session, catalog, SPELT)

    много = await _семена(session, catalog, полный, сорт, сила=PERCENT)
    мало = await _семена(session, catalog, слабый, сорт, сила=PERCENT / 2)

    plot_а, момент_а = await _до_уборки(session, constants, catalog, полный, много)
    plot_б, момент_б = await _до_уборки(session, constants, catalog, слабый, мало)
    урожай_а = await farm.harvest(
        session, constants, catalog, полный, plot_а, now=момент_а
    )
    урожай_б = await farm.harvest(
        session, constants, catalog, слабый, plot_б, now=момент_б
    )
    assert урожай_б == pytest.approx(урожай_а / 2, rel=0.01)


# --- скрещивание ------------------------------------------------------------


async def test_скрещивание_идёт_цикл_и_требует_питомника(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Селекция — занятие на недели: результат приходит не сразу."""
    _, _, без_питомника = await _ферма(session)
    сорт = await breed.landrace(session, catalog, SPELT)
    a = await _семена(session, catalog, без_питомника, сорт)
    b = await _семена(session, catalog, без_питомника, сорт)
    with pytest.raises(breed.NoNursery):
        await breed.cross(session, constants, catalog, без_питомника, a, b)

    _, _, селекционер = await _ферма(session, питомник=True)
    один = await _семена(session, catalog, селекционер, сорт)
    другой = await _семена(session, catalog, селекционер, сорт)
    #: Момент задаётся явно: `started_at` ставит база, а её `now()` заморожен
    #: на транзакцию — сравнивать его с часами теста бессмысленно.
    начало = datetime.now(UTC)
    питомник = await breed.cross(
        session, constants, catalog, селекционер, один, другой, now=начало
    )

    plant = catalog.plants.by_id(SPELT)
    цикл = timedelta(hours=plant.cycle_days * constants[R.TIME_DAY_TERRA])
    assert питомник.ready_at == начало + цикл
    with pytest.raises(breed.BreedError):
        await breed.gather_cross(
            session, constants, catalog, селекционер, питомник,
            now=питомник.started_at,
        )


async def test_слишком_похожий_сорт_не_всходит(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Гейт встроен в биологию: селекционер получает пустую грядку (D-067)."""
    _, _, body = await _ферма(session, питомник=True)
    сорт = await breed.landrace(session, catalog, SPELT)
    a = await _семена(session, catalog, body, сорт)
    b = await _семена(session, catalog, body, сорт)

    #: Родители — один и тот же базовый сорт: потомок неотличим от него.
    питомник = await breed.cross(session, constants, catalog, body, a, b)
    вышло = await breed.gather_cross(
        session, constants, catalog, body, питомник,
        now=питомник.ready_at, rng=random.Random(1),
    )
    assert вышло is None, "неотличимое не прорастает"
    assert питомник.done and питомник.result_variety_id is None


async def test_разные_родители_дают_новый_сорт(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Признаки — среднее родителей с отклонением: формула вольта дословно."""
    _, identity, body = await _ферма(session, питомник=True)
    базовый = await breed.landrace(session, catalog, SPELT)
    #: Второй родитель заметно другой — такой в мире появляется отбором.
    другой = Variety(
        culture_id=SPELT,
        name="Скороспелка",
        generation=0,
        stable=True,
        traits={**базовый.traits, "yield_per_m2": базовый.traits["yield_per_m2"] * 2,
                "cycle_days": базовый.traits["cycle_days"] / 2},
    )
    session.add(другой)
    await session.flush()

    a = await _семена(session, catalog, body, базовый)
    b = await _семена(session, catalog, body, другой)
    питомник = await breed.cross(session, constants, catalog, body, a, b)
    гибрид = await breed.gather_cross(
        session, constants, catalog, body, питомник,
        now=питомник.ready_at, rng=random.Random(7),
    )

    assert гибрид is not None, "разные родители дают различимое потомство"
    assert гибрид.author_identity_id == identity.id
    assert not гибрид.stable, "первое поколение — гибрид, а не сорт"
    #: Каждый признак — между родительскими, с отклонением по вольту.
    отклонение = breed._drift_share(constants)  # noqa: SLF001
    for ключ in ("yield_per_m2", "cycle_days"):
        один, два = базовый.traits[ключ], другой.traits[ключ]
        середина = (один + два) / 2
        разброс = abs(один - два)
        assert abs(гибрид.traits[ключ] - середина) <= разброс * отклонение * 2 + 1e-6


async def test_имя_даётся_только_постоянному_сорту_и_только_автором(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Имя автора закрепляется навсегда — как клеймо мастера за изделием."""
    _, _, автор = await _ферма(session)
    _, _, чужой = await _ферма(session)
    базовый = await breed.landrace(session, catalog, SPELT)
    гибрид = Variety(
        culture_id=SPELT, name=None, generation=1, stable=False,
        traits=базовый.traits, author_identity_id=автор.identity_id,
    )
    session.add(гибрид)
    await session.flush()

    with pytest.raises(breed.NotStable):
        await breed.name_variety(session, автор, гибрид, "Тэрновка")

    #: Поколения отбора доводят гибрид до постоянства.
    порог = constants[R.BREED_GENERATIONS_TO_STABILIZE]
    for _ in range(int(порог.max)):
        await breed.select_generation(session, constants, гибрид)
    assert гибрид.stable

    with pytest.raises(breed.BreedError):
        await breed.name_variety(session, чужой, гибрид, "Чужовка")

    await breed.name_variety(session, автор, гибрид, "Тэрновка")
    assert гибрид.name == "Тэрновка"
