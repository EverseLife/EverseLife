# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""craft: internal.

Split out of `engine/craft.py` along its sections (review 2026-08-23, wave 3).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, Constants, current, current_catalog
from src.constants import registry as R
from src.engine import goods, travel, wear
from src.engine.craft import power
from src.engine.craft._base import (
    Busy,
    CraftError,
    CutOff,
    NoStation,
    NotEnough,
    NotLearned,
    NoTool,
    Plan,
    Procedure,
    TooBig,
    Unmakeable,
    _Pick,
    _Ready,
    carrier_names,
)
from src.engine.craft.method_of_making import batch_minutes, procedure
from src.engine.craft.quality import (
    forecast_quality,
    optimal_amounts,
    ratio_accuracy,
    spread_of,
    waste_share,
)
from src.engine.world import body_container, has_place, node_yard
from src.models.craft import CraftBatch
from src.models.identity import Body, BodyState, Knowledge, KnowledgeKind
from src.models.inventory import Container, Item
from src.models.world import Node
from src.units import (
    MINUTES_PER_HOUR,
    PERCENT,
    SECONDS_PER_HOUR,
    amount,
    amount_float,
)


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
    way: str | None = None,
    recipe_key: str | None = None,
    tiers: dict[str, str] | None = None,
) -> _Ready:
    """The common flow of forecast and start.

    One function for both cases deliberately: a forecast computed by code other
    than the batch's will sooner or later diverge from it -- and the promise
    "the player sees the exact number before the batch" stops being true (D-092).
    """
    if body.state is not BodyState.ALIVE:
        raise CraftError(key="craft-dead-works")
    await travel.require_here(session, body)
    if units <= 0:
        raise CraftError(key="craft-zero-batch")
    if units > constants[R.CRAFT_BATCH_MAX]:
        raise TooBig(key="craft-batch-too-big", units=units)

    proc = procedure(catalog, output, way=way)
    #: A counted thing is made in whole pieces (D-212): there is no batch of
    #: two and a half ingots, and a product was never divisible to begin with.
    if goods.counted(proc.output, catalog) and units != int(units):
        raise CraftError(key="craft-counted-whole", goods=proc.output)
    if proc.needs_recipe and not await _knows(session, body, proc.output):
        raise NotLearned(key="craft-not-learned", recipe=proc.output)

    #: A knowledge carrier is written by whoever knows the recipe (D-209): the
    #: name of what goes onto it is part of the request, and it must be in the
    #: master's own head -- a carrier is a copy, not a source.
    if proc.output in carrier_names(catalog):
        if not recipe_key:
            raise CraftError(key="craft-write-needs-recipe")
        recipe_key = catalog.recipes.recipe(recipe_key).type_key
        if not await _knows(session, body, recipe_key):
            raise NotLearned(key="craft-write-not-learned", recipe=recipe_key)
    elif recipe_key:
        raise CraftError(key="craft-not-a-carrier", goods=proc.output)

    #: Place extraction (D-177): runs where the node has the named property,
    #: and only on own or unowned land -- somebody else's forest belongs to its owner.
    if proc.place is not None:
        node = await session.get(Node, body.node_id)
        #: Asked through the one shared question (D-254): `water` is stored as
        #: a word, and a bare truthiness test read a dry node as watered.
        if not has_place(node, proc.place):
            raise CraftError(key="craft-no-place", place=proc.place)
        foreign = (
            node.owner_identity_id is not None and node.owner_identity_id != body.identity_id
        ) or (node.owner_identity_id is None and node.owner_city_id is not None)
        if foreign:
            raise CraftError(key="craft-place-not-yours", place=proc.place)

    station = await _station_item(session, body, proc)
    tools = await _tool_items(session, catalog, body, proc, tool_item_id)

    scale = constants[R.QUALITY_SCALE]
    #: Limits **effective** quality: a broken anvil gives a worse result, not
    #: just breaks suddenly (`engine.wear`).
    limiters = [wear.effective(constants, item) for item in [station, *tools] if item is not None]
    ceiling = min(limiters) if limiters else scale.max

    inventory = await body_container(session, body)
    #: Which stacks feed the batch is the master's choice (D-058): by tier per
    #: input, or worst first when nothing is said.
    stock = await _stock(session, inventory, proc.inputs, tiers=_tiers_by(catalog, tiers))
    if proc.output in carrier_names(catalog):
        return await _prepare_write(
            session, constants, catalog, body, proc, units, stock, recipe_key
        )

    optimal = optimal_amounts(constants, proc, units, _base_quality(proc, stock, scale.max))
    actual = (
        {catalog.recipes.resolve(name): value * units for name, value in proportions.items()}
        if proportions
        else {name: value * units for name, value in proc.per_unit.items()}
    )
    accuracy = 1.0 if not proc.mix else ratio_accuracy(actual, optimal)

    waste = waste_share(constants, accuracy)
    #: Waste is a share of the **inputs**, so it is taken on top of the norm,
    #: not out of the output: a batch of ten nails does not give nine and a half nails.
    #:
    #: What a counted thing gives to the work, it gives whole (D-212): two and a
    #: half boards means three boards, and the half that was cut is not returned.
    #: The waste on top of that is dust until it gathers into a piece: five per
    #: cent of two ingots does not cost a third one (`goods.spent`).
    #: Rounding comes after the proportion is judged -- accuracy is about what
    #: the master laid out, not about what the saw could not halve.
    required = {
        name: goods.spent(name, value, value / (1 - waste / PERCENT), catalog=catalog)
        for name, value in actual.items()
    }

    picks = _pick(stock, required)
    minutes = batch_minutes(constants, proc, units, wear.effective(constants, station))
    #: Electricity for a machine on it (D-269): the forecast reads it here, the
    #: start draws it -- one arithmetic for both, like everything in this flow.
    juice = await power.forecast(
        session,
        constants,
        catalog,
        body,
        None if station is None else station.type_key,
        minutes / MINUTES_PER_HOUR,
    )

    forecast = Plan(
        output=proc.output,
        units=units,
        quality=forecast_quality(
            constants,
            proc,
            ceiling=ceiling,
            material=_material_quality(picks, scale.max),
            accuracy=accuracy,
        ),
        spread=spread_of(constants, accuracy),
        ceiling=ceiling,
        accuracy=accuracy,
        waste=waste,
        minutes=minutes,
        consumes=dict(required),
        energy=None if juice is None else juice[0],
        price=None if juice is None else juice[1],
    )
    return _Ready(plan=forecast, picks=tuple(picks), station=station, recipe_key=recipe_key)


