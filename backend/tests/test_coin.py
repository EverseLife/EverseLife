"""Монета: чеканка и переплавка (D-016, D-086).

Проверяется то, ради чего монета вообще введена отдельно от счёта:

* монета — предмет, а не запись: у неё клеймо, проба и место в кармане;
* проба одна на весь мир — `coin.default_fineness` (900‰), выбора у эмитента
  нет: состав задан количествами рецепта — 0.9 аффинажа и 0.1 железа;
* партия доходит до кошелька через журнал заданий — монеты не пропадают;
* переплавка возвращает аффинированный металл за вычетом угара, лигатура
  теряется;
* чеканят только у монетного станка и только своим металлом;
* монета не ходит общей дверью крафта: у неё своя.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import coin, craft, jobs, world
from src.models.craft import CraftBatch
from src.models.inventory import Item
from src.models.job import Job
from src.units import PERCENT, amount_float

GOLD = "Золотая монета"
GOLD_METAL = "Аффинированное золото"
IRON = "Слиток железа"


async def _двор(session: AsyncSession, *, металла: float = 100, железа: float = 100):
    метка = uuid.uuid4().hex[:8]
    node = await world.create_node(session, f"terra.mint.{метка}", "Двор", area_m2=100)
    двор = await world.node_container(session, node)
    await world.grant_item(session, двор, coin.MINT, quality=60, origin="тест")
    identity = await world.create_identity(session, f"Чеканщик-{метка}")
    body = await world.print_body(session, identity, node)
    карман = await world.body_container(session, body)
    if металла:
        await world.grant_item(
            session, карман, GOLD_METAL, amount=металла, quality=60, origin="тест"
        )
    if железа:
        await world.grant_item(
            session, карман, IRON, amount=железа, quality=55, origin="тест"
        )
    await world.learn(session, identity, GOLD)
    return node, identity, body


async def _довести(session: AsyncSession, batch: CraftBatch) -> None:
    """Досрочно завершить **эту** партию руками теста — как сделал бы воркер."""
    job = (
        await session.execute(
            select(Job).where(Job.dedup_key == f"craft.batch:{batch.id}")
        )
    ).scalar_one()
    job.run_at = datetime.now(UTC)
    await craft.finish(session, job)


async def _монеты(session: AsyncSession, body) -> list[Item]:
    карман = await world.body_container(session, body)
    rows = await session.execute(
        select(Item).where(Item.container_id == карман.id, Item.type_key == GOLD)
    )
    return list(rows.scalars().all())


# --- чеканка ----------------------------------------------------------------


async def test_чеканка_тратит_состав_рецепта(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """0.9 аффинажа и 0.1 железа на монету — из количеств рецепта, не из головы."""
    _, _, body = await _двор(session)
    batch = await coin.mint(session, constants, catalog, body, GOLD, 10)

    состав = coin.per_coin(catalog, GOLD)
    assert состав == {GOLD_METAL: pytest.approx(0.9), IRON: pytest.approx(0.1)}
    assert batch.spent[GOLD_METAL] == pytest.approx(10 * состав[GOLD_METAL])
    assert batch.spent[IRON] == pytest.approx(10 * состав[IRON])
    #: Проба не выбирается: она одна на весь мир.
    assert float(batch.fineness) == constants[R.COIN_DEFAULT_FINENESS]


async def test_монета_приходит_с_клеймом_и_пробой_а_качества_у_неё_нет(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Монету описывает содержание металла, а не шкала качества."""
    _, identity, body = await _двор(session)
    batch = await coin.mint(session, constants, catalog, body, GOLD, 5)
    await _довести(session, batch)

    (стопка,) = await _монеты(session, body)
    assert amount_float(стопка.amount) == 5
    assert float(стопка.fineness) == constants[R.COIN_DEFAULT_FINENESS]
    assert стопка.quality is None, "у монеты нет качества: есть проба"
    assert стопка.maker_identity_id == identity.id, "клеймо эмитента"


async def test_монеты_не_пропадают_на_пути_через_журнал(
    factory: async_sessionmaker[AsyncSession], constants: Constants, catalog: Catalog
) -> None:
    """Регресс на «при чеканке монеты пропадают»: полный путь через воркер.

    Металл списан при запуске, монеты приходят выполнением задания — тем же
    кодом, каким их довёл бы настоящий воркер, включая повтор задания: второй
    прогон не создаёт вторых монет и не съедает первых.
    """
    async with factory() as session, session.begin():
        _, _, body = await _двор(session)
        batch = await coin.mint(session, constants, catalog, body, GOLD, 7)
        batch_id, body_id, срок = batch.id, body.id, batch.ready_at

    выполнено = await jobs.run_one(factory, now=срок)
    assert выполнено is not None and выполнено.last_error is None

    #: Повтор того же задания — вторых монет не даёт.
    assert await jobs.run_one(factory, now=срок) is None

    async with factory() as session:
        from src.models.identity import Body

        body = await session.get(Body, body_id)
        монеты = await _монеты(session, body)
        assert sum(amount_float(m.amount) for m in монеты) == 7
        batch = await session.get(CraftBatch, batch_id)
        assert batch.state.value == "done"


