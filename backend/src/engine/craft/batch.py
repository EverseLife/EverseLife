# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""craft: batch.

Split out of `engine/craft.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, ConstantError, Constants, current, current_catalog
from src.constants import registry as R
from src.engine import events, goods, liquid, occupation, travel, wear
from src.engine import world as world_engine
from src.engine.craft._base import (
    BENCHLESS,
    CraftError,
    NoLibrary,
    NoStrength,
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
    _pieces,
    _prepare,
    _release,
    _seconds,
    _station_item,
    _stock,
    _tiers_by,
    _tool_items,
    _wear_station,
)
from src.engine.craft.method_of_making import batch_minutes, procedure, step_hours
from src.engine.craft.queue import _launch, wake, wake_node
from src.engine.jobs import handler
from src.engine.world import body_container, learn, node_container
from src.models.craft import BatchKind, BatchState, CraftBatch
from src.models.event import EventKind
from src.models.identity import Body, BodyState, Identity, Knowledge
from src.models.inventory import Container, Item
from src.models.job import Job, JobKind
from src.models.world import Node
from src.units import (
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
    auto: bool = False,
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
        auto=auto,
        way=way,
        recipe_key=recipe_key,
        tiers=tiers,
    )
    return ready.plan


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
    auto: bool = False,
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
        auto=auto,
        way=way,
        recipe_key=recipe_key,
        tiers=tiers,
    )
    forecast = ready.plan

    #: The automaton's energy is written off up front, like materials: the city
    #: releases it at the tariff, and whoever burns it pays (D-085, D-135).
    if forecast.energy > 0:
        from src.engine import (  # noqa: PLC0415 -- lazy: breaks the import cycle with energy
            energy as power,
        )

        await power.draw_for_work(
            session,
            constants,
            body,
            forecast.energy,
            what=f"партия «{forecast.output}»",
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
UTENSILS = "Утварь"


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
        raise CraftError("мёртвое тело не готовит")
    await travel.require_here(session, body)
    #: One body does one thing (D-211), and a queued batch is not a second.
    await occupation.require_free(session, body, besides=frozenset({occupation.CRAFT}))

    recipe = catalog.recipes.recipe(output)
    if not recipe.roles:
        raise Unmakeable(f"{recipe.name!r} — не блюдо: это делают партией, не котлом")
    if not await _knows(session, body, recipe.name):
        raise NotLearned(f"рецепт {recipe.name!r} не скопирован в личность")

    #: Roles come from vault constants, with weights. An extra role in the request is an error.
    weights = constants[R.COOK_ROLE_WEIGHTS]
    unknown = set(filling) - set(weights)
    if unknown:
        raise CraftError(f"нет таких ролей: {', '.join(sorted(unknown))}")

    proc = Procedure(
        output=recipe.name,
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
            raise NotIngredient(f"«{name}» — не продукт: в котёл кладут съедобное")
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
        raise NotEnough("в котле пусто: закройте хотя бы одну роль")

    empty = len(weights) - len(products)
    base = weighted / closed_weight
    penalty = 1 - constants[R.COOK_EMPTY_ROLE_PENALTY] * empty / PERCENT
    quality = scale.clamp(ceiling * (base / scale.max) * max(0.0, penalty))

    portions = constants[R.COOK_POT_PORTIONS]
    minutes = batch_minutes(constants, proc, portions, wear.effective(constants, station))

    batch = CraftBatch(
        body_id=body.id,
        node_id=body.node_id,
        output=recipe.name,
        units=amount(portions),
        station=None if station is None else station.type_key,
        quality=_num(quality),
        spread=_num(constants[R.QUALITY_SPREAD_GOOD_RATIO]),
        spent=consumed,
        #: The combination decides the kind: "stew - beans, vegetables" and
        #: "stew - turnip" are different dishes for the diet, though the recipe
        #: is one (D-060 not violated).
        flavor=f"{recipe.name} · {', '.join(sorted(products))}",
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
            "output": recipe.name,
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
        raise Unmakeable(
            "монету переплавляют командой `coin.melt`: металл возвращается "
            "по её пробе, а не по норме рецепта"
        )
    share = constants[R.CRAFT_RECYCLE_RETURN] / PERCENT
    return await _work_on(
        session, constants, catalog, body, item, BatchKind.RECYCLE, share, now=now
    )


@handler(JobKind.CRAFT_BATCH)
async def finish(session: AsyncSession, job: Job) -> None:
    """Work is done: products, a repaired thing, or a handful of materials."""
    batch = await session.get(CraftBatch, uuid.UUID(job.payload["batch"]))
    if batch is None:  # pragma: no cover -- a job without a batch is a bug
        raise CraftError(f"задание {job.id}: партии нет")
    if batch.state is not BatchState.RUNNING:
        #: The job may have repeated after a failure -- no second batch comes of
        #: it. Or the batch froze while the master was away (D-209): the job of
        #: the frozen run finds nothing to finish, the resumed run has its own.
        return
    if job.payload.get("run", batch.runs) != batch.runs:
        #: A job of an earlier run, fired after the batch was frozen and resumed:
        #: it would finish the work ahead of time. Only the current run's job counts.
        return

    constants, catalog = current(), current_catalog()
    body = await session.get(Body, batch.body_id, with_for_update=True)
    node = await session.get(Node, batch.node_id)
    if body is None or node is None:  # pragma: no cover
        raise CraftError(f"партия {batch.id} ссылается в никуда")

    #: The master stands at the machine -- takes it themselves; left or died --
    #: the output stays at the machine. Matter does not vanish with whoever ordered it.
    at_bench = body.state is BodyState.ALIVE and body.node_id == batch.node_id
    where = await body_container(session, body) if at_bench else await node_container(session, node)

    if batch.kind is BatchKind.REPAIR:
        made = await _finish_repair(session, constants, batch)
    elif batch.kind is BatchKind.RECYCLE:
        made = await _finish_recycle(session, constants, catalog, batch, where)
    else:
        made = await _finish_make(session, constants, catalog, batch, body, where, job.run_at)

    await _wear_station(session, constants, batch)
    #: The work is over -- the machine is free and waits for the next (D-150).
    await _release(session, batch.station_item_id)

    batch.state = BatchState.DONE
    batch.finished_at = job.run_at
    await session.flush()

    await events.record(
        session,
        EventKind.CRAFT_FINISHED,
        actor_identity_id=body.identity_id,
        node_id=batch.node_id,
        batch_id=str(batch.id),
        work=batch.kind.value,
        output=batch.output,
        units=amount_float(batch.units),
        quality=made,
    )
    #: The master's hands and the machine are free: the next work of theirs
    #: takes its turn, and whoever waited for this machine gets it (D-209).
    await wake(session, body, now=job.run_at)
    await wake_node(session, node, now=job.run_at)


async def _finish_make(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    batch: CraftBatch,
    body: Body,
    where: Container,
    moment: datetime,
) -> list[float]:
    """The batch: products with a mark and a quality spread around the promised value."""
    #: The seed comes from the batch: a job retry after a failure gives the
    #: same thing, not a new roll. Spread is a property of the batch, not the
    #: worker's luck.
    noise = random.Random(str(batch.id))
    scale = constants[R.QUALITY_SCALE]
    spread = float(batch.spread)
    units = amount_float(batch.units)

    #: A coin has no quality at all: fineness describes it, and it comes off the
    #: batch together with the minter's mark (D-016).
    from src.engine import coin  # noqa: PLC0415 -- lazy: breaks the import cycle with coin

    coin_ = coin.is_coin(catalog, batch.output)

    #: Food gets a shelf life at making: cooked from the pot spoils
    #: `cook.spoilage_multiplier` times faster, dry at the base speed. An
    #: operation's output (ingot, gravel) has no recipe at all -- and that is
    #: normal, not a reason to drop the batch: smelting runs without a recipe
    #: (20-systems/03).
    try:
        recipe = catalog.recipes.recipe(batch.output)
    except ConstantError:
        recipe = None
    spoils_at = None
    if recipe is not None and recipe.food:
        from src.engine import food  # noqa: PLC0415 -- lazy: breaks the import cycle with food

        spoils_at = (
            food.cooked_spoils_at(constants, now=moment)
            if batch.flavor is not None
            else moment + timedelta(hours=food.shelf_hours(constants, rate=1))
        )

    made: list[float] = []
    for piece in _pieces(catalog, batch.output, units):
        quality = scale.clamp(float(batch.quality) + noise.uniform(-spread, spread))
        made.append(float(batch.fineness) if coin_ else quality)
        fresh = Item(
            container_id=where.id,
            type_key=batch.output,
            amount=amount(piece),
            quality=None if coin_ else _num(quality),
            fineness=batch.fineness,
            maker_identity_id=body.identity_id,
            made_at=moment,
            made_node_id=batch.node_id,
            spoils_at=spoils_at,
            flavor=batch.flavor,
            roles_filled=batch.roles_filled,
            recipe_key=batch.recipe_key,
        )
        session.add(fresh)
        #: Loose output joins a stack it is indistinguishable from (D-214) --
        #: which in practice means an earlier batch of the same hour that came
        #: out at exactly the same quality. The spread usually sees to it that
        #: it did not, and then the stacks stay apart, as they should.
        await world_engine.stack_up(session, fresh)
        #: A liquid is poured, not handed over (D-230): into the vessels in
        #: the master's hands, then into those at the machine. What fits
        #: nowhere is spilled -- and said so, because matter that vanished in
        #: silence is a bug report waiting to happen.
        within = await _vessels_reach(session, batch, where)
        spilled = await liquid.settle(session, catalog, fresh, within)
        if spilled > 0:
            await events.record(
                session,
                EventKind.STORAGE_SPILLED,
                actor_identity_id=body.identity_id,
                node_id=batch.node_id,
                type_key=batch.output,
                amount=spilled,
            )
    return made


async def _vessels_reach(
    session: AsyncSession, batch: CraftBatch, where: Container
) -> list[Container]:
    """Where a liquid output may be poured: the hands first when the master is
    at the machine, then the place itself. Away from the bench the hands are
    out of reach, and only what stands at the machine takes it."""
    yard = await node_container(session, await session.get(Node, batch.node_id))
    if where.id == yard.id:
        return [yard]
    return [where, yard]


async def _finish_repair(
    session: AsyncSession, constants: Constants, batch: CraftBatch
) -> list[float]:
    """Repair: condition came back, the ceiling dropped."""
    item = await _target(session, batch)
    scale = constants[R.QUALITY_SCALE]
    #: `quality.repair_ceiling_loss` is given negative -- we add rather than
    #: subtract: the sign belongs to the vault, not the engine.
    cap = scale.clamp(float(item.condition_cap) + constants[R.QUALITY_REPAIR_CEILING_LOSS])
    item.condition_cap = _num(cap)
    item.condition = _num(cap)
    await session.flush()
    return [cap]


async def _finish_recycle(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    batch: CraftBatch,
    where: Container,
) -> list[float]:
    """Recycling: the thing is gone, and not all materials came back."""
    from src.engine import coin  # noqa: PLC0415 -- lazy: breaks the import cycle with coin

    #: A coin melts by its fineness, not by the recipe norm: a spoiled one has
    #: exactly as much metal as was put into it (D-016).
    if coin.is_coin(catalog, batch.output):
        return await coin.finish_melt(session, constants, catalog, batch, where)

    item = await _target(session, batch)
    proc = procedure(catalog, batch.output)
    scale = constants[R.QUALITY_SCALE]

    carryover = constants[R.QUALITY_RECYCLE_CARRYOVER] / PERCENT
    share = constants[R.CRAFT_RECYCLE_RETURN] / PERCENT
    quality = scale.max if item.quality is None else float(item.quality)
    back = scale.clamp(quality * carryover)

    returned: list[float] = []
    for name, per_unit in proc.per_unit.items():
        #: What comes back comes back whole (D-212): a fifth of an ingot is not
        #: an ingot, and taking a thing apart cannot mint one out of rounding.
        given = amount(goods.whole(name, per_unit * share, catalog=catalog))
        if given <= 0:
            continue
        back_into = Item(container_id=where.id, type_key=name, amount=given, quality=_num(back))
        session.add(back_into)
        await world_engine.stack_up(session, back_into)
        returned.append(back)

    await events.record(
        session,
        EventKind.ITEM_CONSUMED,
        item_id=str(item.id),
        type_key=item.type_key,
        cause="переработка",
    )
    await session.delete(item)
    await session.flush()
    return returned


async def _target(session: AsyncSession, batch: CraftBatch) -> Item:
    item = await session.get(Item, batch.target_item_id)
    if item is None:
        raise CraftError(f"работа {batch.id}: вещи больше нет")
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
        raise CraftError("мёртвое тело не работает")
    await travel.require_here(session, body)

    inventory = await body_container(session, body)
    #: One body does one thing (D-211), and a queued batch is not a second.
    await occupation.require_free(session, body, besides=frozenset({occupation.CRAFT}))
    if item.container_id != inventory.id:
        raise CraftError("вещь не в руках: чинят и разбирают своё, а не чужое")

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


async def copy_recipe(
    session: AsyncSession, catalog: Catalog, body: Body, key: str
) -> Knowledge | None:
    """Copy a recipe from the Library.

    Free of money, unconditional and without citizenship -- and **does not work
    remotely**: the Library's only restriction is geographic (D-053).

    But not for nothing: copying costs `craft.copy_stamina` stamina (D-148).
    The body pays, not the account -- and knowledge stays a public good while no
    longer being a "learn the whole list in one go" button.
    """
    constants = current()
    await travel.require_here(session, body)
    node = await session.get(Node, body.node_id)
    #: The library is a machine (D-176): recipes are taken where it stands.
    if node is None or not await world_engine.is_library(session, node):
        raise NoLibrary("Библиотека не работает удалённо: за знанием надо прийти")

    recipe = catalog.recipes.recipe(key)
    #: A library holds what was put into it (D-068, D-209): the capital's has
    #: the base set, a city's has what people brought. What is not on the shelf
    #: is not here to copy -- go where it is, or bring it.
    from src.engine import library  # noqa: PLC0415 -- lazy: breaks the import cycle with library

    if not await library.has(session, node, recipe.name):
        raise NoLibrary(f"в этой библиотеке нет «{recipe.name}»: его сюда ещё не принесли")
    identity = await session.get(Identity, body.identity_id)
    if identity is None:  # pragma: no cover
        raise CraftError("тело без личности")

    await _lock_body(session, body)

    #: What is already known is not rewritten: the same body does not pay twice.
    #: Under the lock, or the "twice" is exactly what happens: two sockets of one
    #: identity both find the recipe unknown, both pay, and only the first of
    #: them learns anything -- the second `learn` sees the committed row and
    #: returns nothing, having charged for it.
    if await _knows(session, body, recipe.name):
        return None

    _pay_copy(constants, body)
    return await learn(session, identity, recipe.name)


async def _lock_body(session: AsyncSession, body: Body) -> None:
    """Take the body's row before the reads that decide the payment.

    Stamina is on the same list as money and remainders (CLAUDE.md): read
    outside a lock, two sockets of one identity both find the reserve enough
    and both write their own remainder -- one copy paid for two. The lock also
    has to cover the knowledge check, or the same pair pays twice for one
    recipe. `mining.swing` carries the full account of the pattern, including
    why the flush comes before the reread.
    """
    await session.flush()
    await session.refresh(body, with_for_update=True)


def _pay_copy(constants: Constants, body: Body) -> None:
    """Copying costs stamina, at a library shelf and off a carrier alike (D-148).

    The caller holds the body's row (`_lock_body`) -- this only spends it.
    """
    spend = constants[R.CRAFT_COPY_STAMINA]
    if spend > float(body.stamina):
        raise NoStrength(
            f"на переписывание нужно {spend:.0f} выносливости, а есть "
            f"{float(body.stamina):.1f}: знание бесплатно, но работа — нет"
        )
    body.stamina = Decimal(str(float(body.stamina) - spend))
