# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The batch at the bench: planned, started, cooked, repaired or recycled --
the work while the master stands at it.
"""

from __future__ import annotations

import uuid
from bisect import bisect_left
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants
from src.constants import registry as R
from src.engine import occupation, travel, wear
from src.engine.craft import power
from src.engine.craft._base import (
    BENCHLESS,
    CraftError,
    NotEnough,
    NotIngredient,
    NotLearned,
    Plan,
    Procedure,
    Unmakeable,
)
from src.engine.craft._internal import (
    _knows,
    _material_quality,
    _num,
    _pick,
    _prepare,
    _seconds,
    _station_item,
    _stock,
    _tiers_by,
    _tool_items,
    demand,
)
from src.engine.craft.method_of_making import batch_minutes, procedure, step_hours
from src.engine.craft.queue import _launch
from src.engine.world import body_container
from src.models.craft import BatchKind, CraftBatch
from src.models.identity import Body, BodyState
from src.models.inventory import Item
from src.units import (
    MINUTES_PER_HOUR,
    PERCENT,
    amount,
    amount_float,
)


async def plan(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    output: str,
    units: float,
    *,
    tool_item_id: uuid.UUID | None = None,
    proportions: dict[str, float] | None = None,
    way: str | None = None,
    recipe_key: str | None = None,
    tiers: dict[str, str] | None = None,
) -> Plan:
    """Forecast before a batch. Changes nothing and reserves nothing."""
    ready = await _prepare(
        session,
        constants,
        catalog,
        body,
        output,
        units,
        tool_item_id=tool_item_id,
        proportions=proportions,
        way=way,
        recipe_key=recipe_key,
        tiers=tiers,
    )
    return ready.plan


async def most(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    output: str,
    *,
    tool_item_id: uuid.UUID | None = None,
    proportions: dict[str, float] | None = None,
    way: str | None = None,
    recipe_key: str | None = None,
    tiers: dict[str, str] | None = None,
) -> float:
    """The largest batch these hands can pay for right now, in whole units.

    **Why the engine is asked at all** (D-225). What feeds a batch is not the
    stacks on the screen: a tier chosen for an input narrows them to that
    tier alone (D-058), water for the dough is counted inside the canister it
    stands in (D-230), a counted input is taken whole per batch and the waste
    on top rounds to pieces (D-212), electricity is drunk by the hour at a
    machine that runs on it (D-269), and `craft.batch_max` cuts the answer from
    above. A client counting from the visible amounts would land on a refusal
    every time one of those applied -- and a "maximum" that refuses is worse
    than no button.

    **Everything the start would refuse for, the answer counts.** Materials
    and electricity both, because both are written off up front and either one
    short stops the batch whole (D-135). Nothing that fits is answered with
    less: the pool is read as it stands, so a batch that turns out affordable a
    minute later was never promised away.

    Found by halving, over the same arithmetic the forecast and the start run
    on: the demand of a batch never falls as the batch grows, and neither does
    its hour at the machine, so the largest that fits is where the halving
    stops. A formula of its own would be a second arithmetic, and it would
    drift (D-092).

    A batch of one that does not fit is not answered with nought: the
    preparation refuses in its own words -- what is missing and by how much --
    and that is the answer the player needs. Electricity refuses in `power`'s
    words, the very ones the start would have used.
    """
    ready = await _prepare(
        session,
        constants,
        catalog,
        body,
        output,
        1,
        tool_item_id=tool_item_id,
        proportions=proportions,
        way=way,
        recipe_key=recipe_key,
        tiers=tiers,
    )
    have = {name: sum(item.amount for item in rows) for name, rows in ready.stock.items()}
    #: What the machine could drink here, read without locking or creating
    #: anything: `None` at a machine driven by the hands, and then the hour it
    #: takes limits nothing.
    machine = None if ready.station is None else ready.station.type_key
    supply = await power.at_hand(session, constants, catalog, body, machine)
    grind = wear.effective(constants, ready.station)

    def juice(units: float) -> float:
        """What a batch this size draws -- by the same clock the start bills."""
        minutes = batch_minutes(constants, ready.proc, units, grind)
        return power.need_of(constants, catalog, machine, minutes / MINUTES_PER_HOUR)

    def fits(units: float) -> bool:
        wanted = demand(constants, catalog, ready.proc, units, ready.stock, proportions=proportions)
        if any(amount(value) > have.get(name, 0) for name, value in wanted.items()):
            return False
        return supply is None or juice(units) <= supply.have

    #: Not even one unit's worth of current: the machine stands, and it says so
    #: in `power`'s own words rather than answering a size that will not start.
    if machine is not None and supply is not None and juice(1) > supply.have:
        raise power.short_of(machine, supply, juice(1))

    #: The sizes that fit are a run from one upwards -- neither the demand nor
    #: the hour falls as the batch grows -- so the boundary is found by
    #: halving. `bisect` counts the fitting ones from the left, and that count
    #: **is** the largest that fits: the first size that does not stands
    #: exactly one past it. One unit is known to fit -- `_prepare` took it off
    #: these very stacks by this very arithmetic, and the current was just
    #: asked for it -- so the count is never nought.
    sizes = range(1, int(constants[R.CRAFT_BATCH_MAX]) + 1)
    return float(bisect_left(sizes, True, key=lambda units: not fits(units)))


async def start(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    output: str,
    units: float,
    *,
    tool_item_id: uuid.UUID | None = None,
    proportions: dict[str, float] | None = None,
    way: str | None = None,
    recipe_key: str | None = None,
    tiers: dict[str, str] | None = None,
    now: datetime | None = None,
) -> CraftBatch:
    """Start a batch: the input is written off at once, the product arrives on schedule.

    "On schedule" counts only the time the master stands by (D-209): the batch
    goes to work now if nothing else of theirs is running, otherwise it waits
    its turn; and it freezes whenever the master leaves the node.
    """
    moment = now or datetime.now(UTC)
    #: One body does one thing (D-211): a batch is not begun by a body that is
    #: searching the land, ploughing a plot or standing in a face. A second
    #: batch is not a second occupation -- it is a place in the queue (D-209).
    await occupation.require_free(session, body, besides=frozenset({occupation.CRAFT}))
    ready = await _prepare(
        session,
        constants,
        catalog,
        body,
        output,
        units,
        tool_item_id=tool_item_id,
        proportions=proportions,
        way=way,
        recipe_key=recipe_key,
        tiers=tiers,
    )
    forecast = ready.plan

    #: Electricity before the materials (D-269): a machine on it that cannot be
    #: fed refuses here, while nothing below has been touched yet.
    if ready.station is not None:
        await power.draw(
            session,
            constants,
            catalog,
            body,
            ready.station.type_key,
            forecast.minutes / MINUTES_PER_HOUR,
            now=moment,
        )

    for pick in ready.picks:
        if pick.item.amount > pick.take:
            pick.item.amount -= pick.take
        else:
            await session.delete(pick.item)
    await session.flush()

    batch = CraftBatch(
        body_id=body.id,
        node_id=body.node_id,
        output=forecast.output,
        units=amount(forecast.units),
        station=None if ready.station is None else ready.station.type_key,
        tool_item_id=tool_item_id,
        quality=_num(forecast.quality),
        spread=_num(forecast.spread),
        spent=forecast.consumes,
        recipe_key=ready.recipe_key,
        remaining_seconds=_seconds(forecast.minutes),
    )
    return await _launch(
        session,
        batch,
        body,
        now=moment,
        event={
            "output": forecast.output,
            "units": forecast.units,
            "quality": forecast.quality,
            "spent": forecast.consumes,
            "waste": forecast.waste,
            "recipe": ready.recipe_key,
        },
    )


#: Utensil class from `build/recipes.json`: pot and cauldron set the ceiling
#: alongside the hearth (D-119). A utensil is a tool, not a container.
UTENSILS = "cookware"


async def cook(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    output: str,
    filling: dict[str, str | None],
    *,
    tiers: dict[str, str] | None = None,
    now: datetime | None = None,
) -> CraftBatch:
    """Cook a pot: `cook.pot_portions` portions at once, a flow rather than an order.

    The composition is given by roles (D-119): into a role goes whatever product
    was found, one unit per pot. Quality per D-128, verbatim:

        ceiling  = min(hearth quality, utensil quality)
        base     = sum(input quality * role weight) / sum(weights of filled roles)
        quality  = ceiling * base/100 * (1 - penalty * number of empty roles)

    An unfilled role hurts more than a bad product: cheap fat is better than
    no fat. The combination decides the dish's **kind**, not its quality --
    dietary variety works by kind, there is no compatibility table.

    What counts as a product at all is decided by data: edible recipes and the
    vault's `edible` list. Suitability for a specific role is content too, but
    it does not exist yet: a product goes into any role, a pickaxe into none
    (16-cooking).
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise CraftError(key="craft-dead-cooks")
    await travel.require_here(session, body)
    #: One body does one thing (D-211), and a queued batch is not a second.
    await occupation.require_free(session, body, besides=frozenset({occupation.CRAFT}))

    recipe = catalog.recipes.recipe(output)
    if not recipe.roles:
        raise Unmakeable(key="craft-not-a-dish", goods=recipe.type_key)
    if not await _knows(session, body, recipe.type_key):
        raise NotLearned(key="craft-not-learned", recipe=recipe.type_key)

    #: Roles come from vault constants, with weights. An extra role in the request is an error.
    weights = constants[R.COOK_ROLE_WEIGHTS]
    unknown = set(filling) - set(weights)
    if unknown:
        raise CraftError(key="craft-unknown-roles", roles=", ".join(sorted(unknown)))

    proc = Procedure(
        output=recipe.type_key,
        station=None if recipe.station in (None, *BENCHLESS) else recipe.station,
        tools=(UTENSILS,),
        inputs=(),
        per_unit={},
        step_hours=step_hours(catalog, recipe),
        mix=False,
        needs_recipe=True,
    )
    station = await _station_item(session, body, proc)
    tools = await _tool_items(session, catalog, body, proc, None)
    ceiling = min(wear.effective(constants, item) for item in [station, *tools])

    #: Into each filled role goes one unit of product per whole pot.
    pocket = await body_container(session, body)
    scale = constants[R.QUALITY_SCALE]
    one = amount(1)
    weighted = 0.0
    closed_weight = 0.0
    consumed: dict[str, float] = {}
    products: list[str] = []
    for role, weight in weights.items():
        product = filling.get(role)
        if not product:
            continue
        name = catalog.recipes.resolve(product)
        if not catalog.recipes.is_ingredient(name):
            raise NotIngredient(key="craft-not-ingredient", goods=name)
        #: The tier is chosen per role: the good meat into the stew, the rest
        #: into the salting (D-058).
        chosen = (tiers or {}).get(role)
        stock = await _stock(session, pocket, (name,), tiers={name: chosen} if chosen else None)
        picks = _pick(stock, {name: amount_float(one)})
        quality = _material_quality(picks, scale.mid)
        for pick in picks:
            if pick.item.amount > pick.take:
                pick.item.amount -= pick.take
            else:
                await session.delete(pick.item)
        weighted += quality * weight
        closed_weight += weight
        consumed[name] = consumed.get(name, 0) + 1
        products.append(name)
    await session.flush()

    if closed_weight <= 0:
        raise NotEnough(key="craft-empty-pot")

    empty = len(weights) - len(products)
    base = weighted / closed_weight
    penalty = 1 - constants[R.COOK_EMPTY_ROLE_PENALTY] * empty / PERCENT
    quality = scale.clamp(ceiling * (base / scale.max) * max(0.0, penalty))

    portions = constants[R.COOK_POT_PORTIONS]
    minutes = batch_minutes(constants, proc, portions, wear.effective(constants, station))

    batch = CraftBatch(
        body_id=body.id,
        node_id=body.node_id,
        output=recipe.type_key,
        units=amount(portions),
        station=None if station is None else station.type_key,
        quality=_num(quality),
        spread=_num(constants[R.QUALITY_SPREAD_GOOD_RATIO]),
        spent=consumed,
        #: The combination decides the kind: "stew - beans, vegetables" and
        #: "stew - turnip" are different dishes for the diet, though the recipe
        #: is one (D-060 not violated).
        #: Flavor is an identity string, so it is built of D-251 ids: diet
        #: variety compares flavors, and an id survives every display rename.
        flavor=f"{recipe.id or recipe.name} · {', '.join(sorted(products))}",
        roles_filled=_num(len(products) / len(weights)),
        remaining_seconds=_seconds(minutes),
    )
    return await _launch(
        session,
        batch,
        body,
        now=moment,
        event={
            "work": "cook",
            "output": recipe.type_key,
            "flavor": batch.flavor,
            "quality": quality,
            "spent": consumed,
        },
    )


