"""Еда: выносливость, сытость, разнообразие, порча (D-091, D-105, D-119, D-121).

Еда — самый массовый расходник игры: спрос равен населению. Правила собраны из
четырёх решений, и каждое даёт свою строку формулы.

## Откуда взялась каждая формула

**Питательность.** База — `body.food_restore`, множитель качества — линейно по
шкале от `food.restore_by_quality.min` (скверное) до `.max` (отличное). Дешёвая
еда кормит хуже, но кормит: бедность стоит времени, а не жизни (D-121).

**Сухое против горячего.** Сухое отдаёт восстановление целиком. Горячее — лишь
`cook.hot_restore_share` от него, зато даёт **сытость**: до
`cook.hot_duration × доля закрытых ролей` часов расход выносливости снижен на
`cook.hot_drain_reduction`. Горячее не добавляет запаса — оно замедляет расход:
это не бафф, а самое очевидное свойство обеда. Сытость требует качества не ниже
`cook.hot_quality_min` — машинная еда не даёт её никогда (D-121).

**Разнообразие.** Среди последних `food.variety_window` приёмов не меньше
`food.variety_min_kinds` разных видов — надбавка `body.diet_variety_bonus`
процентов к восстановлению. Считается по съеденному, а не по запасам, и вид
блюда — это сочетание, поэтому видов столько, сколько поваров (D-105).

**Порча.** Срок жизни еды: `spoilage.food_base` суток ÷ скорость порчи. У
урожая скорость — `spoilage_k` культуры, у готовых блюд —
`cook.spoilage_multiplier`. Протухшее не еда: съесть нельзя, суточный тик
подметает. Из порчи растёт всё остальное — оборот, спрос на соль и осада как
оружие — без единой новой механики.

## Что осталось честно недоделанным

Соль и холодный склад (`spoilage.salted_multiplier`, `cold_storage_multiplier`)
пока не замедляют порчу: солонина получает срок как обычная еда. Приедет вместе
с тарой и складами (04-items).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events, world
from src.models.event import EventKind
from src.models.food import Meal
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.units import PERCENT, amount


class FoodError(Exception):
    pass


class NotFood(FoodError):
    """Это не еда. Что съедобно — решают данные, а не движок (D-119)."""


class Spoiled(FoodError):
    """Испортилось. Порча — не ошибка, а свойство еды."""


def shelf_hours(constants: Constants, *, rate: float) -> float:
    """Срок жизни еды в часах: базовые сутки порчи, делённые на скорость."""
    if rate <= 0:
        return constants[R.SPOILAGE_FOOD_BASE] * constants[R.TIME_DAY_TERRA]
    return constants[R.SPOILAGE_FOOD_BASE] * constants[R.TIME_DAY_TERRA] / rate


def harvest_spoils_at(
    constants: Constants, spoilage_k: float, *, now: datetime
) -> datetime | None:
    """Когда испортится урожай. Культуры портятся со своей скоростью."""
    if spoilage_k <= 0:
        return None
    return now + timedelta(hours=shelf_hours(constants, rate=spoilage_k))


def cooked_spoils_at(constants: Constants, *, now: datetime) -> datetime:
    """Когда испортится готовое: в `cook.spoilage_multiplier` раз быстрее сырья."""
    return now + timedelta(
        hours=shelf_hours(constants, rate=constants[R.COOK_SPOILAGE_MULTIPLIER])
    )


def drain_multiplier(constants: Constants, body: Body, now: datetime) -> float:
    """Множитель расхода выносливости: сытый работает ровнее (D-119)."""
    if body.satiated_until is not None and now < body.satiated_until:
        return 1 - constants[R.COOK_HOT_DRAIN_REDUCTION] / PERCENT
    return 1.0


async def eat(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    *,
    now: datetime | None = None,
) -> float:
    """Съесть порцию. Возвращает, сколько выносливости вернулось.

    Еда работает и в дороге: сухарь в пути — ровно тот случай, ради которого
    сухое существует. Спит — не ест: рот занят сном.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise FoodError("мёртвые не едят")
    if body.sleeping_since is not None:
        raise FoodError("тело спит: сначала проснуться")

    pocket = await world.body_container(session, body)
    if item.container_id != pocket.id:
        raise FoodError("еда не в руках: едят своё и из рук")

    recipe = _recipe_of(catalog, item.type_key)
    if recipe is None or not recipe.food:
        raise NotFood(f"{item.type_key!r} не еда")
    if item.spoils_at is not None and moment >= item.spoils_at:
        #: Тухлое исчезает при попытке: его видно, и его больше нет.
        await session.delete(item)
        await session.flush()
        raise Spoiled(f"{item.type_key!r} испортилось")

    scale = constants[R.QUALITY_SCALE]
    quality = scale.mid if item.quality is None else float(item.quality)

    #: Питательность: линейно по качеству, от min к max множителя.
    span = constants[R.FOOD_RESTORE_BY_QUALITY]
    nutrition = span.min + (span.max - span.min) * quality / scale.max
    restore = constants[R.BODY_FOOD_RESTORE] * nutrition

    #: Горячее восстанавливает меньше сразу, но даёт сытость — если дотягивает
    #: до `cook.hot_quality_min`. Ниже порога это просто еда (D-121).
    satiated = False
    if recipe.hot:
        restore *= constants[R.COOK_HOT_RESTORE_SHARE] / PERCENT
        if quality >= constants[R.COOK_HOT_QUALITY_MIN]:
            filled = 1.0 if item.roles_filled is None else float(item.roles_filled)
            body.satiated_until = moment + timedelta(
                hours=constants[R.COOK_HOT_DURATION] * filled
            )
            satiated = True

    flavor = item.flavor or item.type_key
    session.add(Meal(identity_id=body.identity_id, flavor=flavor, at=moment))
    await session.flush()

    #: Разнообразие: считается по съеденному, включая этот приём (D-105).
    if await _varied(session, constants, body.identity_id):
        restore *= 1 + constants[R.BODY_DIET_VARIETY_BONUS] / PERCENT

    cap = constants[R.BODY_STAMINA_MAX]
    before = float(body.stamina)
    from decimal import Decimal

    body.stamina = Decimal(str(min(cap, before + restore)))

    one = amount(1)
    if item.amount > one:
        item.amount -= one
    else:
        await session.delete(item)
    await session.flush()

    await events.record(
        session,
        EventKind.MEAL_EATEN,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        flavor=flavor,
        quality=quality,
        restored=float(body.stamina) - before,
        satiated=satiated,
    )
    return float(body.stamina) - before


