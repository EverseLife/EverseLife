# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Nurlan Urazkulov

"""The batch's end: the journal job that lands the make, the repair or the
recycling -- and where the yield reaches, vessels included.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import Catalog, ConstantError, Constants, current, current_catalog
from src.constants import registry as R
from src.engine import events, goods, liquid, vent
from src.engine import world as world_engine
from src.engine.craft._base import (
    CraftError,
)
from src.engine.craft._internal import (
    _num,
    _pieces,
    _release,
)
from src.engine.craft.batch.work import _target
from src.engine.craft.method_of_making import procedure
from src.engine.craft.queue import wake, wake_node
from src.engine.craft.wearing import _hours_run, _wear_station, _wear_tools
from src.engine.jobs import handler
from src.engine.ship import lines
from src.engine.world import BIOPRINTER, body_container, node_container, station_names
from src.models.craft import BatchKind, BatchState, CraftBatch
from src.models.event import EventKind
from src.models.identity import Body, BodyState
from src.models.inventory import Container, Item
from src.models.job import Job, JobKind
from src.models.world import Node
from src.units import (
    PERCENT,
    amount,
    amount_float,
)


@handler(JobKind.CRAFT_BATCH)
async def finish(session: AsyncSession, job: Job) -> None:
    """Work is done: products, a repaired thing, or a handful of materials."""
    batch = await session.get(CraftBatch, uuid.UUID(job.payload["batch"]))
    if batch is None:  # pragma: no cover -- a job without a batch is a bug
        raise CraftError(key="craft-job-without-batch", job=str(job.id))

    #: The body's row is taken **before** the batch's state is judged, and the
    #: state is read again under it. A freeze stops the work while holding that
    #: row (D-209, `_alive`), so a state read ahead of the lock is a state from
    #: before the freeze: the batch would be landed after the master walked
    #: away, and the hour its tools swung would be billed a second time (D-309).
    body = await session.get(Body, batch.body_id, with_for_update=True)
    node = await session.get(Node, batch.node_id)
    if body is None or node is None:  # pragma: no cover
        raise CraftError(key="craft-batch-dangling", batch=str(batch.id))
    await session.refresh(batch)

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

    #: The master stands at the machine -- takes it themselves; left or died --
    #: the output stays at the machine. Matter does not vanish with whoever ordered it.
    at_bench = body.state is BodyState.ALIVE and body.node_id == batch.node_id
    #: A station built in place never enters the hands (D-268): it stands on
    #: the floor of the place it was made in, master present or not.
    if catalog.recipes.built(batch.output):
        at_bench = False
    where = await body_container(session, body) if at_bench else await node_container(session, node)

    if batch.kind is BatchKind.REPAIR:
        made = await _finish_repair(session, constants, batch)
    elif batch.kind is BatchKind.RECYCLE:
        made = await _finish_recycle(session, constants, catalog, batch, where)
    else:
        made = await _finish_make(session, constants, catalog, batch, body, where, job.run_at)

    await _wear_station(session, constants, batch)
    #: And the tools in the hands, by the hours this run took (D-309): the
    #: machine pays per batch, what is carried pays per hour.
    await _wear_tools(session, constants, batch, hours=_hours_run(batch, job.run_at))
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

    #: A station built in place stands the moment it is made, and this is the
    #: last of the three doors it can stand in a city by (D-312) -- the other
    #: two are `station.place` and the start of the batch. The start is not
    #: enough on its own: twenty hours pass in between, and a second batch may
    #: be queued behind the first (D-209), a frozen one may thaw, or somebody
    #: may put a printer up by hand meanwhile. Refusing here would burn work
    #: already paid for, so the machine is simply **laid down** instead of
    #: stood up: matter is not lost, the rule holds, and whoever may put it up
    #: does so through the door that asks.
    stands = catalog.recipes.built(batch.output)
    if stands and batch.output in station_names(BIOPRINTER):
        from src.engine import station  # noqa: PLC0415 -- lazy: breaks the cycle with station

        node = await session.get(Node, batch.node_id)
        if node is not None:
            try:
                await station.require_printer_room(session, body, node, lock=True)
            except station.StationError:
                stands = False

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

    #: The air aboard pours through the hull's lines (D-340): the machine the
    #: batch ran at, read again now -- it may have been taken down meanwhile,
    #: and then the yield lands at the bench like any other.
    station_item = (
        None if batch.station_item_id is None else await session.get(Item, batch.station_item_id)
    )
    plumbed = await lines.plumbing_of(session, constants, catalog, station_item, batch.output)

    made: list[float] = []
    #: What arrived in the hands, for the carry rule below (D-265): judged
    #: once for the whole yield, not piece by piece.
    arrived: list[Item] = []
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
            #: A station built in place stands where it was made (D-268); a
            #: portable one left at the bench lies there as cargo until somebody
            #: puts it up (D-278). A printer the city may not have lies too.
            installed=stands,
        )
        session.add(fresh)
        #: And only the first of them stands: a batch of two printers passed
        #: the door once and would otherwise stand both (D-312). The start
        #: refuses such a batch inside a city outright; this is the same rule
        #: said where the machines actually go up.
        if stands and batch.output in station_names(BIOPRINTER):
            stands = False
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
        if plumbed is not None and liquid.is_liquid(catalog, batch.output):
            #: Into the vessels on the outlet, in line order. The start made
            #: sure they could take it all; what somebody filled them with
            #: during the hours is a spill, said as one.
            spilled = await liquid.fill_or_drop(
                session, catalog, fresh, plumbed.outlets.get(batch.output, [])
            )
        else:
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
        elif len(within) > 1 and not liquid.is_liquid(catalog, batch.output):
            arrived.append(fresh)
    shed = catalog.recipes.byproduct_of(batch.output)
    if shed:
        await _shed(session, catalog, batch, body, where, plumbed, shed, moment)
    if arrived:
        #: Paid into the master's hands past the carry limit, the yield falls
        #: underfoot (D-265): a station is not carried off because it was
        #: made rather than picked up. Liquids are in vessels already.
        from src.engine import overload  # noqa: PLC0415 -- lazy: cycle via storage, estate

        await overload.settle_load(session, constants, catalog, body, arrived)
    return made


async def _shed(
    session: AsyncSession,
    catalog: Catalog,
    batch: CraftBatch,
    body: Body,
    where: Container,
    plumbed: lines.Plumbing | None,
    byproduct: dict[str, float],
    moment: datetime,
) -> None:
    """The batch's byproduct (D-340): the hydrogen of electrolysis.

    A liquid byproduct is a vent gas (the vault build holds it to that), and
    it goes where `engine.vent` sends it: into the vessels on its vent line
    aboard, and what finds no room out where there is no air outside or into
    the node's flare stack where there is -- without a word, because nothing
    anybody kept was lost.

    The start refused a batch whose gas would have had nowhere to go, but the
    place can change during the hours: a hull that set down under a sky with
    air, a vent tank somebody filled meanwhile. Then what finds no place
    **spills with an event**, as the oxygen does when its room was taken --
    an accident said aloud, never a release planned into the air. The flare
    itself cannot vanish meanwhile: a station built in place is never taken
    down (D-268). A byproduct that is not a liquid lands with the yield.
    """
    units = amount_float(batch.units)
    node = await session.get(Node, batch.node_id)
    for name, per in byproduct.items():
        made = amount(per * units)
        if made <= 0:
            continue
        solid = not liquid.is_liquid(catalog, name)
        vessels = [] if solid or plumbed is None else plumbed.vents.get(name, [])
        left = amount_float(made)
        if solid or vessels:
            extra = Item(
                container_id=where.id,
                type_key=name,
                amount=made,
                quality=batch.quality,
                maker_identity_id=body.identity_id,
                made_at=moment,
                made_node_id=batch.node_id,
            )
            session.add(extra)
            await session.flush()
            if solid:
                await world_engine.stack_up(session, extra)
                continue
            left = await liquid.fill_or_drop(session, catalog, extra, vessels)
        if left > 0 and await vent.sink(session, node) is None:
            await events.record(
                session,
                EventKind.STORAGE_SPILLED,
                actor_identity_id=body.identity_id,
                node_id=batch.node_id,
                type_key=name,
                amount=left,
            )


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
        cause="recycled",
    )
    await session.delete(item)
    await session.flush()
    return returned