async def repair(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    *,
    tiers: dict[str, str] | None = None,
    now: datetime | None = None,
) -> CraftBatch:
    """Repair a thing.

    Repair restores condition but **lowers the ceiling**: after a repair the
    condition no longer rises to the previous maximum. So the thing stays
    finite (pillar P2), and repair a meaningful choice between "cheap now" and
    "expensive but new" (15-quality).

    Costs `craft.repair_cost_share` of a new thing -- in materials and in time:
    the vault gives one share, and there is nowhere for a second one to come from.
    """
    share = constants[R.CRAFT_REPAIR_COST_SHARE] / PERCENT
    return await _work_on(
        session, constants, catalog, body, item, BatchKind.REPAIR, share, tiers=tiers, now=now
    )


async def recycle(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    *,
    now: datetime | None = None,
) -> CraftBatch:
    """Take a thing apart for part of the materials.

    The return is always less than invested, the difference is a sink
    (20-systems/03). Quality carries over to the materials by
    `quality.recycle_carryover`: a good thing taken apart gives better raw
    material, but worse than it was.
    """
    from src.engine import coin  # noqa: PLC0415 -- lazy: breaks the import cycle with coin

    if coin.is_coin(catalog, item.type_key):
        raise Unmakeable(key="craft-coin-melts-elsewhere")
    share = constants[R.CRAFT_RECYCLE_RETURN] / PERCENT
    return await _work_on(
        session, constants, catalog, body, item, BatchKind.RECYCLE, share, now=now
    )