async def sweep_spoiled(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Подмести протухшее по всему миру. Зовётся суточным тиком.

    Порча — сток материи, разрешённый столпом П1: еда исчезает так же честно,
    как исчезает угар при плавке.
    """
    moment = now or datetime.now(UTC)
    rotten = (
        await session.execute(select(Item).where(Item.spoils_at <= moment))
    ).scalars().all()
    for item in rotten:
        await events.record(
            session,
            EventKind.ITEM_CONSUMED,
            item_id=str(item.id),
            type_key=item.type_key,
            cause="испортилось",
        )
        await session.delete(item)
    await session.flush()
    return len(rotten)


# --- внутреннее -------------------------------------------------------------


def _recipe_of(catalog: Catalog, type_key: str):
    try:
        return catalog.recipes.recipe(type_key)
    except Exception:  # noqa: BLE001 — сырьё рецептом не описано
        return None


async def _varied(
    session: AsyncSession, constants: Constants, identity_id
) -> bool:
    window = int(constants[R.FOOD_VARIETY_WINDOW])
    recent = (
        await session.execute(
            select(Meal.flavor)
            .where(Meal.identity_id == identity_id)
            .order_by(Meal.at.desc())
            .limit(window)
        )
    ).scalars().all()
    return len(set(recent)) >= int(constants[R.FOOD_VARIETY_MIN_KINDS])