async def _prepare_write(
    session: AsyncSession,
    constants: Constants,
    catalog: Catalog,
    body: Body,
    proc: Procedure,
    units: float,
    stock: dict[str, list[Item]],
    recipe_key: str | None,
) -> _Ready:
    """A carrier is **written**, not manufactured (D-209): the batch flow, but
    none of the workshop arithmetic.

    * no waste -- one recipe takes exactly one blank, a piece of memory is not
      a heap of ore;
    * no spread and no ceiling -- the carrier is the same piece of memory the
      blank was, one write poorer (`carrier.write_wear`);
    * time follows the blank's quality, not the ladder's labour
      (`carrier.write_seconds`): good memory writes in a blink, worn memory
      takes minutes;
    * a blank worn to zero is dead and is not written on. Dead blanks are
      skipped, the worst live one goes first, as everywhere in the workshop.
    """
    del catalog
    live = {
        name: [item for item in rows if item.quality is None or float(item.quality) > 0]
        for name, rows in stock.items()
    }
    required = {name: value * units for name, value in proc.per_unit.items()}
    try:
        picks = _pick(live, required)
    except NotEnough:
        dead = sum(len(rows) for rows in stock.values()) - sum(len(rows) for rows in live.values())
        if dead:
            raise Unmakeable(
                key="craft-blank-dead", live="true" if any(live.values()) else "false"
            ) from None
        raise

    scale = constants[R.QUALITY_SCALE]
    memory = _material_quality(picks, scale.max)
    quality = scale.clamp(memory - constants[R.CARRIER_WRITE_WEAR])
    minutes = write_seconds(constants, memory) * units / (SECONDS_PER_HOUR / MINUTES_PER_HOUR)
    forecast = Plan(
        output=proc.output,
        units=units,
        quality=quality,
        spread=scale.min,
        ceiling=memory,
        accuracy=1.0,
        waste=0.0,
        minutes=minutes,
        consumes=dict(required),
    )
    return _Ready(plan=forecast, picks=tuple(picks), station=None, recipe_key=recipe_key)


