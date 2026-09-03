# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""Food: stamina, satiety, variety, spoilage (D-091, D-105, D-119, D-121).

Food is the game's most massive consumable: demand equals population. The
rules are assembled from four decisions, and each gives its own formula line.

## Where each formula came from

**Nutrition.** The base is `body.food_restore`, the quality multiplier is
linear along the scale from `food.restore_by_quality.min` (poor) to `.max`
(excellent). Cheap food feeds worse, but feeds: poverty costs time, not life (D-121).

**Dry versus hot.** Dry gives the whole restoration. Hot -- only
`cook.hot_restore_share` of it, but gives **satiety**: for
`cook.hot_duration * share of filled roles` hours the stamina spend is reduced
by `cook.hot_drain_reduction`. Hot adds no reserve -- it slows the spend: not
a buff but the most obvious property of a meal. Satiety requires quality not
below `cook.hot_quality_min` -- machine food never gives it (D-121).

**Variety.** Among the last `food.variety_window` meals at least
`food.variety_min_kinds` different kinds -- a bonus of `body.diet_variety_bonus`
percent to restoration. Counted by what was eaten, not by stocks, and a dish's
kind is the combination, so there are as many kinds as cooks (D-105).

**Spoilage.** Food shelf life: `spoilage.food_base` days / spoilage speed. For
harvest the speed is the crop's `spoilage_k`, for cooked dishes
`cook.spoilage_multiplier`. Rotten is not food: it cannot be eaten, the daily
tick sweeps it. Everything else grows out of spoilage -- turnover, demand for
salt and siege as a weapon -- without a single new mechanic.

## What remains honestly unfinished

Salt and cold storage (`spoilage.salted_multiplier`, `cold_storage_multiplier`)
do not slow spoilage yet: salted meat gets a term like ordinary food. Arrives
together with containers and warehouses (04-items).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import events, world
from src.engine.errors import Refusal
from src.models.event import EventKind
from src.models.food import Meal
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.units import PERCENT, ROUND_STAMINA, amount, on_grid


class FoodError(Refusal):
    pass


class NotFood(FoodError):
    """Not food. What is edible is decided by data, not the engine (D-119)."""


class Spoiled(FoodError):
    """Spoiled. Spoilage is not an error but a property of food."""


def shelf_hours(constants: Constants, *, rate: float) -> float:
    """Food shelf life in hours: the base spoilage day divided by the speed."""
    if rate <= 0:
        return constants[R.SPOILAGE_FOOD_BASE] * constants[R.TIME_DAY_TERRA]
    return constants[R.SPOILAGE_FOOD_BASE] * constants[R.TIME_DAY_TERRA] / rate


def harvest_spoils_at(constants: Constants, spoilage_k: float, *, now: datetime) -> datetime | None:
    """When the harvest spoils. Crops spoil at their own speed."""
    if spoilage_k <= 0:
        return None
    return now + timedelta(hours=shelf_hours(constants, rate=spoilage_k))


def cooked_spoils_at(constants: Constants, *, now: datetime) -> datetime:
    """When cooked food spoils: `cook.spoilage_multiplier` times faster than raw."""
    return now + timedelta(hours=shelf_hours(constants, rate=constants[R.COOK_SPOILAGE_MULTIPLIER]))


def drain_multiplier(constants: Constants, body: Body, now: datetime) -> float:
    """Stamina spend multiplier: the fed work steadier (D-119)."""
    if body.satiated_until is not None and now < body.satiated_until:
        return 1 - constants[R.COOK_HOT_DRAIN_REDUCTION] / PERCENT
    return 1.0


