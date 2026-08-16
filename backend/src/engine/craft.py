"""Craft: batch, quality, losses (D-092, D-133).

Five conditions at once, all mandatory: knowledge, machine, tool, inputs, place
(20-systems/03-crafting). A batch is started in person and runs offline; the
input is written off at once, the product appears on schedule.

## Where each formula came from

Numbers are set by the vault, the order of steps is the engine's business
(vault CLAUDE.md). Below is the derivation of each formula so it can be checked
against D-092 and D-133 rather than taken on faith.

**Batch time.** `craft.time_per_unit` is "base batch time per unit of output",
`craft.time_growth_per_level` is "how many times longer a product takes with
each processing level deeper". The vault has already folded both into
`labor_hours`: a product's labour equals its own processing time plus the
labour of its inputs. So the own time is obtained by subtraction and **is not
re-derived in code**:

    step(product) = labor_hours(product) - sum(amounts[j] * labor_hours(j))

An operation without a recipe has its own time set directly --
`hours_per_unit[output]`.

**Machine speed.** `craft.station_speed_k` is "time multiplier from machine
quality; a broken anvil works slowly". So the worst machine works at the upper
bound of the multiplier, the best at the lower:

    k = max - (max - min) * machine_quality / scale

**Quality ceiling.** The lesser of machine and tool quality: the weakest link
limits (15-quality). What is absent does not limit: a "By hand" recipe without a
tool is bounded only by the raw material.

**Approach to the ceiling.** For an assembly it is set by inputs alone, for a
mix by inputs and proportion accuracy, with weights `quality.material_weight`
and `quality.ratio_weight` (D-092).

**Optimal proportion for a mix.** "Poor ore needs more coal and flux, pure ore
less". Amounts from `recipes.json` are the norm for ordinary raw material, i.e.
for the middle of the quality scale; deviation from it is symmetric:

    optimum[additive] = amounts[additive] * (1 + (middle - base_quality) / scale)

The base is the recipe's first input. A poor base needs up to one and a half
norms of additives, an excellent one down to half.

**Losses and spread.** `craft.waste_share` for correct work,
`craft.waste_bad_ratio` for a miss; `quality.spread_good_ratio` and
`quality.spread_bad_ratio` likewise. There is no "hit / miss" threshold in the
vault, and inventing one here is not allowed: both quantities follow accuracy
continuously.

**Craft premium.** `quality.hand_craft_bonus` is "up to +10 for hitting the
proportions exactly". Only for a mix: an assembly has no proportions at all, and
the premium there would be a bonus out of thin air.

Not one number beyond the vault appeared here. If a quantity is missing, it is
added to `data/constants.yaml`, not to code (D-065).

## What is not here yet

* **Dishes by roles** (`roles: true`) -- arrive with cooking on E2 (D-119,
  D-128) together with `cook.*`;
* **Automatic machine** -- on E2.5 together with energy (D-035), when
  `craft.auto_speed_k` switches on;
* **Invention, repair and recycling** -- separate registry actions with their
  own constants.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, ConstantError, Constants, current, current_catalog
from src.constants import registry as R
from src.constants.catalog import ItemKind, Operation, Recipe
from src.engine import events, travel, wear
from src.engine import world as world_engine
from src.engine.jobs import enqueue, handler
from src.engine.world import body_container, learn, node_container
from src.models.craft import BatchKind, BatchState, CraftBatch
from src.models.event import EventKind
from src.models.identity import Body, BodyState, Identity, Knowledge, KnowledgeKind
from src.models.inventory import Container, Item
from src.models.job import Job, JobKind
from src.models.world import Node
from src.units import MINUTES_PER_HOUR, PERCENT, amount, amount_float


class CraftError(Exception):
    pass


class NotLearned(CraftError):
    """The recipe is not in the identity. Knowledge is taken in the Library for free (D-053)."""


class NoStation(CraftError):
    """The machine is not in the node. The place requirement is what makes craft city-forming."""


class NoTool(CraftError):
    pass


class NoLibrary(CraftError):
    """No library in the node. Its only restriction is geographic."""


class NotEnough(CraftError):
    """Not enough inputs. Matter is not created (I1)."""


class NoStrength(CraftError):
    """Not enough strength. Work is paid with the body, not only with materials (D-148)."""


class Busy(CraftError):
    """The machine is taken by another worker. As many places as machines (D-150)."""


class CutOff(CraftError):
    """The node is disconnected for non-payment: machines do not work until the debt is settled
    (D-149)."""


class Unmakeable(CraftError):
    """Not done that way: no such method, or the mechanic has not arrived yet."""


class TooBig(CraftError):
    """The batch is larger than `craft.batch_max`."""


class NotIngredient(CraftError):
    """Something inedible was put in a role. What counts as a product is decided by data
    (16-cooking)."""


#: The "By hand" station from `build/recipes.json` is the absence of a machine, not a machine.
HANDS = "Руками"

#: "Construction" is not a machine either but on-site work (D-158). There is
#: and cannot be an item with that name in the vault data: it marks everything
#: assembled on a plot -- from road surface to a workshop. Requiring a machine
#: for it would forbid a whole family of recipes, which is what happened
#: before D-158.
SITE = "Стройка"

#: What reads as "no machine needed". A list of two, both from data.
BENCHLESS = (HANDS, SITE)

#: Automatic machine (D-035, D-058). The industrial mode: works twice as fast,
#: sets the ceiling itself, needs no tool, gives an even result -- and for that
#: it eats energy from the city pool at the tariff.
#:
#: The vault does not list which processes are automated, so the automaton is
#: substituted for any recipe machine -- by the master's own decision, not
#: silently: "put on automatic" is a choice between quality and volume.
AUTO_BENCH = "Автоматический станок"


@dataclass(frozen=True, slots=True)
class Procedure:
    """A way to make something: a recipe, or an operation without a recipe.

    From here on the engine does not care where the method came from -- except
    for one thing: a recipe requires knowledge, an operation never does
    (20-systems/03-crafting).
    """

    output: str
    #: Machine name, or None if done by hand.
    station: str | None
    #: What must be in the hands: an item name or a tool class.
    tools: tuple[str, ...]
    inputs: tuple[str, ...]
    #: How much of what per unit of output.
    per_unit: dict[str, float]
    #: Own processing time, hours per unit.
    step_hours: float
    #: Mix: composition is given as a proportion, and hit accuracy affects quality.
    mix: bool
    needs_recipe: bool
    #: Node property where the method is possible (D-177): "Felling" -> `forest`.
    #: Empty -- the method is not tied to a place.
    place: str | None = None


@dataclass(frozen=True, slots=True)
class Plan:
    """Batch forecast -- what the player sees **before** materials are spent.

    Quality is shown as an exact number: without it the player cannot connect
    action with result and will not derive a single proportion (D-092).
    """

    output: str
    units: float
    quality: float
    spread: float
    ceiling: float
    #: Proportion hit accuracy, 0..1. Always 1 for an assembly: no proportions.
    accuracy: float
    #: Loss share for waste and scrap, percent of inputs.
    waste: float
    minutes: float
    consumes: dict[str, float] = field(default_factory=dict)
    #: Industrial mode: the batch runs on the automaton (D-035, D-058).
    auto: bool = False
    #: How much energy the automaton eats per batch and what that costs at the
    #: city tariff. Zero for a manual batch: a workbench consumes nothing.
    energy: float = 0.0
    energy_cost: int = 0


@dataclass(frozen=True, slots=True)
class _Pick:
    """The stack taken from, and exactly how much is taken."""

    item: Item
    take: int


@dataclass(frozen=True, slots=True)
class _Ready:
    """A parsed batch request: the forecast plus what was set aside for it."""

    plan: Plan
    picks: tuple[_Pick, ...]
    station: Item | None
    auto: bool = False


# --- method of making ---------------------------------------------------------


def procedure(catalog: Catalog, output: str, *, way: str | None = None) -> Procedure:
    """Find a way to make `output` -- first among recipes, then operations.

    One thing can come from several operations: wood is both felled with an axe
    and gathered as deadwood by hand (D-196). Which one is the worker's choice,
    not the resolver's, so `way` names the operation. Without it the first
    listed wins -- the vault lists the proper, faster way first.
    """
    book = catalog.recipes
    name = book.resolve(output)
    found = next((recipe for recipe in book.recipes if recipe.name == name), None)
    if found is not None:
        return _from_recipe(catalog, found)

    ways = [
        operation
        for operation in book.operations
        if name in {book.resolve(gives) for gives in operation.gives}
    ]
    if way is not None:
        ways = [operation for operation in ways if operation.name == way]
        if not ways:
            raise Unmakeable(f"{output!r} не делается способом {way!r}")
    if ways:
        return _from_operation(catalog, ways[0], name)
    raise Unmakeable(f"{output!r} не делается ни по рецепту, ни операцией")


def _from_recipe(catalog: Catalog, recipe: Recipe) -> Procedure:
    if recipe.roles:
        raise Unmakeable(f"{recipe.name!r} — блюдо: его варят котлом, командой `cook`")
    if recipe.kind is ItemKind.MONEY:
        raise Unmakeable(
            f"{recipe.name!r} — монета: её чеканят, и металл считается по пробе "
            "(команда `coin.mint`)"
        )
    book = catalog.recipes
    return Procedure(
        output=recipe.name,
        #: The machine also goes through synonyms: recipes call it "Furnace",
        #: while in the node stands a "Smelting furnace". Without name resolution
        #: all chemistry and refining were unmakeable -- no machine has that name.
        station=None if recipe.station in (None, *BENCHLESS) else book.resolve(recipe.station),
        tools=(),
        inputs=tuple(book.resolve(name) for name in recipe.inputs),
        per_unit={book.resolve(name): value for name, value in recipe.amounts.items()},
        step_hours=step_hours(catalog, recipe),
        mix=recipe.mix,
        needs_recipe=True,
    )


def _from_operation(catalog: Catalog, operation: Operation, output: str) -> Procedure:
    book = catalog.recipes
    per_unit = {
        book.resolve(name): value for name, value in operation.amounts.get(output, {}).items()
    }
    #: An operation without spends is extraction. With a `place` field it is
    #: place extraction (D-177): felling runs as a batch without inputs.
    #: Without the field it is somebody else's mechanic (a vein).
    if not per_unit and operation.place is None:
        raise Unmakeable(
            f"операция «{operation.name}» ничего не расходует: это добыча, а не крафт"
        )

    station: str | None = None
    tools: list[str] = []
    for requirement in operation.requires:
        canonical = book.resolve(requirement)
        if book.tools_of_class(canonical):
            tools.append(canonical)
        elif book.is_raw(canonical):
            #: "Vein" in extraction requirements is not equipment but the mechanic itself.
            continue
        elif book.recipe(canonical).kind is ItemKind.STATION:
            station = canonical
        else:
            tools.append(canonical)

    #: Bare hands are slower than a tool (D-196): gathering deadwood, stones and
    #: flax breaks the "axe from wood, wood from axe" circle without replacing
    #: proper extraction.
    hours = operation.hours_per_unit.get(output, 0.0)
    if not tools and station is None and operation.place is not None:
        from src.constants import current

        hours *= current()[R.HARVEST_BAREHAND_K]

    return Procedure(
        output=output,
        station=station,
        tools=tuple(tools),
        inputs=tuple(per_unit),
        per_unit=per_unit,
        step_hours=hours,
        mix=False,
        needs_recipe=False,
        place=operation.place,
    )


def step_hours(catalog: Catalog, recipe: Recipe) -> float:
    """Own processing time: the product's labour minus the labour of its inputs.

    The vault has already folded `craft.time_per_unit` and growth by processing
    depth into this (D-133). Re-deriving them would mean keeping a second copy
    of the formula.
    """
    book = catalog.recipes
    spent = sum(value * book.labor_of(name) for name, value in recipe.amounts.items())
    return max(0.0, book.labor_of(recipe.name) - spent)


def batch_minutes(
    constants: Constants,
    proc: Procedure,
    units: float,
    station_quality: float,
    *,
    auto: bool = False,
) -> float:
    """How long a batch takes. A broken anvil works slowly.

    The automaton wins on volume: `craft.auto_speed_k` -- that many times faster.
    """
    speed = constants[R.CRAFT_STATION_SPEED_K]
    scale = constants[R.QUALITY_SCALE]
    k = speed.max - (speed.max - speed.min) * station_quality / scale.max
    minutes = proc.step_hours * MINUTES_PER_HOUR * units * k
    return minutes / constants[R.CRAFT_AUTO_SPEED_K] if auto else minutes


# --- quality -----------------------------------------------------------------


def optimal_amounts(
    constants: Constants, proc: Procedure, units: float, base_quality: float
) -> dict[str, float]:
    """The optimal proportion for **this** raw material.

    An assembly has none: a workbench is a log and a rope, nothing in between.
    """
    nominal = {name: value * units for name, value in proc.per_unit.items()}
    if not proc.mix or not proc.inputs:
        return nominal

    scale = constants[R.QUALITY_SCALE]
    correction = 1 + (scale.mid - base_quality) / scale.max
    base = proc.inputs[0]
    return {
        name: value if name == base else value * correction for name, value in nominal.items()
    }


def ratio_accuracy(actual: dict[str, float], optimal: dict[str, float]) -> float:
    """How well the proportion was hit: 1 -- exactly, 0 -- missed entirely."""
    errors = [
        abs(actual.get(name, 0.0) - want) / want for name, want in optimal.items() if want > 0
    ]
    if not errors:
        return 1.0
    return max(0.0, 1 - sum(errors) / len(errors))


def waste_share(constants: Constants, accuracy: float) -> float:
    """Losses for waste and scrap, percent of inputs.

    There is no "hit / miss" threshold in the vault, so losses follow accuracy
    continuously: from `craft.waste_share` for correct work to
    `craft.waste_bad_ratio` for a complete miss.
    """
    good = constants[R.CRAFT_WASTE_SHARE]
    bad = constants[R.CRAFT_WASTE_BAD_RATIO]
    return good + (bad - good) * (1 - accuracy)


def spread_of(constants: Constants, accuracy: float) -> float:
    """Result spread: narrow with correct proportions, wide on a miss."""
    good = constants[R.QUALITY_SPREAD_GOOD_RATIO]
    bad = constants[R.QUALITY_SPREAD_BAD_RATIO]
    return good + (bad - good) * (1 - accuracy)


def forecast_quality(
    constants: Constants,
    proc: Procedure,
    *,
    ceiling: float,
    material: float,
    accuracy: float,
    auto: bool = False,
) -> float:
    """Quality forecast: the ceiling and how close to it we came."""
    scale = constants[R.QUALITY_SCALE]
    if proc.mix:
        closeness = (
            constants[R.QUALITY_MATERIAL_WEIGHT] * material
            + constants[R.QUALITY_RATIO_WEIGHT] * accuracy * scale.max
        ) / PERCENT
    else:
        closeness = material
    value = ceiling * closeness / scale.max
    #: Craft premium: the master sees today's ore is worse than usual and
    #: adjusts proportions for it. A machine always works by its setting
    #: (15-quality), so the automaton gets no premium -- that is the whole
    #: difference between the modes.
    if proc.mix and not auto:
        value += constants[R.QUALITY_HAND_CRAFT_BONUS] * accuracy
    return scale.clamp(min(value, quality_cap(constants, proc, ceiling, auto=auto)))


def quality_cap(
    constants: Constants, proc: Procedure, ceiling: float, *, auto: bool = False
) -> float:
    """Only the craft premium rises above the ceiling, and only for a mix."""
    scale = constants[R.QUALITY_SCALE]
    bonus = (
        constants[R.QUALITY_HAND_CRAFT_BONUS] if proc.mix and not auto else 0.0
    )
    return min(scale.max, ceiling + bonus)


# --- batch -------------------------------------------------------------------


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
    now: datetime | None = None,
) -> CraftBatch:
    """Start a batch: the input is written off at once, the product arrives on schedule."""
    moment = now or datetime.now(UTC)
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
    )
    forecast = ready.plan

    #: The automaton's energy is written off up front, like materials: the city
    #: releases it at the tariff, and whoever burns it pays (D-085, D-135).
    if forecast.energy > 0:
        from src.engine import energy as power

        await power.draw_for_work(
            session, constants, body, forecast.energy,
            what=f"партия «{forecast.output}»", now=moment,
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
        station_item_id=None if ready.station is None else ready.station.id,
        tool_item_id=tool_item_id,
        quality=_num(forecast.quality),
        spread=_num(forecast.spread),
        spent=forecast.consumes,
        ready_at=moment + timedelta(minutes=forecast.minutes),
    )
    session.add(batch)
    await session.flush()
    await _occupy(session, ready.station, body, batch.ready_at)

    event = await events.record(
        session,
        EventKind.CRAFT_STARTED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        batch_id=str(batch.id),
        output=forecast.output,
        units=forecast.units,
        quality=forecast.quality,
        spent=forecast.consumes,
        waste=forecast.waste,
    )
    #: A batch is an ordinary journal job: it survives a process restart and
    #: runs exactly once (01-tech-notes, pattern 1).
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
        stock = await _stock(session, pocket, (name,))
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
        station_item_id=None if station is None else station.id,
        quality=_num(quality),
        spread=_num(constants[R.QUALITY_SPREAD_GOOD_RATIO]),
        spent=consumed,
        #: The combination decides the kind: "stew - beans, vegetables" and
        #: "stew - turnip" are different dishes for the diet, though the recipe
        #: is one (D-060 not violated).
        flavor=f"{recipe.name} · {', '.join(sorted(products))}",
        roles_filled=_num(len(products) / len(weights)),
        ready_at=moment + timedelta(minutes=minutes),
    )
    session.add(batch)
    await session.flush()
    await _occupy(session, station, body, batch.ready_at)

    event = await events.record(
        session,
        EventKind.CRAFT_STARTED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        batch_id=str(batch.id),
        work="cook",
        output=recipe.name,
        flavor=batch.flavor,
        quality=quality,
        spent=consumed,
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


async def repair(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    item: Item,
    *,
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
        session, constants, catalog, body, item, BatchKind.REPAIR, share, now=now
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
    from src.engine import coin

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
        #: The job may have repeated after a failure -- no second batch comes of it.
        return

    constants, catalog = current(), current_catalog()
    body = await session.get(Body, batch.body_id)
    node = await session.get(Node, batch.node_id)
    if body is None or node is None:  # pragma: no cover
        raise CraftError(f"партия {batch.id} ссылается в никуда")

    #: The master stands at the machine -- takes it themselves; left or died --
    #: the output stays at the machine. Matter does not vanish with whoever ordered it.
    at_bench = body.state is BodyState.ALIVE and body.node_id == batch.node_id
    where = (
        await body_container(session, body) if at_bench else await node_container(session, node)
    )

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
    from src.engine import coin

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
        from src.engine import food

        spoils_at = (
            food.cooked_spoils_at(constants, now=moment)
            if batch.flavor is not None
            else moment + timedelta(hours=food.shelf_hours(constants, rate=1))
        )

    made: list[float] = []
    for piece in _pieces(catalog, batch.output, units):
        quality = scale.clamp(float(batch.quality) + noise.uniform(-spread, spread))
        made.append(float(batch.fineness) if coin_ else quality)
        session.add(
            Item(
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
            )
        )
    return made


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
    from src.engine import coin

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
        given = amount(per_unit * share)
        if given <= 0:
            continue
        session.add(
            Item(container_id=where.id, type_key=name, amount=given, quality=_num(back))
        )
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
    if item.container_id != inventory.id:
        raise CraftError("вещь не в руках: чинят и разбирают своё, а не чужое")

    proc = procedure(catalog, item.type_key)
    station = await _station_item(session, body, proc)
    scale = constants[R.QUALITY_SCALE]

    spent: dict[str, float] = {}
    if kind is BatchKind.REPAIR:
        stock = await _stock(session, inventory, proc.inputs)
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
        station_item_id=None if station is None else station.id,
        quality=_num(scale.min if item.quality is None else float(item.quality)),
        spread=_num(scale.min),
        spent=spent,
        ready_at=moment + timedelta(minutes=minutes),
    )
    session.add(batch)
    await session.flush()
    await _occupy(session, station, body, batch.ready_at)

    event = await events.record(
        session,
        EventKind.CRAFT_STARTED,
        actor_identity_id=body.identity_id,
        node_id=body.node_id,
        batch_id=str(batch.id),
        work=kind.value,
        output=batch.output,
        item_id=str(item.id),
        spent=spent,
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
    identity = await session.get(Identity, body.identity_id)
    if identity is None:  # pragma: no cover
        raise CraftError("тело без личности")

    #: What is already known is not rewritten: the same body does not pay twice.
    if await _knows(session, body, recipe.name):
        return None

    spend = constants[R.CRAFT_COPY_STAMINA]
    if spend > float(body.stamina):
        raise NoStrength(
            f"на переписывание нужно {spend:.0f} выносливости, а есть "
            f"{float(body.stamina):.1f}: знание бесплатно, но работа — нет"
        )
    body.stamina = Decimal(str(float(body.stamina) - spend))
    await session.flush()
    return await learn(session, identity, recipe.name)


# --- internal ----------------------------------------------------------------


async def _prepare(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    output: str,
    units: float,
    *,
    tool_item_id: uuid.UUID | None,
    proportions: dict[str, float] | None,
    auto: bool = False,
    way: str | None = None,
) -> _Ready:
    """The common flow of forecast and start.

    One function for both cases deliberately: a forecast computed by code other
    than the batch's will sooner or later diverge from it -- and the promise
    "the player sees the exact number before the batch" stops being true (D-092).
    """
    if body.state is not BodyState.ALIVE:
        raise CraftError("мёртвое тело не работает")
    await travel.require_here(session, body)
    if units <= 0:
        raise CraftError("партия из нуля единиц")
    if units > constants[R.CRAFT_BATCH_MAX]:
        raise TooBig(f"партия больше craft.batch_max: {units}")

    proc = procedure(catalog, output, way=way)
    if not _stackable(catalog, proc.output) and units != int(units):
        raise CraftError(f"{proc.output!r} — изделие, а не сырьё: партия считается штуками")
    if proc.needs_recipe and not await _knows(session, body, proc.output):
        raise NotLearned(f"рецепт {proc.output!r} не скопирован в личность")

    #: Place extraction (D-177): runs where the node has the named property,
    #: and only on own or unowned land -- somebody else's forest belongs to its owner.
    if proc.place is not None:
        node = await session.get(Node, body.node_id)
        if node is None or not (node.properties or {}).get(proc.place):
            raise CraftError(f"здесь нет: {proc.place}")
        foreign = (
            node.owner_identity_id is not None
            and node.owner_identity_id != body.identity_id
        ) or (node.owner_identity_id is None and node.owner_city_id is not None)
        if foreign:
            raise CraftError(f"{proc.place} на чужой земле: рубить может хозяин")

    if auto:
        #: Industrial mode: the machine sets the ceiling, no tool is needed at
        #: all, proportions are its setting, not the master's decision (D-058).
        station = await _named_station(session, body, AUTO_BENCH)
        tools: list[Item] = []
    else:
        station = await _station_item(session, body, proc)
        tools = await _tool_items(session, catalog, body, proc, tool_item_id)

    scale = constants[R.QUALITY_SCALE]
    #: Limits **effective** quality: a broken anvil gives a worse result, not
    #: just breaks suddenly (`engine.wear`).
    limiters = [wear.effective(constants, item) for item in [station, *tools] if item is not None]
    ceiling = min(limiters) if limiters else scale.max

    inventory = await body_container(session, body)
    stock = await _stock(session, inventory, proc.inputs)

    optimal = optimal_amounts(constants, proc, units, _base_quality(proc, stock, scale.max))
    actual = (
        {catalog.recipes.resolve(name): value * units for name, value in proportions.items()}
        if proportions and not auto
        else {name: value * units for name, value in proc.per_unit.items()}
    )
    #: For the automaton the proportion is its setting, made once: it always
    #: works by the recipe norm and therefore hits it exactly (D-058).
    accuracy = 1.0 if auto or not proc.mix else ratio_accuracy(actual, optimal)

    waste = waste_share(constants, accuracy)
    #: Waste is a share of the **inputs**, so it is taken on top of the norm,
    #: not out of the output: a batch of ten nails does not give nine and a half nails.
    required = {name: value / (1 - waste / PERCENT) for name, value in actual.items()}

    picks = _pick(stock, required)
    minutes = batch_minutes(
        constants, proc, units, wear.effective(constants, station), auto=auto
    )
    #: The automaton eats energy for its working time. A manual workbench
    #: consumes nothing: craft stays available to those with no money for bills.
    from src.engine import energy as power

    energy = (
        constants[R.ENERGY_AUTO_BENCH_DRAW] * minutes / MINUTES_PER_HOUR if auto else 0.0
    )
    energy_price = 0
    if energy > 0:
        node = await session.get(Node, body.node_id)
        energy_price = await power.price_of(session, constants, node, energy)

    forecast = Plan(
        output=proc.output,
        units=units,
        quality=forecast_quality(
            constants,
            proc,
            ceiling=ceiling,
            material=_material_quality(picks, scale.max),
            accuracy=accuracy,
            auto=auto,
        ),
        spread=spread_of(constants, accuracy),
        ceiling=ceiling,
        accuracy=accuracy,
        waste=waste,
        minutes=minutes,
        consumes=dict(required),
        auto=auto,
        energy=energy,
        energy_cost=energy_price,
    )
    return _Ready(plan=forecast, picks=tuple(picks), station=station, auto=auto)


async def _knows(session: AsyncSession, body: Body, key: str) -> bool:
    stmt = select(Knowledge).where(
        Knowledge.identity_id == body.identity_id,
        Knowledge.kind == KnowledgeKind.RECIPE,
        Knowledge.key == key,
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def _named_station(session: AsyncSession, body: Body, name: str) -> Item:
    """The machine with this name in the node, the best of the free ones."""
    return await _pick_station(session, body, name)


async def _station_item(session: AsyncSession, body: Body, proc: Procedure) -> Item | None:
    """The machine stands in the node -- exactly this makes craft city-forming."""
    if proc.station is None:
        return None
    return await _pick_station(session, body, proc.station)


async def _pick_station(session: AsyncSession, body: Body, name: str) -> Item:
    """The best **free** machine with this name in the node (D-150).

    A machine is taken by one worker: while a batch runs it is not given to a
    second. Hence the consequence the rule exists for -- the city workshop
    stops being a free shop floor for the whole town, and the craftsman comes
    to need a machine of their own at home.

    A node disconnected for non-payment does not work at all (D-149): the meter
    is as much a condition of work as the machine itself.
    """
    from src.engine import utility

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise CraftError("тело вне узла")
    if await utility.cut_off(session, node):
        raise CutOff(
            f"«{node.name}» отключён за неуплату: станки не работают, пока долг не закрыт"
        )

    where = await node_container(session, node)
    moment = datetime.now(UTC)
    standing = (
        await session.execute(
            select(Item)
            .where(Item.container_id == where.id, Item.type_key == name)
            .order_by(Item.quality.desc())
        )
    ).scalars().all()
    if not standing:
        raise NoStation(f"в узле нет станка «{name}»")

    own = False
    for machine in standing:
        #: Taken means taken, including by the same master: one work goes at a
        #: machine, not as many as the owner managed to order. The stamp
        #: insures against eternal occupancy: the batch could have vanished
        #: past its job, and the machine need not idle forever because of that.
        if machine.busy_body_id is not None and (
            machine.busy_until is None or machine.busy_until > moment
        ):
            own = own or machine.busy_body_id == body.id
            continue
        return machine
    raise Busy(
        f"«{name}» занят"
        + (" вашей же работой: дождитесь конца партии" if own else
           ": за станком работает один. Свой станок ставят у себя")
    )


async def _occupy(session: AsyncSession, station: Item | None, body: Body, until) -> None:
    """Occupy the machine for the duration of the work (D-150)."""
    if station is None:
        return
    station.busy_body_id = body.id
    station.busy_until = until
    await session.flush()


async def _release(session: AsyncSession, station_item_id) -> None:
    """Free the machine. Called together with the completion of the work."""
    if station_item_id is None:
        return
    station = await session.get(Item, station_item_id)
    if station is None:  # pragma: no cover -- the machine may have been dismantled
        return
    station.busy_body_id = None
    station.busy_until = None
    await session.flush()


async def _tool_items(
    session: AsyncSession,
    catalog: Catalog,
    body: Body,
    proc: Procedure,
    tool_item_id: uuid.UUID | None,
) -> list[Item]:
    """The tool is carried along and takes part in the quality ceiling."""
    inventory = await body_container(session, body)
    found: list[Item] = []

    for requirement in proc.tools:
        names = catalog.recipes.tools_of_class(requirement) or (requirement,)
        item = (
            await session.execute(
                select(Item)
                .where(Item.container_id == inventory.id, Item.type_key.in_(names))
                .order_by(Item.quality.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if item is None:
            raise NoTool(f"нужен инструмент: {requirement}")
        found.append(item)

    if tool_item_id is not None and all(item.id != tool_item_id for item in found):
        chosen = await session.get(Item, tool_item_id)
        if chosen is None or chosen.container_id != inventory.id:
            raise NoTool("инструмента нет в инвентаре")
        found.append(chosen)
    return found


async def _stock(
    session: AsyncSession, container: Container, names: Iterable[str]
) -> dict[str, list[Item]]:
    """What lies for each input, worst first.

    The order is not accidental: the worse goes into the work, and the pure raw
    material stays for the batch it was mined for. Picking a stack by hand will
    arrive with the interface.
    """
    out: dict[str, list[Item]] = {}
    for name in names:
        rows = (
            (
                await session.execute(
                    select(Item)
                    .where(Item.container_id == container.id, Item.type_key == name)
                    .order_by(Item.quality.asc().nulls_first(), Item.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        out[name] = list(rows)
    return out


def _base_quality(proc: Procedure, stock: dict[str, list[Item]], default: float) -> float:
    """Base quality -- of the first input. The proportion optimum depends on it."""
    if not proc.inputs:
        return default
    graded = [item for item in stock.get(proc.inputs[0], []) if item.quality is not None]
    total = sum(item.amount for item in graded)
    if not total:
        return default
    return sum(float(item.quality) * item.amount for item in graded) / total


def _pick(stock: dict[str, list[Item]], required: dict[str, float]) -> list[_Pick]:
    """Gather what is needed from the stacks. Not enough -- the batch does not start at all."""
    picks: list[_Pick] = []
    for name, want in required.items():
        left = amount(want)
        for item in stock.get(name, []):
            if left <= 0:
                break
            take = min(left, item.amount)
            picks.append(_Pick(item=item, take=take))
            left -= take
        if left > 0:
            raise NotEnough(f"не хватает «{name}»: нужно ещё {amount_float(left)}")
    return picks


def _material_quality(picks: Sequence[_Pick], default: float) -> float:
    """Input quality, weighted by amount.

    An input without quality -- water, energy, coin -- is not in the average:
    it has no quality at all, rather than zero (15-quality, open questions).
    """
    graded = [pick for pick in picks if pick.item.quality is not None]
    total = sum(pick.take for pick in graded)
    if not total:
        return default
    return sum(float(pick.item.quality) * pick.take for pick in graded) / total


async def _wear_station(session: AsyncSession, constants: Constants, batch: CraftBatch) -> None:
    """The machine wears per batch: maintenance is mandatory (D-129)."""
    if batch.station_item_id is None:
        return
    station = await session.get(Item, batch.station_item_id)
    if station is None:  # pragma: no cover -- the machine may have been dismantled
        return
    await wear.spend(
        session,
        constants,
        station,
        constants[R.WEAR_STATION_PER_BATCH],
        cause="партия крафта",
    )


def _pieces(catalog: Catalog, output: str, units: float) -> list[float]:
    """What the batch turns into: one stack of raw material or so many products.

    Raw material stacks, products do not (04-items), and every product has its
    own spread roll -- because each has its own mark and quality (D-058).
    """
    if _stackable(catalog, output):
        return [units]
    return [1.0] * int(units)


def _stackable(catalog: Catalog, output: str) -> bool:
    try:
        kind = catalog.recipes.recipe(output).kind
    except ConstantError:
        #: An operation's output has no recipe -- it is raw material, and it stacks.
        return True
    return kind in (ItemKind.MATERIAL, ItemKind.CONSUMABLE, ItemKind.MONEY)


def _num(value: float) -> Decimal:
    """A number on the 0..100 scale in the form the database stores it."""
    return Decimal(str(value))