def write_seconds(constants: Constants, quality: float) -> float:
    """How long one write takes on memory of this quality: a straight line from
    `carrier.write_seconds.max` at quality 0 to `.min` at the top of the scale."""
    span = constants[R.CARRIER_WRITE_SECONDS]
    scale = constants[R.QUALITY_SCALE]
    share = scale.clamp(quality) / scale.max
    return span.max - (span.max - span.min) * share


async def _knows(session: AsyncSession, body: Body, key: str) -> bool:
    stmt = select(Knowledge).where(
        Knowledge.identity_id == body.identity_id,
        Knowledge.kind == KnowledgeKind.RECIPE,
        Knowledge.key == key,
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


async def _station_item(session: AsyncSession, body: Body, proc: Procedure) -> Item | None:
    """The machine stands in the node -- exactly this makes craft city-forming."""
    if proc.station is None:
        return None
    return await _pick_station(session, body, proc.station, allow_own=True)


async def _pick_station(
    session: AsyncSession, body: Body, name: str, *, allow_own: bool = False
) -> Item:
    """The best **free** machine with this name in the node (D-150).

    A machine is taken by one worker: while a batch runs it is not given to a
    second. Hence the consequence the rule exists for -- the city workshop
    stops being a free shop floor for the whole town, and the craftsman comes
    to need a machine of their own at home.

    A node disconnected for non-payment does not work at all (D-149): the meter
    is as much a condition of work as the machine itself.

    `allow_own` is for the forecast: a machine busy with the master's **own**
    work is not refused, because the new batch will not run now anyway -- it
    queues behind the running one and takes a free machine when its turn comes
    (D-209). Somebody else's work still refuses.
    """
    from src.engine import utility  # noqa: PLC0415 -- lazy: breaks the import cycle with utility

    node = await session.get(Node, body.node_id)
    if node is None:  # pragma: no cover
        raise CraftError(key="craft-body-off-node")
    if await utility.cut_off(session, node):
        raise CutOff(key="craft-cut-off", node=node.name)

    #: A frozen node stops machines the same way non-payment does (D-231): what
    #: does not burn its own fuel does not work in the cold. Asked by class, so
    #: a stove of any name answers for itself.
    from src.engine import frost  # noqa: PLC0415 -- lazy: breaks the import cycle with frost

    await frost.require_working(session, current(), node, name)

    from src.engine.world import (  # noqa: PLC0415 -- lazy: breaks the import cycle with world
        station_names,
    )

    #: The yard is read, not made: choosing a machine is the forecast's step
    #: too (`craft.plan`), and a place with nothing in it has no yard at all --
    #: which is already the answer "no such machine here".
    where = await node_yard(session, node)
    moment = datetime.now(UTC)
    standing = (
        (
            await session.execute(
                select(Item)
                .where(
                    Item.container_id == where.id,
                    Item.type_key.in_(station_names(name)),
                    #: Put up, not lying (D-278): nobody works at cargo.
                    Item.installed.is_(True),
                )
                .order_by(Item.quality.desc())
            )
        )
        .scalars()
        .all()
        if where is not None
        else []
    )
    if not standing:
        raise NoStation(key="craft-no-station", station=name)

    own: Item | None = None
    for machine in standing:
        #: Taken means taken, including by the same master: one work goes at a
        #: machine, not as many as the owner managed to order. The stamp
        #: insures against eternal occupancy: the batch could have vanished
        #: past its job, and the machine need not idle forever because of that.
        if machine.busy_body_id is not None and (
            machine.busy_until is None or machine.busy_until > moment
        ):
            if machine.busy_body_id == body.id and own is None:
                own = machine
            continue
        return machine
    if own is not None and allow_own:
        return own
    raise Busy(key="craft-station-busy", station=name, whose="own" if own is not None else "other")


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
        names = catalog.recipes.of_class(requirement) or (requirement,)
        item = (
            await session.execute(
                select(Item)
                .where(Item.container_id == inventory.id, Item.type_key.in_(names))
                .order_by(Item.quality.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if item is None:
            raise NoTool(key="craft-no-tool", tool=requirement)
        found.append(item)

    if tool_item_id is not None and all(item.id != tool_item_id for item in found):
        chosen = await session.get(Item, tool_item_id)
        if chosen is None or chosen.container_id != inventory.id:
            #: What `tool` is, not just that this one is wrong: it names a
            #: thing in the worker's own hands, while the machine standing in
            #: the node is taken by the engine itself. An AI citizen (D-224)
            #: read the id of the smelter off the place and sent it here
            #: twenty-four times in seven minutes.
            raise NoTool(key="craft-tool-not-in-hands")
        found.append(chosen)
    return found


async def _stock(
    session: AsyncSession,
    container: Container,
    names: Iterable[str],
    *,
    tiers: dict[str, str] | None = None,
) -> dict[str, list[Item]]:
    """What lies for each input, worst first -- or only the chosen quality tier.

    The order is not accidental: the worse goes into the work, and the pure raw
    material stays for the batch it was mined for. `tiers` is the master's
    word on that: "this input -- from the good stacks only". Then nothing else
    is touched, and too little of the chosen tier is a refusal, not a silent
    fallback to worse -- the choice was made for a reason (D-058).
    """
    from src.engine import (  # noqa: PLC0415 -- lazy: breaks the import cycle craft -> liquid -> station -> craft
        liquid,
        market,
    )

    constants = current()
    wanted = {name: tier for name, tier in (tiers or {}).items() if tier}
    #: The container and the vessels in it (D-230): water for the dough is in
    #: the canister, and the recipe need not know that.
    within = await liquid.reach(session, current_catalog(), container)
    out: dict[str, list[Item]] = {}
    for name in names:
        rows = (
            (
                await session.execute(
                    select(Item)
                    .where(Item.container_id.in_(within), Item.type_key == name)
                    .order_by(Item.quality.asc().nulls_first(), Item.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        tier = wanted.get(name)
        if tier is not None:
            rows = [
                item
                for item in rows
                if market.tier_of(constants, None if item.quality is None else float(item.quality))
                == tier
            ]
        out[name] = list(rows)
    return out


def _tiers_by(catalog: Catalog, tiers: dict[str, str] | None) -> dict[str, str]:
    """Chosen tiers keyed by canonical input name: the client speaks in synonyms too."""
    if not tiers:
        return {}
    return {catalog.recipes.resolve(name): tier for name, tier in tiers.items() if tier}


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
            raise NotEnough(key="craft-not-enough", goods=name, short=amount_float(left))
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
        cause="craft_batch",
    )


def _pieces(catalog: Catalog, output: str, units: float) -> list[float]:
    """What the batch turns into: one stack of raw material or so many products.

    Raw material stacks, products do not (04-items), and every product has its
    own spread roll -- because each has its own mark and quality (D-058).
    """
    if goods.stackable(output, catalog):
        return [units]
    return [1.0] * int(units)


def _num(value: float) -> Decimal:
    """A number on the 0..100 scale in the form the database stores it."""
    return Decimal(str(value))


def _seconds(minutes: float) -> Decimal:
    """Work left, as the batch stores it: seconds of the master's presence."""
    return Decimal(str(minutes * SECONDS_PER_HOUR / MINUTES_PER_HOUR))