async def test_станок_занят_чеканкой(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Чеканка — работа у станка: вторая партия на том же станке не идёт (D-150)."""
    _, _, body = await _двор(session)
    await coin.mint(session, constants, catalog, body, GOLD, 2)
    with pytest.raises(craft.Busy):
        await coin.mint(session, constants, catalog, body, GOLD, 2)


async def test_без_монетного_станка_не_чеканят(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Станок в узле — то же условие, что у всякого крафта."""
    метка = uuid.uuid4().hex[:6]
    node = await world.create_node(session, f"terra.bare.{метка}", "Голо", area_m2=50)
    identity = await world.create_identity(session, f"Босой-{метка}")
    body = await world.print_body(session, identity, node)
    карман = await world.body_container(session, body)
    await world.grant_item(session, карман, GOLD_METAL, amount=50, quality=60, origin="тест")
    await world.grant_item(session, карман, IRON, amount=50, quality=60, origin="тест")
    await world.learn(session, identity, GOLD)

    with pytest.raises(craft.NoStation):
        await coin.mint(session, constants, catalog, body, GOLD, 1)


async def test_без_металла_не_чеканят(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Материя не создаётся: нет золота — нет и монеты (И1)."""
    _, _, body = await _двор(session, металла=1)
    with pytest.raises(craft.NotEnough):
        await coin.mint(session, constants, catalog, body, GOLD, 5)


async def test_без_железа_не_чеканят(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Лигатура — такой же вход: без 0.1 железа на монету партия не начнётся."""
    _, _, body = await _двор(session, железа=0)
    with pytest.raises(craft.NotEnough):
        await coin.mint(session, constants, catalog, body, GOLD, 5)


async def test_дробной_монеты_не_бывает(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _двор(session)
    with pytest.raises(coin.CoinError):
        await coin.mint(session, constants, catalog, body, GOLD, 2.5)


# --- переплавка -------------------------------------------------------------


async def test_переплавка_возвращает_аффинаж_минус_угар(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Возврат — доля `craft.recycle_return` от 0.9 на монету; железо — угар."""
    _, _, body = await _двор(session, металла=100)
    batch = await coin.mint(session, constants, catalog, body, GOLD, 4)
    await _довести(session, batch)
    (стопка,) = await _монеты(session, body)

    было = await _в_кармане(session, body, GOLD_METAL)
    плавка = await coin.melt(session, constants, catalog, body, стопка, 4)
    await _довести(session, плавка)

    стало = await _в_кармане(session, body, GOLD_METAL)
    доля = constants[R.CRAFT_RECYCLE_RETURN] / PERCENT
    assert стало - было == pytest.approx(4 * 0.9 * доля, abs=0.01)


async def test_плавят_только_часть_стопки(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Монеты лежат стопкой, и переплавка одной не уничтожает остальные."""
    _, _, body = await _двор(session)
    batch = await coin.mint(session, constants, catalog, body, GOLD, 6)
    await _довести(session, batch)
    (стопка,) = await _монеты(session, body)

    await coin.melt(session, constants, catalog, body, стопка, 2)
    осталось = sum(amount_float(item.amount) for item in await _монеты(session, body))
    assert осталось == 4


async def test_нельзя_переплавить_больше_чем_есть(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    _, _, body = await _двор(session)
    batch = await coin.mint(session, constants, catalog, body, GOLD, 2)
    await _довести(session, batch)
    (стопка,) = await _монеты(session, body)

    with pytest.raises(coin.CoinError):
        await coin.melt(session, constants, catalog, body, стопка, 3)


# --- одна дверь -------------------------------------------------------------


async def test_монету_не_делают_обычной_партией(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Иначе в мире было бы два способа чеканить."""
    _, _, body = await _двор(session)
    with pytest.raises(craft.Unmakeable):
        await craft.plan(session, constants, catalog, body, GOLD, 1)


async def test_монету_не_разбирают_обычной_переработкой(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> None:
    """Общая переработка вернула бы металл по норме рецепта, не по составу."""
    _, _, body = await _двор(session)
    batch = await coin.mint(session, constants, catalog, body, GOLD, 3)
    await _довести(session, batch)
    (стопка,) = await _монеты(session, body)

    with pytest.raises(craft.Unmakeable):
        await craft.recycle(session, constants, catalog, body, стопка)


async def _в_кармане(session: AsyncSession, body, type_key: str) -> float:
    карман = await world.body_container(session, body)
    rows = (
        await session.execute(
            select(Item).where(
                Item.container_id == карман.id, Item.type_key == type_key
            )
        )
    ).scalars().all()
    return sum(amount_float(item.amount) for item in rows)
