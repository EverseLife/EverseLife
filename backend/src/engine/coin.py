"""Монета: чеканка и переплавка (D-016, D-086).

Денег в мире две формы, и они устроены принципиально по-разному
(30-economy/01-currency):

* **Терракоин** — электронная запись на счёте. Невесом, мгновенен, существует
  только как чей-то долг. Живёт в двойной записи (`engine/ledger.py`);
* **Монета** — предмет. Лежит в кармане, гибнет вместе с телом, ходит там, где
  нет терминала.

Здесь — вторая. Монета намеренно не является счётом: она проходит по тем же
путям, что кирка и мешок зерна, потому что она такая же материя.

## Проба одна и решена вольтом

Проба монеты — `coin.default_fineness` (900‰), и **эмитент её не выбирает**:
состав монеты задан количествами рецепта — 0.9 аффинированного металла и 0.1
слитка железа лигатурой на монету. Механика занижения пробы убрана: разной
пробы в мире не существует, и монета всегда содержит то, что обещает.

**Клеймо — это `maker_identity_id`**, то самое поле, которым подписана всякая
вещь мастера (D-058): монета помнит чеканщика тем же способом, что кирка
помнит кузнеца.

**Переплавка возвращает аффинированный металл** — долей
`craft.recycle_return`, как всякая переработка. Лигатура — угар: выковыривать
десятую часть железа из сплава дороже самого железа.

## Чего здесь нет и почему

* **Курса ТК к монете движок не знает** и знать не должен: соотношение решает
  рынок игроков (D-016), а стакан для монеты — тот же самый, что для руды.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, ConstantError, Constants
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.engine import events, travel, wear
from src.engine.jobs import enqueue
from src.engine.world import body_container
from src.models.craft import BatchKind, CraftBatch
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Container, Item
from src.models.job import JobKind
from src.units import PERCENT, amount, amount_float

#: Станок из `build/recipes.json`. Чеканят только там, где он стоит.
MINT = "Монетный станок"


class CoinError(Exception):
    pass


class NotCoin(CoinError):
    """Это не монета. Монета — предмет вида `money` из `build/recipes.json`."""


def is_coin(catalog: Catalog, type_key: str) -> bool:
    """Монета ли это. Решают данные: вид рецепта `money` (D-090)."""
    try:
        return catalog.recipes.recipe(type_key).kind is ItemKind.MONEY
    except ConstantError:
        #: Сырьё рецептом не описано — деньгами оно тем более не бывает.
        return False


def fineness_of(constants: Constants) -> float:
    """Проба монеты, ‰. Одна на весь мир: механики занижения нет."""
    return constants[R.COIN_DEFAULT_FINENESS]


def per_coin(catalog: Catalog, coin: str) -> dict[str, float]:
    """Состав монеты: имя входа → сколько на одну монету.

    Берётся из количеств рецепта (0.9 аффинажа + 0.1 железа), а не из констант:
    состав — это и есть рецепт, второй таблицы быть не должно (D-065).
    """
    recipe = catalog.recipes.recipe(coin)
    if recipe.kind is not ItemKind.MONEY:
        raise NotCoin(f"{recipe.name!r} — не монета")
    if not recipe.amounts:
        raise NotCoin(f"у {recipe.name!r} не задан состав: чеканить не из чего")
    return {
        catalog.recipes.resolve(name): value for name, value in recipe.amounts.items()
    }


def metal_of(catalog: Catalog, coin: str) -> str:
    """Аффинированный металл монеты — первый вход рецепта. Он же возвращается
    переплавкой; лигатура (железо) — угар."""
    recipe = catalog.recipes.recipe(coin)
    if recipe.kind is not ItemKind.MONEY:
        raise NotCoin(f"{recipe.name!r} — не монета")
    if not recipe.inputs:
        raise NotCoin(f"у {recipe.name!r} нет входа: чеканить не из чего")
    return catalog.recipes.resolve(recipe.inputs[0])


def melt_return(constants: Constants, catalog: Catalog, coin: str, count: float) -> float:
    """Сколько аффинированного металла вернёт переплавка.

    Доля металла в монете — из рецепта, угар — общий для всякой переработки
    `craft.recycle_return`: своего числа для монеты вольт не задаёт (D-065).
    """
    состав = per_coin(catalog, coin)
    металл = metal_of(catalog, coin)
    share = constants[R.CRAFT_RECYCLE_RETURN] / PERCENT
    return count * состав.get(металл, 0.0) * share


async def mint(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    coin: str,
    count: float,
    *,
    now: datetime | None = None,
) -> CraftBatch:
    """Отчеканить партию монет.

    Присутственное и длительное, как всякая работа у станка: монетный станок
    стоит в узле, металл и лигатура списываются сразу, монеты приходят по
    сроку заданием журнала. Проба всегда `coin.default_fineness` — выбора нет.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise CoinError("мёртвое тело не чеканит")
    await travel.require_here(session, body)

    #: Импорт внутри: `craft` знает про монету только через этот модуль, а не
    #: наоборот, — иначе вышел бы круг.
    from src.engine import craft

    recipe = catalog.recipes.recipe(coin)
    if recipe.kind is not ItemKind.MONEY:
        raise NotCoin(f"{recipe.name!r} — не монета: её делают партией, не чеканкой")
    if not await craft._knows(session, body, recipe.name):  # noqa: SLF001
        raise craft.NotLearned(f"рецепт {recipe.name!r} не скопирован в личность")

    if count <= 0 or count != int(count):
        raise CoinError("монеты считаются целыми штуками")
    if count > constants[R.CRAFT_BATCH_MAX]:
        raise craft.TooBig(f"партия больше craft.batch_max: {count}")

    состав = per_coin(catalog, coin)
    proc = craft.Procedure(
        output=recipe.name,
        station=catalog.recipes.resolve(recipe.station) if recipe.station else None,
        tools=(),
        inputs=tuple(состав),
        per_unit=состав,
        step_hours=craft.step_hours(catalog, recipe),
        mix=False,
        needs_recipe=True,
    )
    station = await craft._station_item(session, body, proc)  # noqa: SLF001

    нужно = {имя: сколько * count for имя, сколько in состав.items()}
    карман = await body_container(session, body)
    stock = await craft._stock(session, карман, tuple(нужно))  # noqa: SLF001
    picks = craft._pick(stock, нужно)  # noqa: SLF001

    scale = constants[R.QUALITY_SCALE]
    #: Качество металла монете не передаётся: её описывает проба. Число живёт
    #: в партии лишь потому, что поле общее для всех работ у станка.
    качество_металла = craft._material_quality(picks, scale.max)  # noqa: SLF001
    for pick in picks:
        if pick.item.amount > pick.take:
            pick.item.amount -= pick.take
        else:
            await session.delete(pick.item)
    await session.flush()

    minutes = craft.batch_minutes(
        constants, proc, count, wear.effective(constants, station)
    )
    проба = fineness_of(constants)
    batch = CraftBatch(
        body_id=body.id,
        node_id=body.node_id,
        output=recipe.name,
        units=amount(count),
        station_item_id=None if station is None else station.id,
        quality=Decimal(str(качество_металла)),
        spread=Decimal(str(scale.min)),
        spent=нужно,
        fineness=Decimal(str(проба)),
        ready_at=moment + timedelta(minutes=minutes),
    )
    session.add(batch)
    await session.flush()
    #: Станок занят чеканкой, как всякой работой (D-150): без этого две партии
    #: шли бы на одном станке одновременно.
    await craft._occupy(session, station, body, batch.ready_at)  # noqa: SLF001

    event = await events.record(
        session,
        EventKind.CRAFT_STARTED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        batch_id=str(batch.id),
        work="mint",
        output=recipe.name,
        units=count,
        fineness=проба,
        spent=нужно,
    )
    await enqueue(
        session,
        JobKind.CRAFT_BATCH,
        batch.ready_at,
        payload={"batch": str(batch.id)},
        dedup_key=f"craft.batch:{batch.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    return batch


async def melt(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    count: float,
    *,
    now: datetime | None = None,
) -> CraftBatch:
    """Переплавить монеты обратно в аффинированный металл.

    Отдельная работа, а не общая переработка: монеты лежат стопкой, и
    разбирать надо часть, а не всю.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise CoinError("мёртвое тело не работает")
    await travel.require_here(session, body)

    from src.engine import craft

    if not is_coin(catalog, item.type_key):
        raise NotCoin(f"{item.type_key!r} — не монета: это переработка, а не переплавка")

    карман = await body_container(session, body)
    if item.container_id != карман.id:
        raise CoinError("монета не в руках: плавят своё")
    сколько = amount(count)
    if сколько <= 0 or сколько > item.amount:
        raise CoinError(f"столько монет нет: в стопке {amount_float(item.amount)}")

    станок = catalog.recipes.recipe(item.type_key).station
    proc = craft.Procedure(
        output=item.type_key,
        station=catalog.recipes.resolve(станок) if станок else None,
        tools=(),
        inputs=(),
        per_unit={},
        step_hours=craft.step_hours(catalog, catalog.recipes.recipe(item.type_key)),
        mix=False,
        needs_recipe=False,
    )
    #: Плавят там же, где чеканят: разбирают и чинят у того станка, где делают.
    station = await craft._station_item(session, body, proc)  # noqa: SLF001

    проба = fineness_of(constants) if item.fineness is None else float(item.fineness)
    if item.amount > сколько:
        item.amount -= сколько
    else:
        await session.delete(item)
    await session.flush()

    minutes = craft.batch_minutes(
        constants, proc, count, wear.effective(constants, station)
    )
    scale = constants[R.QUALITY_SCALE]
    batch = CraftBatch(
        body_id=body.id,
        node_id=body.node_id,
        kind=BatchKind.RECYCLE,
        output=item.type_key,
        units=amount(count),
        station_item_id=None if station is None else station.id,
        quality=Decimal(str(scale.min)),
        spread=Decimal(str(scale.min)),
        spent={item.type_key: count},
        fineness=Decimal(str(проба)),
        ready_at=moment + timedelta(minutes=minutes),
    )
    session.add(batch)
    await session.flush()
    await craft._occupy(session, station, body, batch.ready_at)  # noqa: SLF001

    event = await events.record(
        session,
        EventKind.CRAFT_STARTED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        batch_id=str(batch.id),
        work="melt",
        output=item.type_key,
        units=count,
        fineness=проба,
    )
    await enqueue(
        session,
        JobKind.CRAFT_BATCH,
        batch.ready_at,
        payload={"batch": str(batch.id)},
        dedup_key=f"craft.batch:{batch.id}",
        cause_event_id=event.id,
        body_id=body.id,
    )
    return batch


async def finish_melt(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    batch: CraftBatch,
    where: Container,
) -> list[float]:
    """Переплавка окончена: вернулся аффинированный металл, лигатура — угар."""
    металл = metal_of(catalog, batch.output)
    вернулось = melt_return(constants, catalog, batch.output, amount_float(batch.units))
    if вернулось <= 0:  # pragma: no cover — партия из нуля не запускается
        return []

    #: Качество металла из монеты неизвестно: монета его не помнит, а вольт
    #: качества металлу в монете не назначает. Берём середину шкалы — то же,
    #: что движок делает со всяким сырьём без истории.
    scale = constants[R.QUALITY_SCALE]
    session.add(
        Item(
            container_id=where.id,
            type_key=металл,
            amount=amount(вернулось),
            quality=Decimal(str(scale.mid)),
        )
    )
    await events.record(
        session,
        EventKind.ITEM_CONSUMED,
        type_key=batch.output,
        cause="переплавка монеты",
        units=amount_float(batch.units),
        returned=вернулось,
    )
    await session.flush()
    return [scale.mid]