async def _lock(session: AsyncSession, body: Body) -> Body:
    """The body's row, locked for this transaction.

    Stamina is a quantity of the body and the meal is a read-modify-write of
    it, so two sockets of one identity eating in the same second would each
    read the same figure and the second write would swallow the first
    (CLAUDE.md, review 2026-08-23).
    """
    return (
        (
            await session.execute(
                select(Body)
                .where(Body.id == body.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .one()
    )


async def _portion(session: AsyncSession, item: Item) -> Item:
    """The stack's row, locked and reread.

    Gone between the read and the lock -- eaten to the last portion by another
    socket, sold, carried off, spoiled, burned in an eruption: the refusal is
    the same either way, because the answer to the only question `eat` asks is.
    """
    fresh = (
        (
            await session.execute(
                select(Item)
                .where(Item.id == item.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .one_or_none()
    )
    if fresh is None:
        raise FoodError(key="food-not-in-hands")
    return fresh


async def eat(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    *,
    now: datetime | None = None,
) -> float:
    """Eat a portion. Returns how much stamina came back.

    Food works on the road too: hardtack en route is exactly the case dry food
    exists for. Asleep -- not eating: the mouth is busy sleeping.
    """
    moment = now or datetime.now(UTC)
    body = await _lock(session, body)
    #: And the portion's own row, in the order the rest of the engine takes
    #: them: body, then stack. Locking the body is not enough for the stack --
    #: it was read before the lock, so the count this session holds can already
    #: be a meal out of date, and `item.amount -= one` would write that stale
    #: count back and hand the second eater their portion free.
    item = await _portion(session, item)

    #: Every check below the locks, and none above them. A check made on a row
    #: this session read a moment ago is a check on what was true then: the
    #: loaf can be sold, moved or burned between the reading and the lock, and
    #: eating it would then take a portion out of somebody else's container and
    #: pay for it in this body's strength. The lock is worth nothing if the
    #: question it protects was already answered.
    if body.state is not BodyState.ALIVE:
        raise FoodError(key="food-dead-eats")
    if body.sleeping_since is not None:
        raise FoodError(key="food-asleep")

    pocket = await world.body_container(session, body)
    if item.container_id != pocket.id:
        raise FoodError(key="food-not-in-hands")

    recipe = _recipe_of(catalog, item.type_key)
    if recipe is None or not recipe.food:
        raise NotFood(key="food-not-food", goods=item.type_key)
    if item.spoils_at is not None and moment >= item.spoils_at:
        #: Rotten disappears on the attempt: it is seen, and it is gone.
        await session.delete(item)
        await session.flush()
        raise Spoiled(key="food-spoiled", goods=item.type_key)

    scale = constants[R.QUALITY_SCALE]
    quality = scale.mid if item.quality is None else float(item.quality)

    #: Nutrition: linear by quality, from min to max of the multiplier.
    span = constants[R.FOOD_RESTORE_BY_QUALITY]
    nutrition = span.min + (span.max - span.min) * quality / scale.max
    restore = constants[R.BODY_FOOD_RESTORE] * nutrition

    #: Hot restores less at once but gives satiety -- if it reaches
    #: `cook.hot_quality_min`. Below the threshold it is just food (D-121).
    satiated = False
    if recipe.hot:
        restore *= constants[R.COOK_HOT_RESTORE_SHARE] / PERCENT
        if quality >= constants[R.COOK_HOT_QUALITY_MIN]:
            filled = 1.0 if item.roles_filled is None else float(item.roles_filled)
            body.satiated_until = moment + timedelta(hours=constants[R.COOK_HOT_DURATION] * filled)
            satiated = True

    flavor = item.flavor or item.type_key
    session.add(Meal(identity_id=body.identity_id, flavor=flavor, at=moment))
    await session.flush()

    #: Variety: counted by what was eaten, including this meal (D-105).
    if await _varied(session, constants, body.identity_id):
        restore *= 1 + constants[R.BODY_DIET_VARIETY_BONUS] / PERCENT

    #: The ceiling on the column's grid, the same one sleep fills to: the raw
    #: maximum, rounded the column's way, would put a fed body above it.
    roof = world.stamina_roof(constants)
    before = float(body.stamina)
    #: On the grid here rather than left to the column, so the answer this
    #: command returns and the event it writes are the number the row took.
    #: Left to the column they differed by up to half a hundredth, and `look`
    #: a moment later contradicted the reply the player had just been given.
    #:
    #: To the nearest, and not down: a meal is one item and there is no loop,
    #: so there is nothing to carry -- and flooring every meal would shave a
    #: hundredth off each with nowhere to keep it, which is how an error that
    #: cancels becomes one that always takes. (Not quite Postgres's own
    #: rounding: it goes half away from zero, this half to even. They differ
    #: only on an exact half of a hundredth, against a meal worth twenty.)
    #:
    #: Rounding to the nearest cannot lift a body over the ceiling `stamina_roof`
    #: sets, which is what that floor is there to prevent: the roof is already
    #: on this grid, and the cap is taken before the rounding, so the most the
    #: nearest can do is reach it.
    body.stamina = on_grid(min(roof, before + restore), ROUND_STAMINA)

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
    """Sweep the rotten across the whole world. Called by the daily tick.

    Spoilage is a matter sink allowed by pillar P1: food disappears as honestly
    as loss in smelting.
    """
    moment = now or datetime.now(UTC)
    rotten = (await session.execute(select(Item).where(Item.spoils_at <= moment))).scalars().all()
    for item in rotten:
        await events.record(
            session,
            EventKind.ITEM_CONSUMED,
            item_id=str(item.id),
            type_key=item.type_key,
            cause="spoiled",
        )
        await session.delete(item)
    await session.flush()
    return len(rotten)


# --- internal ----------------------------------------------------------------


def _recipe_of(catalog: Catalog, type_key: str):
    try:
        return catalog.recipes.recipe(type_key)
    except Exception:  # noqa: BLE001 -- raw material has no recipe
        return None


async def _varied(session: AsyncSession, constants: Constants, identity_id) -> bool:
    window = int(constants[R.FOOD_VARIETY_WINDOW])
    recent = (
        (
            await session.execute(
                select(Meal.flavor)
                .where(Meal.identity_id == identity_id)
                .order_by(Meal.at.desc())
                .limit(window)
            )
        )
        .scalars()
        .all()
    )
    return len(set(recent)) >= int(constants[R.FOOD_VARIETY_MIN_KINDS])