async def _target(session: AsyncSession, batch: CraftBatch) -> Item:
    item = await session.get(Item, batch.target_item_id)
    if item is None:
        raise CraftError(key="craft-target-gone", batch=str(batch.id))
    return item


async def _work_on(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    kind: BatchKind,
    share: float,
    *,
    tiers: dict[str, str] | None = None,
    now: datetime | None,
) -> CraftBatch:
    """The common flow of repair and recycling: both are work on a finished thing.

    Both go at the same machine as making: things are taken apart and repaired
    where they are made.
    """
    moment = now or datetime.now(UTC)
    if body.state is not BodyState.ALIVE:
        raise CraftError(key="craft-dead-works")
    await travel.require_here(session, body)

    inventory = await body_container(session, body)
    #: One body does one thing (D-211), and a queued batch is not a second.
    await occupation.require_free(session, body, besides=frozenset({occupation.CRAFT}))
    if item.container_id != inventory.id:
        raise CraftError(key="craft-item-not-in-hands")

    proc = procedure(catalog, item.type_key)
    station = await _station_item(session, body, proc)
    scale = constants[R.QUALITY_SCALE]

    spent: dict[str, float] = {}
    if kind is BatchKind.REPAIR:
        stock = await _stock(session, inventory, proc.inputs, tiers=_tiers_by(catalog, tiers))
        spent = {name: value * share for name, value in proc.per_unit.items()}
        for pick in _pick(stock, spent):
            if pick.item.amount > pick.take:
                pick.item.amount -= pick.take
            else:
                await session.delete(pick.item)
        await session.flush()

    minutes = batch_minutes(constants, proc, share, wear.effective(constants, station))
    batch = CraftBatch(
        body_id=body.id,
        node_id=body.node_id,
        kind=kind,
        output=item.type_key,
        target_item_id=item.id,
        units=amount(1),
        station=None if station is None else station.type_key,
        quality=_num(scale.min if item.quality is None else float(item.quality)),
        spread=_num(scale.min),
        spent=spent,
        remaining_seconds=_seconds(minutes),
    )
    return await _launch(
        session,
        batch,
        body,
        now=moment,
        event={
            "work": kind.value,
            "output": batch.output,
            "item_id": str(item.id),
            "spent": spent,
        },
    )
