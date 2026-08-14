"""Износ: почему вещи кончаются (D-129, D-058, 15-quality).

Столп П2 требует, чтобы предмет был конечен. Отсюда четыре потока износа, и
каждый параметризован вольтом отдельно: инструмент за сессию добычи, станок за
партию, снаряжение за сутки ношения, транспорт за переход.

## Два числа на предмете, и их путают чаще всего

| | Качество | Состояние |
|---|---|---|
| Что означает | каким предмет сделан | насколько изношен сейчас |
| Меняется | никогда | постоянно, от использования |

**Качество определяет, как быстро падает состояние.** Множитель срока службы
задан формулой `quality.durability_factor` — и она именно вычисляется, а не
переписывается кодом: иначе её числа переехали бы в движок (D-065).

**Состояние определяет, насколько предмет хорош сейчас.** Действующее качество
инструмента и станка — качество, взятое по доле оставшегося состояния. Без
этого износ был бы просто счётчиком до поломки, а «содержание обязательно»
осталось бы словами: разбитая наковальня обязана давать худший результат, а не
только внезапно ломаться.

**Дошло до нуля — вещь кончилась.** Не «работает с нулевой отдачей», а исчезает:
ориентир приёмки прямой — инструмент кончается за `100 / wear.tool_per_session`
сессий (07-implementation-map).

Среда ускоряет износ снаряжения множителем `wear.environment_k`. Это и делает
Пироксис дорогим сам по себе, без единой специальной механики (D-129).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.constants.catalog import ItemKind
from src.constants.spec import ConstantError
from src.engine import events
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Container, ContainerKind, Item
from src.models.world import Node

#: Имя планеты в `wear.environment_k` — ключи там человеческие, как в вольте.
PLANET_NAMES = {
    "terra": "Терра",
    "aquatica": "Акватика",
    "pyroxis": "Пироксис",
    "aurora": "Аврора",
}


def life_factor(constants: Constants, quality: float | None) -> float:
    """Во сколько раз дольше служит вещь такого качества.

    Формула берётся из вольта и вычисляется. Единица на входе означает «срок
    службы обычной вещи»: наружу идёт множитель, а не абсолютный срок.
    """
    scale = constants[R.QUALITY_SCALE]
    value = scale.mid if quality is None else quality
    factor = constants[R.QUALITY_DURABILITY_FACTOR].value(base_life=1, quality=value)
    if factor <= 0:  # pragma: no cover — защита от правки формулы в ноль
        raise ConstantError("quality.durability_factor даёт неположительный срок службы")
    return factor


def effective(constants: Constants, item: Item | None) -> float:
    """Действующее качество вещи: каким сделана, с поправкой на износ.

    Изношенная вещь работает хуже новой той же выделки — отсюда и смысл
    содержания. Целая вещь работает ровно на своё качество.
    """
    scale = constants[R.QUALITY_SCALE]
    if item is None:
        return scale.max
    quality = scale.max if item.quality is None else float(item.quality)
    return scale.clamp(quality * float(item.condition) / scale.max)


def spent_on(
    constants: Constants, item: Item | None, base: float, *, environment: float = 1.0
) -> float:
    """Сколько состояния съест такой износ у этой вещи.

    Хорошая вещь изнашивается медленнее ровно во столько раз, во сколько дольше
    служит, — второй формулы для этого не нужно.
    """
    if item is None:
        return 0.0
    return base * environment / life_factor(constants, _quality(item))


def wears_out(
    constants: Constants, item: Item | None, base: float, *, environment: float = 1.0
) -> bool:
    """Кончится ли вещь от такого износа — до того, как он списан.

    Нужно тем, кто обязан прибраться **перед** исчезновением вещи: обоз
    выгружает груз в узел раньше, чем повозки не станет (D-157). Считает та же
    формула, что и списывает: разойтись им нельзя.
    """
    if item is None:
        return False
    scale = constants[R.QUALITY_SCALE]
    return float(item.condition) - spent_on(
        constants, item, base, environment=environment
    ) <= scale.min


async def spend(
    session: AsyncSession,
    constants: Constants,
    item: Item | None,
    base: float,
    *,
    environment: float = 1.0,
    cause: str,
    actor_identity_id=None,
) -> bool:
    """Списать износ. Возвращает True, если вещь на этом кончилась."""
    if item is None:
        return False
    scale = constants[R.QUALITY_SCALE]
    spent = spent_on(constants, item, base, environment=environment)
    left = max(scale.min, float(item.condition) - spent)
    item.condition = Decimal(str(left))

    await events.record(
        session,
        EventKind.ITEM_WORN,
        actor_identity_id=actor_identity_id,
        item_id=str(item.id),
        type_key=item.type_key,
        spent=spent,
        condition=left,
        cause=cause,
    )
    if left > scale.min:
        await session.flush()
        return False

    await events.record(
        session,
        EventKind.ITEM_CONSUMED,
        actor_identity_id=actor_identity_id,
        item_id=str(item.id),
        type_key=item.type_key,
        cause=f"износ: {cause}",
    )
    await session.delete(item)
    await session.flush()
    return True


async def daily_gear_wear(
    session: AsyncSession, constants: Constants, catalog: Catalog
) -> int:
    """Суточный износ снаряжения на живых телах. Возвращает число кончившихся вещей.

    Снаряжение изнашивается от ношения, а не от применения (сток С2), и среда
    решает, насколько быстро: на Пироксисе вчетверо.
    """
    rows = (
        (
            await session.execute(
                select(Item, Node.planet, Body.identity_id)
                .join(Container, Container.id == Item.container_id)
                .join(Body, Body.id == Container.owner_id)
                .join(Node, Node.id == Body.node_id)
                .where(
                    Container.kind == ContainerKind.BODY,
                    Body.state == BodyState.ALIVE,
                )
            )
        )
        .all()
    )

    per_day = constants[R.WEAR_GEAR_PER_DAY]
    modifiers = constants[R.WEAR_ENVIRONMENT_K]
    gone = 0
    for item, planet, identity_id in rows:
        if not _is_gear(catalog, item.type_key):
            continue
        environment = modifiers.get(PLANET_NAMES.get(planet.value, ""), 1.0)
        if await spend(
            session,
            constants,
            item,
            per_day,
            environment=environment,
            cause="ношение",
            actor_identity_id=identity_id,
        ):
            gone += 1
    return gone


def _is_gear(catalog: Catalog, type_key: str) -> bool:
    """Снаряжение и тара носятся и изнашиваются; сырьё и еда — нет (D-090)."""
    try:
        return catalog.recipes.recipe(type_key).kind is ItemKind.GEAR
    except ConstantError:
        return False


def _quality(item: Item) -> float | None:
    return None if item.quality is None else float(item.quality)
